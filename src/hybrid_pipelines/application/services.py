from __future__ import annotations

import json
import re
import time
from typing import Any, Protocol, cast
from uuid import uuid4

import requests
from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF, RDFS

from ..domain.models import (
    AnalyzeRequest,
    AnalyzeResponse,
    EntityMention,
    WikidataCandidateGroup,
    WikidataEntity,
    WikidataPath,
    WikidataRelationship,
)
from ..infrastructure.request_logger import RequestLogger


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
    def resolve_entities(
        self,
        mentions: list[EntityMention],
        limit: int = 3,
        context: str | None = None,
    ) -> list[WikidataEntity]: ...

    def find_relationships(self, entities: list[WikidataEntity]) -> list[WikidataRelationship]: ...

    def health(self) -> dict[str, Any]: ...


class PromptLoader(Protocol):
    def load_prompt(self, prompt_name: str) -> str: ...


class RDFValidationError(RuntimeError):
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
        max_path_hops: int = 2,
        hub_degree_threshold: int = 25,
        path_expansion_limit: int = 30,
        path_limit: int = 24,
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
        self.max_path_hops = max(1, min(int(max_path_hops), 2))
        self.hub_degree_threshold = max(1, int(hub_degree_threshold))
        self.path_expansion_limit = max(0, int(path_expansion_limit))
        self.path_limit = max(1, int(path_limit))

    def analyze(self, request: AnalyzeRequest) -> AnalyzeResponse:
        key = request.idempotence_key or str(uuid4())
        timeout_seconds = _coerce_timeout_seconds(
            getattr(request, "max_processing_seconds", None),
            self.analyze_timeout_seconds,
        )
        deadline = time.monotonic() + timeout_seconds
        self._log(key, "analyze_started", {"text": request.text})
        self._ensure_not_timed_out(deadline)

        mentions, extraction_raw = self._extract_entities(request.text, key, deadline)
        self._ensure_not_timed_out(deadline)
        candidate_search = getattr(self.wikidata, "search_candidate_groups", None)
        path_search = getattr(self.wikidata, "find_candidate_paths", None)
        disambiguation_raw: str | None = None
        candidate_groups: list[WikidataCandidateGroup] = []
        paths: list[WikidataPath] = []
        if callable(candidate_search) and callable(path_search):
            candidate_groups = cast(Any, candidate_search)(
                mentions,
                limit=self.candidate_limit,
                context=request.text,
            )
            self._log(
                key,
                "wikidata_candidates",
                {"candidate_groups": [_compact_candidate_group(group) for group in candidate_groups]},
            )
            self._ensure_not_timed_out(deadline)
            paths = cast(Any, path_search)(
                candidate_groups,
                max_hops=self.max_path_hops,
                hub_degree_threshold=self.hub_degree_threshold,
                expansion_limit=self.path_expansion_limit,
                path_limit=self.path_limit,
            )
            self._log(key, "wikidata_candidate_paths", {"paths": [path.to_dict() for path in paths]})
            self._ensure_not_timed_out(deadline)
            entities, disambiguation_raw = self._disambiguate_candidates(
                request.text,
                candidate_groups,
                paths,
                key,
                deadline,
            )
        else:
            entities = self.wikidata.resolve_entities(mentions, limit=self.candidate_limit, context=request.text)
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
            getattr(request, "max_rdf_attempts", 3),
            deadline,
        )
        self._log(key, "rdf_built", {"rdf": rdf})

        llm_outputs = {"entity_extraction": extraction_raw}
        if disambiguation_raw is not None:
            llm_outputs["candidate_disambiguation"] = disambiguation_raw
        response = AnalyzeResponse(
            text=request.text,
            entities=entities,
            relationships=relationships,
            rdf=rdf,
            llm=llm_outputs,
            ned={
                "candidate_groups": [_compact_candidate_group(group) for group in candidate_groups],
                "paths": [path.to_dict() for path in paths],
                "path_summary": _summarize_paths(paths),
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
        paths: list[WikidataPath],
        key: str,
        deadline: float | None = None,
    ) -> tuple[list[WikidataEntity], str]:
        prompt_template = self.prompt_repository.load_prompt(self.disambiguation_prompt_name)
        payload = {
            "text": text,
            "candidate_groups": [
                {
                    "mention_index": index,
                    **_compact_candidate_group(group),
                }
                for index, group in enumerate(groups)
            ],
            "graph_context": _summarize_paths(paths),
        }
        prompt = prompt_template.replace("${PAYLOAD}", json.dumps(payload, ensure_ascii=False, indent=2))
        self._log(key, "llm_disambiguation_request", {"prompt": prompt})
        raw = self.llm.generate(
            system_prompt=self.prompt_repository.load_prompt(self.system_prompt_name),
            prompt=prompt,
            stage="candidate_disambiguation",
            timeout_seconds=self._remaining_timeout(deadline),
        )
        self._log(key, "llm_disambiguation_response", {"response": raw})
        result = json.loads(raw)
        if not isinstance(result, dict) or not isinstance(result.get("selections"), list):
            raise ValueError("The LLM disambiguation response must contain a selections array.")

        selections: dict[int, str] = {}
        for selection in result["selections"]:
            if not isinstance(selection, dict):
                continue
            mention_index = _optional_int(selection.get("mention_index"))
            selected_id = selection.get("selected_id")
            if mention_index is None or not isinstance(selected_id, str):
                continue
            if mention_index in selections:
                raise ValueError(f"The LLM returned duplicate selections for mention {mention_index}.")
            selections[mention_index] = selected_id

        expected_indices = {index for index, group in enumerate(groups) if group.candidates}
        if set(selections) != expected_indices:
            raise ValueError(
                "The LLM disambiguation response must select every non-empty candidate group exactly once."
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
            if selected is None:
                allowed = ", ".join(candidate.id or "" for candidate in group.candidates)
                raise ValueError(
                    f"The LLM selected an invalid Wikidata ID for mention {index}; allowed IDs: {allowed}."
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

        payload = _json_from_text(raw)
        items = payload["entities"]
        mentions = [_mention_from_item(item) for item in items if isinstance(item, dict)]
        mentions = [mention for mention in mentions if mention.surface]
        if not mentions:
            raise ValueError("The LLM returned no usable entity mentions.")
        mentions = _realign_mentions(text, mentions)
        if not mentions:
            raise ValueError("The LLM entity mentions do not occur in the input text.")
        mentions = _supplement_mentions(text, mentions, self.mention_limit)
        return _dedupe_mentions(mentions)[: self.mention_limit], raw

    def _build_valid_rdf(
        self,
        text: str,
        entities: list[WikidataEntity],
        relationships: list[WikidataRelationship],
        key: str,
        max_attempts: int,
        deadline: float | None = None,
    ) -> str:
        attempts = max(1, min(int(max_attempts or 3), 3))
        last_error = None

        original_prompt = self._build_rdf_prompt(text, entities, relationships)
        prompt = original_prompt
        for attempt in range(1, attempts + 1):
            rdf = self._build_rdf(prompt, key, deadline)
            try:
                _parse_rdf(rdf)
                rdf = _ensure_entity_labels(
                    rdf,
                    text,
                    entities,
                    relationships,
                )
                _parse_rdf(rdf)
                self._log(
                    key,
                    "rdf_validated",
                    {"attempt": attempt, "validation_method": "strict"},
                )
                return rdf
            except Exception as exc:  # rdflib raises parser-specific exception classes.
                last_error = str(exc)
                self._log(
                    key,
                    "rdf_validation_failed",
                    {"attempt": attempt, "error": last_error},
                )

            if attempt < attempts:
                prompt = _build_retry_prompt(
                    original_prompt,
                    rdf,
                    last_error or "Invalid Turtle RDF.",
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
            "entities": [_compact_entity(entity) for entity in entities],
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
        return _strip_code_fence(rdf)

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


def _json_from_text(text: str) -> dict[str, Any]:
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("The LLM entity response must be a JSON object.")
    if not isinstance(payload.get("entities"), list):
        raise ValueError("The LLM entity response must contain an entities array.")
    return payload


def _mention_from_item(item: dict[str, Any]) -> EntityMention:
    return EntityMention(
        surface=str(item.get("surface") or "").strip(),
        start=_optional_int(item.get("start")),
        end=_optional_int(item.get("end")),
        entity_type=str(item.get("entity_type") or item.get("label") or "Entity"),
        confidence=_optional_float(item.get("confidence")),
    )


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


_DESCRIPTOR_HEADS = (
    "brand|car|vehicle|boat|ship|company|corporation|label|name|"
    "musician|singer-songwriter|language|service|services|outlet|"
    "fashion|line|lines|range"
)


def _supplement_mentions(
    text: str,
    mentions: list[EntityMention],
    limit: int,
) -> list[EntityMention]:
    """Fill omitted relation/class concepts without inventing text spans."""
    supplemental: list[EntityMention] = []
    descriptor_pattern = re.compile(
        rf"\b(?P<modifier>[A-Za-z][A-Za-z'-]*)\s+"
        rf"(?P<head>{_DESCRIPTOR_HEADS})\b",
        flags=re.IGNORECASE,
    )
    head_priority = {
        "name": 0,
        "label": 0,
        "car": 1,
        "boat": 1,
        "ship": 1,
    }
    descriptor_matches = sorted(
        descriptor_pattern.finditer(text),
        key=lambda match: (
            head_priority.get(match.group("head").casefold(), 2),
            match.start(),
        ),
    )
    ignored_modifiers = {
        "a", "an", "and", "as", "in", "its", "of", "on", "or", "the", "to",
    }
    for match in descriptor_matches:
        modifier = match.group("modifier")
        if len(modifier) < 2 or modifier.casefold() in ignored_modifiers:
            continue
        supplemental.append(
            EntityMention(
                surface=modifier,
                start=match.start("modifier"),
                end=match.end("modifier"),
                entity_type="Concept",
                confidence=0.5,
            )
        )
        supplemental.append(
            EntityMention(
                surface=match.group(0),
                start=match.start(),
                end=match.end(),
                entity_type="Class",
                confidence=0.5,
            )
        )

    for match in re.finditer(
        r"\b(?:type|class|model|series|no\.?)\s+(?P<value>\d+[A-Za-z]?)\b",
        text,
        flags=re.IGNORECASE,
    ):
        supplemental.append(
            EntityMention(
                surface=match.group("value"),
                start=match.start("value"),
                end=match.end("value"),
                entity_type="Concept",
                confidence=0.5,
            )
        )

    return _dedupe_mentions([*mentions, *supplemental])[: max(1, int(limit))]


def _dedupe_mentions(mentions: list[EntityMention]) -> list[EntityMention]:
    seen: set[tuple[str, int | None, int | None]] = set()
    deduped: list[EntityMention] = []
    for mention in mentions:
        key = (mention.surface.casefold(), mention.start, mention.end)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(mention)
    return deduped


def _realign_mentions(text: str, mentions: list[EntityMention]) -> list[EntityMention]:
    """Realign model-provided offsets to exact, successive text occurrences."""
    occupied_spans: list[tuple[int, int]] = []
    cursor = 0
    aligned: list[EntityMention] = []
    for mention in mentions:
        matches = list(re.finditer(re.escape(mention.surface), text, flags=re.IGNORECASE))
        available = [
            match
            for match in matches
            if not any(
                match.start() < occupied_end and match.end() > occupied_start
                for occupied_start, occupied_end in occupied_spans
            )
        ]
        if not available:
            continue
        match = next((item for item in available if item.start() >= cursor), available[0])
        occupied_spans.append((match.start(), match.end()))
        cursor = max(cursor, match.end())
        aligned.append(
            EntityMention(
                surface=mention.surface,
                start=match.start(),
                end=match.end(),
                entity_type=mention.entity_type,
                confidence=mention.confidence,
            )
        )
    return aligned


def _compact_entity(entity: WikidataEntity, statement_limit: int = 8) -> dict[str, Any]:
    payload = entity.to_dict()
    statements = payload.get("statements") or []
    priority = {"P31", "P279", "P361", "P527", "P1889", "P1582", "P171", "P105"}
    payload["statements"] = sorted(
        statements,
        key=lambda item: 0 if item.get("property_id") in priority else 1,
    )[:statement_limit]
    return payload


def _compact_candidate_group(group: WikidataCandidateGroup) -> dict[str, Any]:
    return {
        "mention": group.mention.to_dict(),
        "candidates": [
            {
                "id": candidate.id,
                "label": candidate.label,
                "description": candidate.description,
                "score": candidate.score,
                "type_statements": [
                    statement
                    for statement in candidate.statements
                    if str(statement.get("property_id") or statement.get("property") or "")
                    in {"P31", "P279"}
                ][:4],
            }
            for candidate in group.candidates
        ],
    }


def _summarize_paths(paths: list[WikidataPath], character_limit: int = 6000) -> str:
    sentences: list[str] = []
    seen: set[str] = set()
    used = 0
    for path in sorted(paths, key=lambda item: item.hops):
        text = path.to_text().strip()
        if not text or text.casefold() in seen:
            continue
        addition = len(text) + (1 if sentences else 0)
        if used + addition > max(1, int(character_limit)):
            break
        seen.add(text.casefold())
        sentences.append(text)
        used += addition
    return " ".join(sentences)


def _strip_code_fence(text: str) -> str:
    cleaned = text.strip()
    fenced = re.search(r"```(?:turtle|ttl|rdf)?\s*(.*?)\s*```", cleaned, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        cleaned = fenced.group(1).strip()
    prefix_index = cleaned.find("@prefix")
    if prefix_index > 0:
        cleaned = cleaned[prefix_index:].strip()
    note_match = re.search(r"\n(?:Note|Please note|Explanation|The above)\b", cleaned, flags=re.IGNORECASE)
    if note_match:
        cleaned = cleaned[: note_match.start()].strip()
    return cleaned.strip()


def _parse_rdf(rdf_text: str) -> None:
    if not rdf_text.strip():
        raise ValueError("Empty RDF response.")
    graph = Graph().parse(data=rdf_text, format="turtle")
    # Prefix declarations alone parse successfully but produce an empty graph.
    if len(graph) == 0:
        raise ValueError("RDF response contains no triples.")


def _ensure_entity_labels(
    rdf_text: str,
    text: str,
    entities: list[WikidataEntity],
    relationships: list[WikidataRelationship],
) -> str:
    """Add labels omitted by the LLM without changing resource identities.

    Prompting remains useful, but this post-condition makes label-based SPARQL
    reliable even when the model returns valid Turtle with missing labels.
    """
    graph = Graph().parse(data=rdf_text, format="turtle")
    wd = Namespace("http://www.wikidata.org/entity/")
    kg = Namespace("https://example.org/wikidata-description/")
    graph.bind("rdfs", RDFS)
    graph.bind("wd", wd)
    graph.bind("kg", kg)

    allowed_qids = {
        entity.id for entity in entities if entity.id and re.fullmatch(r"Q\d+", entity.id)
    }
    allowed_qids.update(
        entity_id
        for relationship in relationships
        for entity_id in (relationship.subject_id, relationship.object_id)
        if re.fullmatch(r"Q\d+", entity_id)
    )
    _remove_unresolved_wikidata_resources(graph, allowed_qids, wd)

    known_labels: dict[str, str] = {}
    for entity in entities:
        if entity.id:
            # The mention is the human-readable form present in the input text.
            # Prefer it over Wikidata's canonical label so label-anchored queries
            # can find the generated graph using the wording the user supplied.
            text_label = _human_readable_label(entity.mention.surface)
            canonical_label = _human_readable_label(entity.label)
            if text_label or canonical_label:
                known_labels[entity.id] = text_label or canonical_label
                graph.add(
                    (
                        wd[entity.id],
                        RDFS.label,
                        Literal(text_label or canonical_label, lang="en"),
                    )
                )
    for relationship in relationships:
        subject_label = _human_readable_label(relationship.subject_label)
        object_label = _human_readable_label(relationship.object_label)
        if subject_label:
            known_labels.setdefault(relationship.subject_id, subject_label)
        if object_label:
            known_labels.setdefault(relationship.object_id, object_label)

        if re.fullmatch(r"Q\d+", relationship.subject_id) and re.fullmatch(
            r"Q\d+", relationship.object_id
        ):
            graph.add(
                (
                    wd[relationship.subject_id],
                    _relationship_predicate(relationship, kg),
                    wd[relationship.object_id],
                )
            )

    _ensure_text_cooccurrence_relations(graph, text, entities, kg, wd)

    predicates = {predicate for _, predicate, _ in graph}
    resources = {node for node in graph.subjects() if isinstance(node, URIRef)}
    resources.update(node for node in graph.objects() if isinstance(node, URIRef))
    for resource in resources:
        if resource in predicates or resource in {RDF.type, RDFS.label}:
            continue
        label = _resource_label(resource, known_labels)
        existing_labels = list(graph.objects(resource, RDFS.label))
        for existing in existing_labels:
            if not _human_readable_label(str(existing)):
                graph.remove((resource, RDFS.label, existing))
        existing_labels = list(graph.objects(resource, RDFS.label))
        if not label or any(str(existing).casefold() == label.casefold() for existing in existing_labels):
            continue
        graph.add((resource, RDFS.label, Literal(label, lang="en")))

    return graph.serialize(format="turtle")


def _remove_unresolved_wikidata_resources(
    graph: Graph,
    allowed_qids: set[str],
    wd: Namespace,
) -> None:
    namespace = str(wd)
    for triple in list(graph):
        subject, predicate, object_ = triple
        wikidata_nodes = [
            node
            for node in (subject, predicate, object_)
            if isinstance(node, URIRef) and str(node).startswith(namespace)
        ]
        if any(str(node).removeprefix(namespace) not in allowed_qids for node in wikidata_nodes):
            graph.remove(triple)


def _ensure_text_cooccurrence_relations(
    graph: Graph,
    text: str,
    entities: list[WikidataEntity],
    kg: Namespace,
    wd: Namespace,
) -> None:
    """Add text-local connectivity and directed class/type semantics."""
    segments = _discourse_segments(text)
    entities_by_segment: dict[int, list[tuple[URIRef, WikidataEntity]]] = {}
    for entity in entities:
        if not entity.id or not re.fullmatch(r"Q\d+", entity.id):
            continue
        start = entity.mention.start
        if start is None:
            continue
        segment_index = next(
            (
                index
                for index, (segment_start, segment_end) in enumerate(segments)
                if segment_start <= start < segment_end
            ),
            None,
        )
        if segment_index is None:
            continue
        resource = wd[entity.id]
        segment_entities = entities_by_segment.setdefault(segment_index, [])
        if not any(existing_resource == resource for existing_resource, _ in segment_entities):
            segment_entities.append((resource, entity))

    for segment_entities in entities_by_segment.values():
        for left_index, (left, left_entity) in enumerate(segment_entities):
            for right, right_entity in segment_entities[left_index + 1 :]:
                graph.add((left, kg.related_to, right))
                graph.add((right, kg.related_to, left))
                if _is_classlike_mention(right_entity.mention):
                    graph.add((left, kg["is"], right))
                if _is_classlike_mention(left_entity.mention):
                    graph.add((right, kg["is"], left))


def _is_classlike_mention(mention: EntityMention) -> bool:
    entity_type = str(mention.entity_type or "").casefold()
    return any(
        marker in entity_type
        for marker in ("class", "concept", "object", "nationality")
    )


def _discourse_segments(text: str) -> list[tuple[int, int]]:
    boundaries = [0]
    boundaries.extend(
        match.end()
        for match in re.finditer(r"(?<=[.!?])\s+(?=[A-Z])", text)
    )
    boundaries.append(len(text))
    sentence_spans = [
        (boundaries[index], boundaries[index + 1])
        for index in range(len(boundaries) - 1)
        if text[boundaries[index] : boundaries[index + 1]].strip()
    ]
    continuation = re.compile(
        r"^(?:although|until|he|she|it|they|this|these|those|whose|which|the\s+(?:boat|brand|company))\b",
        flags=re.IGNORECASE,
    )
    segments: list[tuple[int, int]] = []
    for sentence_start, sentence_end in sentence_spans:
        sentence_text = text[sentence_start:sentence_end].strip()
        if segments and continuation.match(sentence_text):
            segments[-1] = (segments[-1][0], sentence_end)
        else:
            segments.append((sentence_start, sentence_end))
    return segments


def _resource_label(resource: URIRef, known_labels: dict[str, str]) -> str:
    value = str(resource)
    qid_match = re.search(r"(?:^|/)(Q\d+)$", value)
    if qid_match:
        qid = qid_match.group(1)
        return known_labels.get(qid, "")
    local_name = re.split(r"[/#]", value.rstrip("/#"))[-1]
    return _human_readable_label(re.sub(r"[_-]+", " ", local_name))


def _human_readable_label(value: str | None) -> str:
    label = str(value or "").strip()
    return "" if re.fullmatch(r"(?:wd:)?Q\d+", label, flags=re.IGNORECASE) else label


def _relationship_predicate(
    relationship: WikidataRelationship,
    kg: Namespace,
) -> URIRef:
    label = relationship.property_label.casefold().strip()
    predicate_names = {
        "instance of": "is",
        "subclass of": "is",
        "part of": "part_of",
        "has part": "has_part",
        "country": "located_in",
        "location": "located_in",
    }
    local_name = predicate_names.get(label)
    if not local_name:
        local_name = re.sub(r"[^a-z0-9]+", "_", label).strip("_") or "related_to"
    return kg[local_name]


def _build_retry_prompt(original_prompt: str, invalid_rdf: str, parser_error: str) -> str:
    error = parser_error[:1200]
    previous = invalid_rdf[:6000]
    return (
        f"{original_prompt}\n\n"
        "The previous answer was not valid Turtle RDF when parsed with rdflib Graph.parse.\n"
        f"Parser error:\n{error}\n\n"
        "Regenerate the complete document so every statement conforms to the standard "
        "RDF/Turtle grammar and the full response parses without errors with "
        "rdflib.Graph.parse(format=\"turtle\"). Treat the parser error only as a "
        "diagnostic: review and correct the entire RDF document, not only the reported "
        "line. Return only the corrected Turtle RDF without markdown, comments, or "
        "explanations.\n"
        f"Previous invalid RDF:\n{previous}"
    )


def _coerce_timeout_seconds(value: Any, default: float) -> float:
    if value in (None, ""):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.001, parsed)
