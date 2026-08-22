from __future__ import annotations

import json
import re
import time
from typing import Any, Protocol
from uuid import uuid4

import requests
from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF, RDFS

from ..domain.models import AnalyzeRequest, AnalyzeResponse, EntityMention, WikidataEntity, WikidataRelationship
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
        self.rdf_prompt_name = rdf_prompt_name
        self.request_logger = request_logger
        self.candidate_limit = max(1, int(candidate_limit))
        self.analyze_timeout_seconds = max(1.0, float(analyze_timeout_seconds))
        self.mention_limit = max(1, int(mention_limit))

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
            getattr(request, "max_rdf_attempts", 1),
            deadline,
            prefer_deterministic=getattr(
                request,
                "prefer_deterministic_rdf",
                False,
            ),
        )
        self._log(key, "rdf_built", {"rdf": rdf})

        return AnalyzeResponse(
            text=request.text,
            entities=entities,
            relationships=relationships,
            rdf=rdf,
            llm={"entity_extraction": extraction_raw},
        )

    def health(self) -> dict[str, Any]:
        return {
            "llm": self.llm.health_check(),
            "wikidata_mcp": self.wikidata.health(),
        }

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
        items = payload.get("entities") if isinstance(payload, dict) else None
        mentions = [_mention_from_item(item) for item in items or [] if isinstance(item, dict)]
        mentions = [mention for mention in mentions if mention.surface]
        mentions = _heuristic_mentions(text) if not mentions else _realign_mentions(text, mentions)
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
        prefer_deterministic: bool = False,
    ) -> str:
        attempts = max(1, min(int(max_attempts or 3), 3))
        last_error = None

        if prefer_deterministic:
            try:
                deterministic_rdf = _build_deterministic_rdf(
                    text,
                    entities,
                    relationships,
                )
                _parse_rdf(deterministic_rdf)
                self._log(
                    key,
                    "rdf_validated",
                    {
                        "attempt": 0,
                        "repair_method": "deterministic_primary",
                    },
                )
                return deterministic_rdf
            except Exception as exc:
                last_error = f"deterministic primary: {exc}"

        prompt = self._build_rdf_prompt(text, entities, relationships)
        for attempt in range(1, attempts + 1):
            rdf = self._build_rdf(prompt, key, deadline)
            for repair_method, candidate_rdf in _rdf_repair_candidates(rdf):
                try:
                    _parse_rdf(candidate_rdf)
                    candidate_rdf = _ensure_entity_labels(
                        candidate_rdf,
                        text,
                        entities,
                        relationships,
                    )
                    self._log(
                        key,
                        "rdf_validated",
                        {"attempt": attempt, "repair_method": repair_method},
                    )
                    return candidate_rdf
                except Exception as exc:  # rdflib raises parser-specific exception classes.
                    last_error = str(exc)

            try:
                fallback_rdf = _build_deterministic_rdf(
                    text,
                    entities,
                    relationships,
                )
                _parse_rdf(fallback_rdf)
                self._log(
                    key,
                    "rdf_validated",
                    {
                        "attempt": attempt,
                        "repair_method": "deterministic_fallback",
                        "previous_error": last_error,
                    },
                )
                return fallback_rdf
            except Exception as exc:
                fallback_error = str(exc)
                if last_error:
                    last_error = (
                        f"{last_error}; deterministic fallback: {fallback_error}"
                    )
                else:
                    last_error = f"deterministic fallback: {fallback_error}"

            if attempt < attempts:
                prompt = _build_retry_prompt(
                    prompt,
                    rdf,
                    last_error or "Invalid Turtle RDF.",
                )

        raise RDFValidationError(
            "rdf parse errror",
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
    def payload_from(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if isinstance(value, list):
            return {"entities": value}
        return {}

    try:
        return payload_from(json.loads(text))
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if match:
            try:
                return payload_from(json.loads(match.group(0)))
            except json.JSONDecodeError:
                pass

        entities_start = re.search(r'"entities"\s*:\s*', text)
        array_start = text.find("[", entities_start.end() if entities_start else 0)
        array_end = text.rfind("]")
        if array_start >= 0 and array_end > array_start:
            try:
                entities = json.loads(text[array_start : array_end + 1])
                return payload_from(entities)
            except json.JSONDecodeError:
                pass
        return {}


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


def _heuristic_mentions(text: str) -> list[EntityMention]:
    mentions: list[EntityMention] = []
    stopwords = {"a", "an", "the", "is", "are", "was", "were", "not", "from", "of", "in", "on", "to"}
    for match in re.finditer(r"\b[A-Za-z][A-Za-z-]*\b", text):
        surface = match.group(0)
        if surface.casefold() in stopwords:
            continue
        mentions.append(
            EntityMention(
                surface=surface,
                start=match.start(),
                end=match.end(),
                entity_type="Entity",
                confidence=0.2,
            )
        )
    return mentions


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


def _build_deterministic_rdf(
    text: str,
    entities: list[WikidataEntity],
    relationships: list[WikidataRelationship],
) -> str:
    """Build a valid evidence-only graph when every LLM Turtle attempt is invalid."""
    graph = Graph()
    wd = Namespace("http://www.wikidata.org/entity/")
    graph.bind("rdfs", RDFS)
    graph.bind("wd", wd)

    for entity in entities:
        if not entity.id or not re.fullmatch(r"Q\d+", entity.id):
            continue
        label = (
            _human_readable_label(entity.mention.surface)
            or _human_readable_label(entity.label)
        )
        if label:
            graph.add((wd[entity.id], RDFS.label, Literal(label, lang="en")))

    for relationship in relationships:
        for entity_id, raw_label in (
            (relationship.subject_id, relationship.subject_label),
            (relationship.object_id, relationship.object_label),
        ):
            label = _human_readable_label(raw_label)
            if re.fullmatch(r"Q\d+", entity_id) and label:
                graph.add((wd[entity_id], RDFS.label, Literal(label, lang="en")))

    if len(graph) == 0:
        raise ValueError("No resolved, human-readable entities for deterministic RDF.")

    return _ensure_entity_labels(
        graph.serialize(format="turtle"),
        text,
        entities,
        relationships,
    )


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


def _rdf_repair_candidates(rdf_text: str):
    raw = (rdf_text or "").strip().replace("\r\n", "\n").replace("\r", "\n")
    raw = raw.replace("\u201c", '"').replace("\u201d", '"').replace("\u2019", "'")
    raw = re.sub(r'""([^"\n]+)""', r'"\1"', raw)

    seen = set()

    def emit(method: str, value: str):
        value = (value or "").strip()
        if not value or value in seen:
            return
        seen.add(value)
        yield method, value

    yield from emit("trim", raw)

    if raw and not raw.endswith("."):
        yield from emit("append_final_dot", raw + " .")

    lines = raw.splitlines()
    last_complete_line = None
    for idx, line in enumerate(lines):
        if line.strip().endswith("."):
            last_complete_line = idx
    if last_complete_line is not None:
        yield from emit("keep_through_last_complete_statement", "\n".join(lines[: last_complete_line + 1]))

    blocks = re.split(r"\n\s*\n", raw)
    while len(blocks) > 1:
        blocks = blocks[:-1]
        candidate = "\n\n".join(blocks).strip()
        if candidate and not candidate.endswith("."):
            candidate += " ."
        yield from emit("drop_incomplete_last_block", candidate)


def _build_retry_prompt(original_prompt: str, invalid_rdf: str, parser_error: str) -> str:
    error = parser_error[:1200]
    previous = invalid_rdf[:6000]
    return (
        f"{original_prompt}\n\n"
        "The previous answer was not valid Turtle RDF when parsed with rdflib Graph.parse.\n"
        f"Parser error:\n{error}\n\n"
        "Return only corrected valid Turtle RDF. Do not include markdown fences, comments, or explanations.\n"
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
