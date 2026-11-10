from __future__ import annotations

import json
import time
from typing import Any, Protocol
from uuid import uuid4

import requests
from rdflib.exceptions import ParserError

from ..domain.models import (
    AnalyzeRequest,
    AnalyzeResponse,
    EntityMention,
    WikidataCandidateGroup,
    WikidataEntity,
    WikidataRelationship,
)
from ..domain.structured_rdf import model_response_to_graph, serialize_graph, validate_graph
from ..infrastructure.request_logger import RequestLogger
from .disambiguation import (
    candidate_group_evidence,
    disambiguation_evidence,
    validate_selections,
)
from .mentions import mention_from_item, realign_mentions, supplement_mentions
from .rdf_enrichment import (
    add_text_cooccurrence_relations,
    ensure_resource_labels,
    materialize_relationships,
    remove_unresolved_resources,
)
from .response_parsing import parse_entity_response


class LLMClient(Protocol):
    def generate(
        self,
        system_prompt: str,
        prompt: str,
        stage: str,
        timeout_seconds: float | None = None,
    ) -> str: ...

    def health_check(self) -> dict[str, Any]: ...


class WikidataClient(Protocol):
    def search_candidate_groups(
        self,
        mentions: list[EntityMention],
        limit: int = 3,
        context: str | None = None,
    ) -> list[WikidataCandidateGroup]: ...

    def find_relationships(self, entities: list[WikidataEntity]) -> list[WikidataRelationship]: ...

    def health(self) -> dict[str, Any]: ...


class PromptLoader(Protocol):
    def load_prompt(self, prompt_name: str) -> str: ...


class RDFValidationError(RuntimeError):
    def __init__(self, message: str, attempts: int, last_error: str | None = None) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.last_error = last_error


class CandidateDisambiguationError(RuntimeError):
    def __init__(self, message: str, attempts: int, last_error: str | None = None) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.last_error = last_error


