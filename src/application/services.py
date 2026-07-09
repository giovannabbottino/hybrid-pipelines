from __future__ import annotations

import json
import re
from typing import Any
from uuid import uuid4

from rdflib import Graph

from ..domain.models import AnalyzeRequest, AnalyzeResponse, EntityMention
from ..infrastructure.ollama_client import OllamaClient
from ..infrastructure.prompt_repository import PromptRepository
from ..infrastructure.request_logger import RequestLogger
from ..infrastructure.wikidata_client import WikidataMCPClient


class RDFValidationError(RuntimeError):
    def __init__(self, message: str, attempts: int, last_error: str | None = None) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.last_error = last_error


class HybridAgentService:
    def __init__(
        self,
        llm: OllamaClient,
        wikidata: WikidataMCPClient,
        prompt_repository: PromptRepository,
        system_prompt_name: str = "system/agent.txt",
        entity_prompt_name: str = "prompts/entity-extraction.txt",
        rdf_prompt_name: str = "prompts/rdf-build.txt",
        request_logger: RequestLogger | None = None,
        candidate_limit: int = 3,
    ) -> None:
        self.llm = llm
        self.wikidata = wikidata
        self.prompt_repository = prompt_repository
        self.system_prompt_name = system_prompt_name
        self.entity_prompt_name = entity_prompt_name
        self.rdf_prompt_name = rdf_prompt_name
        self.request_logger = request_logger
        self.candidate_limit = max(1, int(candidate_limit))

    def analyze(self, request: AnalyzeRequest) -> AnalyzeResponse:
        key = request.idempotence_key or str(uuid4())
        self._log(key, "analyze_started", {"text": request.text})

        mentions, extraction_raw = self._extract_entities(request.text, key)
        entities = self.wikidata.resolve_entities(mentions, limit=self.candidate_limit, context=request.text)
        self._log(key, "wikidata_entities", {"entities": [entity.to_dict() for entity in entities]})

        relationships = self.wikidata.find_relationships(entities)
        self._log(key, "wikidata_relationships", {"relationships": [rel.to_dict() for rel in relationships]})

        rdf = self._build_valid_rdf(request.text, entities, relationships, key, getattr(request, "max_rdf_attempts", 3))
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

    def _extract_entities(self, text: str, key: str) -> tuple[list[EntityMention], str]:
        system_prompt = self.prompt_repository.load_prompt(self.system_prompt_name)
        prompt_template = self.prompt_repository.load_prompt(self.entity_prompt_name)
        prompt = prompt_template.replace("${TEXT}", text)
        self._log(key, "llm_entity_request", {"prompt": prompt})
        raw = self.llm.generate(system_prompt=system_prompt, prompt=prompt, stage="entity_extraction")
        self._log(key, "llm_entity_response", {"response": raw})

        payload = _json_from_text(raw)
        items = payload.get("entities") if isinstance(payload, dict) else None
        mentions = [_mention_from_item(item) for item in items or [] if isinstance(item, dict)]
        mentions = [mention for mention in mentions if mention.surface]
        mentions = [*mentions, *_heuristic_mentions(text)]
        return _dedupe_mentions(mentions)[:10], raw

    def _build_valid_rdf(self, text: str, entities: list, relationships: list, key: str, max_attempts: int) -> str:
        attempts = max(1, min(int(max_attempts or 3), 3))
        prompt = self._build_rdf_prompt(text, entities, relationships)
        last_error = None

        for attempt in range(1, attempts + 1):
            rdf = self._build_rdf(prompt, key)
            for repair_method, candidate_rdf in _rdf_repair_candidates(rdf):
                try:
                    _parse_rdf(candidate_rdf)
                    self._log(
                        key,
                        "rdf_validated",
                        {"attempt": attempt, "repair_method": repair_method},
                    )
                    return candidate_rdf
                except Exception as exc:  # rdflib raises parser-specific exception classes.
                    last_error = str(exc)

            if attempt == attempts:
                break
            prompt = _build_retry_prompt(prompt, rdf, last_error or "Invalid Turtle RDF.")

        raise RDFValidationError("rdf parse errror", attempts=attempts, last_error=last_error)

    def _build_rdf_prompt(self, text: str, entities: list, relationships: list) -> str:
        prompt_template = self.prompt_repository.load_prompt(self.rdf_prompt_name)
        payload = {
            "text": text,
            "source_attribution": "Source: Wikidata",
            "entities": [_compact_entity(entity) for entity in entities],
            "relationships": [relationship.to_dict() for relationship in relationships],
        }
        return prompt_template.replace("${PAYLOAD}", json.dumps(payload, ensure_ascii=False, indent=2))

    def _build_rdf(self, prompt: str, key: str) -> str:
        system_prompt = self.prompt_repository.load_prompt(self.system_prompt_name)
        self._log(key, "llm_rdf_request", {"prompt": prompt})
        rdf = self.llm.generate(system_prompt=system_prompt, prompt=prompt, stage="rdf_build").strip()
        self._log(key, "llm_rdf_response", {"response": rdf})
        return _strip_code_fence(rdf)

    def _log(self, key: str, event: str, payload: dict[str, Any]) -> None:
        if self.request_logger:
            self.request_logger.log(idempotence_key=key, event=event, payload=payload)


def _json_from_text(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            return {}
        try:
            data = json.loads(match.group(0))
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
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
        mentions.append(EntityMention(surface=surface, start=match.start(), end=match.end(), entity_type="Entity", confidence=0.2))
    return mentions


def _dedupe_mentions(mentions: list[EntityMention]) -> list[EntityMention]:
    seen: set[str] = set()
    deduped: list[EntityMention] = []
    for mention in mentions:
        key = mention.surface.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(mention)
    return deduped


def _compact_entity(entity: Any, statement_limit: int = 8) -> dict[str, Any]:
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
    Graph().parse(data=rdf_text, format="turtle")


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