class HybridAgentService:
    def __init__(
        self,
        llm: LLMClient,
        wikidata: WikidataClient,
        prompt_repository: PromptLoader,
        system_prompt_name: str = "system/agent.txt",
        entity_prompt_name: str = "prompts/entity-extraction.txt",
        disambiguation_prompt_name: str = "prompts/candidate-disambiguation.txt",
        rdf_prompt_name: str = "prompts/rdf-build.txt",
        request_logger: RequestLogger | None = None,
        candidate_limit: int = 3,
        analyze_timeout_seconds: float = 540.0,
        mention_limit: int = 10,
    ) -> None:
        self.llm = llm
        self.wikidata = wikidata
        self.prompt_repository = prompt_repository
        self.system_prompt_name = system_prompt_name
        self.entity_prompt_name = entity_prompt_name
        self.disambiguation_prompt_name = disambiguation_prompt_name
        self.rdf_prompt_name = rdf_prompt_name
        self.request_logger = request_logger
        self.candidate_limit = max(1, int(candidate_limit))
        self.analyze_timeout_seconds = max(1.0, float(analyze_timeout_seconds))
        self.mention_limit = max(1, int(mention_limit))

    def analyze(self, request: AnalyzeRequest) -> AnalyzeResponse:
        key = request.idempotence_key or str(uuid4())
        timeout_seconds = request.max_processing_seconds or self.analyze_timeout_seconds
        deadline = time.monotonic() + timeout_seconds
        self._log(key, "analyze_started", {"text": request.text})
        self._ensure_not_timed_out(deadline)

        mentions, extraction_raw = self._extract_entities(request.text, key, deadline)
        self._ensure_not_timed_out(deadline)
        candidate_groups = self.wikidata.search_candidate_groups(
            mentions,
            limit=self.candidate_limit,
            context=request.text,
        )
        candidate_payload = [candidate_group_evidence(group) for group in candidate_groups]
        self._log(key, "wikidata_candidates", {"candidate_groups": candidate_payload})
        self._ensure_not_timed_out(deadline)
        entities, disambiguation_raw = self._disambiguate_candidates(
            request.text,
            candidate_groups,
            key,
            deadline,
        )
        self._log(key, "wikidata_entities", {"entities": [entity.to_dict() for entity in entities]})

        self._ensure_not_timed_out(deadline)
        relationships = self.wikidata.find_relationships(entities)
        self._log(key, "wikidata_relationships", {"relationships": [rel.to_dict() for rel in relationships]})

        self._ensure_not_timed_out(deadline)
        rdf = self._build_valid_rdf(
            request.text,
            entities,
            relationships,
            key,
            deadline,
        )
        self._log(key, "rdf_built", {"rdf": rdf})

        llm_outputs = {
            "entity_extraction": extraction_raw,
            "candidate_disambiguation": disambiguation_raw,
        }
        response = AnalyzeResponse(
            text=request.text,
            entities=entities,
            relationships=relationships,
            rdf=rdf,
            llm=llm_outputs,
            ned={
                "candidate_groups": candidate_payload,
            },
        )
        self._log(
            key,
            "analyze_completed",
            {
                "rdf": rdf,
                "entity_count": len(entities),
                "relationship_count": len(relationships),
            },
        )
        return response

    def health(self) -> dict[str, Any]:
        return {
            "llm": self.llm.health_check(),
            "wikidata_mcp": self.wikidata.health(),
        }

    def _disambiguate_candidates(
        self,
        text: str,
        groups: list[WikidataCandidateGroup],
        key: str,
        deadline: float | None = None,
    ) -> tuple[list[WikidataEntity], str]:
        prompt_template = self.prompt_repository.load_prompt(self.disambiguation_prompt_name)
        expected_indices = {index for index, group in enumerate(groups) if group.candidates}
        selections: dict[int, str] = {}
        last_error: str | None = None
        raw = ""
        attempts = 3
        completed_attempts = 0

        for attempt in range(1, attempts + 1):
            pending_indices = sorted(expected_indices - set(selections))
            if not pending_indices:
                break
            completed_attempts = attempt
            payload = {
                "text": text,
                "candidate_groups": [
                    {
                        "mention_index": index,
                        **disambiguation_evidence(groups[index]),
                    }
                    for index in pending_indices
                ],
            }
            prompt = prompt_template.replace("${PAYLOAD}", json.dumps(payload, ensure_ascii=False, indent=2))
            self._log(
                key,
                "llm_disambiguation_request",
                {"attempt": attempt, "pending_indices": pending_indices, "prompt": prompt},
            )
            raw = self.llm.generate(
                system_prompt=self.prompt_repository.load_prompt(self.system_prompt_name),
                prompt=prompt,
                stage="candidate_disambiguation",
                timeout_seconds=self._remaining_timeout(deadline),
            )
            self._log(
                key,
                "llm_disambiguation_response",
                {"attempt": attempt, "response": raw},
            )

            try:
                attempt_selections = validate_selections(
                    raw,
                    groups,
                    set(pending_indices),
                )
            except (json.JSONDecodeError, ValueError) as exc:
                last_error = str(exc)
                self._log(
                    key,
                    "llm_disambiguation_validation_failed",
                    {"attempt": attempt, "error": last_error, "pending_indices": pending_indices},
                )
                continue

            selections.update(attempt_selections)
            remaining = sorted(expected_indices - set(selections))
            if remaining:
                last_error = "Missing selections for mention indices: " + ", ".join(map(str, remaining))
                self._log(
                    key,
                    "llm_disambiguation_validation_failed",
                    {"attempt": attempt, "error": last_error, "pending_indices": remaining},
                )

        missing_indices = sorted(expected_indices - set(selections))
        if missing_indices:
            raise CandidateDisambiguationError(
                "Candidate disambiguation failed.",
                attempts=attempts,
                last_error=last_error
                or ("Missing selections for mention indices: " + ", ".join(map(str, missing_indices))),
            )

        self._log(
            key,
            "llm_disambiguation_validated",
            {"attempts": completed_attempts, "selection_count": len(selections)},
        )

        entities: list[WikidataEntity] = []
        for index, group in enumerate(groups):
            if not group.candidates:
                entities.append(
                    WikidataEntity(
                        mention=group.mention,
                        id=None,
                        iri=None,
                        label=group.mention.surface,
                    )
                )
                continue
            selected_id = selections.get(index)
            selected = next(
                (candidate for candidate in group.candidates if candidate.id == selected_id),
                None,
            )
            if selected is None:  # Guarded by strict response validation above.
                raise CandidateDisambiguationError(
                    "Candidate disambiguation failed.",
                    attempts=attempts,
                    last_error=f"No valid selection for mention index {index}.",
                )
            entities.append(selected)
        return entities, raw

    def _extract_entities(self, text: str, key: str, deadline: float | None = None) -> tuple[list[EntityMention], str]:
        system_prompt = self.prompt_repository.load_prompt(self.system_prompt_name)
        prompt_template = self.prompt_repository.load_prompt(self.entity_prompt_name)
        prompt = prompt_template.replace("${TEXT}", text)
        self._log(key, "llm_entity_request", {"prompt": prompt})
        timeout_seconds = self._remaining_timeout(deadline)
        raw = self.llm.generate(
            system_prompt=system_prompt,
            prompt=prompt,
            stage="entity_extraction",
            timeout_seconds=timeout_seconds,
        )
        self._log(key, "llm_entity_response", {"response": raw})

        payload = parse_entity_response(raw)
        items = payload["entities"]
        mentions = [mention_from_item(item) for item in items if isinstance(item, dict)]
        mentions = [mention for mention in mentions if mention.surface]
        if not mentions:
            raise ValueError("The LLM returned no usable entity mentions.")
        mentions = realign_mentions(text, mentions)
        if not mentions:
            raise ValueError("The LLM entity mentions do not occur in the input text.")
        mentions = supplement_mentions(text, mentions, self.mention_limit)
        return mentions, raw

    def _build_valid_rdf(
        self,
        text: str,
        entities: list[WikidataEntity],
        relationships: list[WikidataRelationship],
        key: str,
        deadline: float | None = None,
    ) -> str:
        attempts = 3
        last_error = None

        original_prompt = self._build_rdf_prompt(text, entities, relationships)
        prompt = original_prompt
        for attempt in range(1, attempts + 1):
            model_response = self._build_rdf(prompt, key, deadline)
            try:
                graph = model_response_to_graph(model_response)
                remove_unresolved_resources(graph, entities, relationships)
                materialize_relationships(graph, relationships)
                add_text_cooccurrence_relations(graph, text, entities)
                ensure_resource_labels(graph, entities, relationships)
                validate_graph(graph)
                rdf = serialize_graph(graph)
                self._log(
                    key,
                    "rdf_validated",
                    {"attempt": attempt, "validation_method": "strict"},
                )
                return rdf
            except (ValueError, SyntaxError, ParserError) as exc:
                last_error = str(exc)
                self._log(
                    key,
                    "rdf_validation_failed",
                    {"attempt": attempt, "error": last_error},
                )

            if attempt < attempts:
                prompt = _build_retry_prompt(
                    original_prompt,
                    model_response,
                    last_error or "Invalid RDF response.",
                )

        raise RDFValidationError(
            "RDF parsing failed.",
            attempts=attempts,
            last_error=last_error,
        )

    def _build_rdf_prompt(
        self,
        text: str,
        entities: list[WikidataEntity],
        relationships: list[WikidataRelationship],
    ) -> str:
        prompt_template = self.prompt_repository.load_prompt(self.rdf_prompt_name)
        payload = {
            "text": text,
            "source_attribution": "Source: Wikidata",
            "entities": [_entity_evidence(entity) for entity in entities],
            "relationships": [relationship.to_dict() for relationship in relationships],
        }
        return prompt_template.replace("${PAYLOAD}", json.dumps(payload, ensure_ascii=False, indent=2))

    def _build_rdf(self, prompt: str, key: str, deadline: float | None = None) -> str:
        system_prompt = self.prompt_repository.load_prompt(self.system_prompt_name)
        self._log(key, "llm_rdf_request", {"prompt": prompt})
        timeout_seconds = self._remaining_timeout(deadline)
        rdf = self.llm.generate(
            system_prompt=system_prompt,
            prompt=prompt,
            stage="rdf_build",
            timeout_seconds=timeout_seconds,
        ).strip()
        self._log(key, "llm_rdf_response", {"response": rdf})
        return rdf

    def _remaining_timeout(self, deadline: float | None) -> float | None:
        if deadline is None:
            return None
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise requests.Timeout("Analyze request exceeded configured max processing time.")
        return remaining

    def _ensure_not_timed_out(self, deadline: float | None) -> None:
        self._remaining_timeout(deadline)

    def _log(self, key: str, event: str, payload: dict[str, Any]) -> None:
        if self.request_logger:
            self.request_logger.log(idempotence_key=key, event=event, payload=payload)


def _entity_evidence(entity: WikidataEntity) -> dict[str, Any]:
    """Keep entity identity and mention metadata in the RDF prompt."""
    payload = entity.to_dict()
    payload.pop("statements")
    return payload


def _build_retry_prompt(original_prompt: str, invalid_response: str, parser_error: str) -> str:
    error = parser_error[:1200]
    previous = invalid_response[:6000]
    return (
        f"{original_prompt}\n\n"
        "The previous RDF response was empty or invalid.\n"
        f"Validation error:\n{error}\n\n"
        'Return the complete corrected JSON object with exactly one key, "rdf", containing '
        "a non-empty valid Turtle string with the required rdfs, wd and kg prefix declarations. "
        "Return JSON only, without Markdown, comments, or explanations.\n"
        f"Previous invalid response:\n{previous}"
    )
