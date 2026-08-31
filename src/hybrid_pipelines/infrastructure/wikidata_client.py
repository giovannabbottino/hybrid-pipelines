from __future__ import annotations

import itertools
import json
import os
import re
import time
from collections import deque
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

import requests

from ..domain.models import (
    EntityMention,
    WikidataCandidateGroup,
    WikidataEntity,
    WikidataPath,
    WikidataRelationship,
)


@dataclass(frozen=True)
class WikidataMCPConfig:
    url: str = "https://wd-mcp.wmcloud.org/mcp/"
    language: str = "en"
    timeout_seconds: float = 60.0
    user_agent: str = "hybrid-pipelines-agent/1.0"
    max_retries: int = 2
    retry_backoff_seconds: float = 2.0

    @classmethod
    def from_env(cls) -> WikidataMCPConfig:
        return cls(
            url=os.getenv("WIKIDATA_MCP_URL", "https://wd-mcp.wmcloud.org/mcp/"),
            language=os.getenv("WIKIDATA_LANGUAGE", "en"),
            timeout_seconds=_float_env("WIKIDATA_TIMEOUT_SECONDS", 60.0),
            user_agent=os.getenv("WIKIDATA_USER_AGENT", "hybrid-pipelines-agent/1.0"),
            max_retries=_int_env("WIKIDATA_MAX_RETRIES", 2),
            retry_backoff_seconds=_float_env("WIKIDATA_RETRY_BACKOFF_SECONDS", 2.0),
        )


class WikidataMCPClient:
    """
    Small client for the hosted Wikidata MCP streamable HTTP endpoint.

    The configured MCP server exposes tools such as search_items and
    get_statements. All Wikidata evidence is obtained through this endpoint.
    """

    def __init__(self, config: WikidataMCPConfig):
        self.config = config
        self._rpc_ids = itertools.count(1)
        self._session_id: str | None = None
        self._initialized = False
        self._label_cache: dict[str, str] = {}
        self._statement_cache: dict[str, list[dict[str, Any]]] = {}

    def health(self) -> dict[str, Any]:
        try:
            self.search_items("Mango", limit=1)
            return {"status": "ok", "url": self.config.url}
        except requests.RequestException as exc:
            return {"status": "unavailable", "details": str(exc)}

    def search_candidate_groups(
        self,
        mentions: list[EntityMention],
        limit: int = 3,
        context: str | None = None,
    ) -> list[WikidataCandidateGroup]:
        groups: list[WikidataCandidateGroup] = []
        candidate_limit = max(1, int(limit))
        for mention in mentions:
            mention_context = _mention_context(context or "", mention)
            items = self.search_items(mention.surface, limit=max(candidate_limit, 5))
            candidates: list[WikidataEntity] = []
            seen_ids: set[str] = set()
            for item in items:
                entity_id = _entity_id(item)
                if not entity_id or entity_id in seen_ids:
                    continue
                seen_ids.add(entity_id)
                if entity_id not in self._statement_cache:
                    self._statement_cache[entity_id] = self.get_statements(entity_id)
                label = _entity_label(item) or mention.surface
                self._label_cache[entity_id] = label
                candidates.append(
                    WikidataEntity(
                        mention=mention,
                        id=entity_id,
                        iri=f"http://www.wikidata.org/entity/{entity_id}",
                        label=label,
                        description=_entity_description(item),
                        score=_entity_score(item),
                        statements=self._statement_cache[entity_id],
                    )
                )
            candidates = _rank_candidate_entities(candidates, mention, mention_context)
            type_aligned = [
                candidate
                for candidate in candidates
                if _candidate_type_alignment(candidate, mention.entity_type) > 0
            ]
            if type_aligned:
                candidates = type_aligned
            candidates = candidates[:candidate_limit]
            groups.append(WikidataCandidateGroup(mention=mention, candidates=candidates))
        return groups

    def find_candidate_paths(
        self,
        groups: list[WikidataCandidateGroup],
        max_hops: int = 2,
        hub_degree_threshold: int = 25,
        expansion_limit: int = 30,
        path_limit: int = 24,
    ) -> list[WikidataPath]:
        candidate_ids = {
            candidate.id
            for group in groups
            for candidate in group.candidates
            if candidate.id
        }
        labels = {
            candidate.id: candidate.label
            for group in groups
            for candidate in group.candidates
            if candidate.id
        }
        edges: list[WikidataRelationship] = []
        edge_keys: set[tuple[str, str, str]] = set()

        for group in groups:
            for candidate in group.candidates:
                if candidate.id:
                    _append_statement_relationships(
                        candidate.id,
                        candidate.label,
                        candidate.statements,
                        edges,
                        edge_keys,
                        labels,
                    )

        if max_hops > 1:
            intermediate_ids = sorted(
                {
                    edge.object_id
                    for edge in edges
                    if edge.object_id not in candidate_ids
                }
            )[: max(0, int(expansion_limit))]
            for entity_id in intermediate_ids:
                if entity_id not in self._statement_cache:
                    self._statement_cache[entity_id] = self.get_statements(entity_id)
                _append_statement_relationships(
                    entity_id,
                    labels.get(entity_id, self._label_cache.get(entity_id, entity_id)),
                    self._statement_cache[entity_id],
                    edges,
                    edge_keys,
                    labels,
                )

        adjacency = _relationship_adjacency(edges)
        blocked_hubs = {
            node_id
            for node_id, neighbors in adjacency.items()
            if len({neighbor_id for neighbor_id, _ in neighbors}) > max(1, int(hub_degree_threshold))
            and node_id not in candidate_ids
        }
        paths: list[WikidataPath] = []
        seen_pairs: set[tuple[str, str]] = set()
        for left_index, left_group in enumerate(groups):
            for right_group in groups[left_index + 1 :]:
                for source in left_group.candidates:
                    if not source.id:
                        continue
                    for target in right_group.candidates:
                        if not target.id or source.id == target.id:
                            continue
                        pair = (
                            min(source.id, target.id),
                            max(source.id, target.id),
                        )
                        if pair in seen_pairs:
                            continue
                        path_edges = _shortest_relationship_path(
                            adjacency,
                            source.id,
                            target.id,
                            max_hops=max(1, min(int(max_hops), 2)),
                            blocked=blocked_hubs,
                        )
                        if not path_edges:
                            continue
                        seen_pairs.add(pair)
                        paths.append(
                            WikidataPath(
                                source_id=source.id,
                                target_id=target.id,
                                edges=path_edges,
                            )
                        )
                        if len(paths) >= max(1, int(path_limit)):
                            return paths
        return paths

    def resolve_entities(
        self,
        mentions: list[EntityMention],
        limit: int = 3,
        context: str | None = None,
    ) -> list[WikidataEntity]:
        entities: list[WikidataEntity] = []
        for mention in mentions:
            mention_context = _mention_context(context or "", mention)
            query = _contextual_query(mention.surface, mention_context)
            candidates = self.search_items(query, limit=max(limit, 10))
            chosen = (
                _choose_candidate(
                    candidates,
                    context=mention_context or mention.surface,
                    surface=mention.surface,
                )
                if candidates
                else {}
            )
            entity_id = _entity_id(chosen)
            label = _entity_label(chosen) or mention.surface
            iri = f"http://www.wikidata.org/entity/{entity_id}" if entity_id else None
            statements = []
            if entity_id is not None and mention.confidence != 0.5:
                if entity_id not in self._statement_cache:
                    self._statement_cache[entity_id] = self.get_statements(entity_id)
                statements = self._statement_cache[entity_id]
            if entity_id:
                self._label_cache[entity_id] = label
            entities.append(
                WikidataEntity(
                    mention=mention,
                    id=entity_id,
                    iri=iri,
                    label=label,
                    description=_entity_description(chosen),
                    score=_entity_score(chosen),
                    statements=statements,
                )
            )
        return entities

    def search_items(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        result = self._call_tool("search_items", {"query": query, "lang": self.config.language})
        return _coerce_items(result)[:limit]

    def get_statements(self, entity_id: str) -> list[dict[str, Any]]:
        result = self._call_tool(
            "get_statements",
            {
                "entity_id": entity_id,
                "include_external_ids": False,
                "lang": self.config.language,
            },
        )
        return _coerce_statements(result)

    def find_relationships(self, entities: list[WikidataEntity]) -> list[WikidataRelationship]:
        by_id = {entity.id: entity for entity in entities if entity.id}
        relationships: list[WikidataRelationship] = []
        seen: set[tuple[str, str, str]] = set()

        for entity in entities:
            if not entity.id:
                continue
            for statement in entity.statements:
                for edge in _statement_edges(statement):
                    object_id = edge.get("object_id")
                    if object_id not in by_id:
                        continue
                    key = (entity.id, edge.get("property_id") or "", object_id)
                    if key in seen:
                        continue
                    seen.add(key)
                    target = by_id[object_id]
                    relationships.append(
                        WikidataRelationship(
                            subject_id=entity.id,
                            subject_label=entity.label,
                            property_id=edge.get("property_id") or "P?",
                            property_label=edge.get("property_label") or edge.get("property_id") or "related to",
                            object_id=object_id,
                            object_label=edge.get("object_label") or target.label,
                        )
                    )
        return relationships

    def _call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        self._ensure_initialized()
        payload = {
            "jsonrpc": "2.0",
            "id": next(self._rpc_ids),
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        data, _ = self._post_jsonrpc(payload)
        if "error" in data:
            raise requests.RequestException(str(data["error"]))
        return _unwrap_mcp_result(data.get("result"))

    def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        payload = {
            "jsonrpc": "2.0",
            "id": next(self._rpc_ids),
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "hybrid-pipelines", "version": "1.0.0"},
            },
        }
        data, response = self._post_jsonrpc(payload, initialize=True)
        if "error" in data:
            raise requests.RequestException(str(data["error"]))
        self._session_id = (
            response.headers.get("Mcp-Session-Id")
            or response.headers.get("mcp-session-id")
            or self._session_id
        )
        self._initialized = True
        with suppress(requests.RequestException):
            self._post_jsonrpc({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _post_jsonrpc(
        self,
        payload: dict[str, Any],
        initialize: bool = False,
    ) -> tuple[dict[str, Any], requests.Response]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Accept-Encoding": "gzip, deflate",
            "Content-Type": "application/json",
            "User-Agent": self.config.user_agent,
        }
        if self._session_id and not initialize:
            headers["Mcp-Session-Id"] = self._session_id
        response = self._request_with_retries("POST", self.config.url, headers=headers, json=payload)
        response.raise_for_status()
        # Streamable HTTP responses may start with an SSE `event:` line and
        # omit a charset. Decode them consistently before parsing `data:`.
        response.encoding = "utf-8"
        if response.text.lstrip().startswith(("data:", "event:")):
            return _parse_event_stream_json(response.text), response
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("Wikidata MCP returned a non-object JSON-RPC response.")
        return data, response

    def _request_with_retries(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        attempts = max(0, self.config.max_retries) + 1
        last_exc: requests.RequestException | None = None
        for attempt in range(attempts):
            try:
                response = requests.request(method, url, timeout=self.config.timeout_seconds, **kwargs)
                if response.status_code == 429 and attempt < attempts - 1:
                    time.sleep(_retry_delay(response, self.config.retry_backoff_seconds * (attempt + 1)))
                    continue
                response.raise_for_status()
                return response
            except requests.HTTPError as exc:
                last_exc = exc
                if exc.response is None or exc.response.status_code not in {429, 503} or attempt == attempts - 1:
                    raise
                time.sleep(_retry_delay(exc.response, self.config.retry_backoff_seconds * (attempt + 1)))
            except requests.RequestException as exc:
                last_exc = exc
                if attempt == attempts - 1:
                    raise
                time.sleep(self.config.retry_backoff_seconds * (attempt + 1))
        if last_exc:
            raise last_exc
        raise requests.RequestException("Wikidata request failed.")


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value in (None, ""):
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    if value in (None, ""):
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _retry_delay(response: requests.Response, default: float) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            return max(0.0, float(retry_after))
        except ValueError:
            return default
    return default


def _parse_event_stream_json(text: str) -> dict[str, Any]:
    for line in reversed(text.splitlines()):
        if not line.startswith("data:"):
            continue
        payload = line.removeprefix("data:").strip()
        if payload and payload != "[DONE]":
            data = json.loads(payload)
            if isinstance(data, dict):
                return data
    raise ValueError("Wikidata MCP returned an event stream without JSON data.")


def _unwrap_mcp_result(result: Any) -> Any:
    if not isinstance(result, dict):
        return result
    content = result.get("content")
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, dict):
            if "json" in first:
                return first["json"]
            text = first.get("text")
            if isinstance(text, str):
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return text
    return result


def _coerce_items(result: Any) -> list[dict[str, Any]]:
    if isinstance(result, dict):
        for key in ("items", "results", "search"):
            if isinstance(result.get(key), list):
                return [item for item in result[key] if isinstance(item, dict)]
    if isinstance(result, list):
        return [item for item in result if isinstance(item, dict)]
    if isinstance(result, str):
        items = []
        for line in result.splitlines():
            match = re.match(r"^(Q\d+):\s*(.*?)(?:\s+—\s+(.*))?$", line.strip())
            if not match:
                continue
            items.append(
                {
                    "id": match.group(1),
                    "label": match.group(2).strip(),
                    "description": (match.group(3) or "").strip(),
                }
            )
        return items
    return []


def _coerce_statements(result: Any) -> list[dict[str, Any]]:
    if isinstance(result, dict):
        for key in ("statements", "claims", "triples", "results"):
            if isinstance(result.get(key), list):
                return [item for item in result[key] if isinstance(item, dict)]
    if isinstance(result, list):
        return [item for item in result if isinstance(item, dict)]
    if isinstance(result, str):
        statements = []
        pattern = re.compile(
            r"^(.*?)\s+\((Q\d+)\):\s+(.*?)\s+\((P\d+)\):\s+(.*?)\s+\((Q\d+)\)$"
        )
        for line in result.splitlines():
            match = pattern.match(line.strip())
            if not match:
                continue
            statements.append(
                {
                    "subject_label": match.group(1).strip(),
                    "subject_id": match.group(2),
                    "property_label": match.group(3).strip(),
                    "property_id": match.group(4),
                    "object_label": match.group(5).strip(),
                    "object_id": match.group(6),
                }
            )
        return statements
    return []


def _entity_id(item: dict[str, Any]) -> str | None:
    for key in ("id", "entity_id", "qid"):
        value = item.get(key)
        if isinstance(value, str) and re.fullmatch(r"Q\d+", value):
            return value
    for key in ("iri", "uri", "url", "concepturi"):
        value = item.get(key)
        if isinstance(value, str):
            match = re.search(r"(Q\d+)", value)
            if match:
                return match.group(1)
    return None


def _entity_label(item: dict[str, Any]) -> str | None:
    for key in ("label", "name", "title"):
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, dict):
            nested = value.get("value") or value.get("text")
            if isinstance(nested, str):
                return nested
    return None


def _entity_description(item: dict[str, Any]) -> str | None:
    value = item.get("description")
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        nested = value.get("value") or value.get("text")
        if isinstance(nested, str):
            return nested
    return None


def _entity_score(item: dict[str, Any]) -> float | None:
    value = item.get("score") or item.get("rank") or item.get("pageid")
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _choose_candidate(
    candidates: list[dict[str, Any]],
    context: str,
    surface: str = "",
) -> dict[str, Any]:
    context_terms = _terms(context)
    if not context_terms:
        return candidates[0]

    normalized_surface = _normalized_name(surface)

    def score(index_and_item: tuple[int, dict[str, Any]]) -> tuple[int, float, int, int]:
        index, item = index_and_item
        label = _entity_label(item) or ""
        normalized_label = _normalized_name(label)
        label_terms = _terms(label)
        haystack = " ".join(
            value
            for value in (
                _entity_label(item),
                _entity_description(item),
            )
            if value
        )
        overlap = len(context_terms.intersection(_terms(haystack)))
        label_overlap = (
            len(_terms(surface) & label_terms) / len(_terms(surface) | label_terms)
            if _terms(surface) and label_terms
            else 0.0
        )
        return (
            1 if normalized_surface and normalized_label == normalized_surface else 0,
            label_overlap,
            -index,
            overlap,
        )

    return max(enumerate(candidates), key=score)[1]


_WIKIDATA_TYPE_ROOTS = {
    "person": {"Q5"},
    "organization": {"Q43229"},
    "place": {"Q2221906"},
    "event": {"Q1656682"},
    "disease": {"Q12136"},
    "taxon": {"Q16521"},
    "work": {"Q386724"},
    "product": {"Q2424752"},
}

_WIKIDATA_TYPE_TERMS = {
    "person": {"person", "human", "people"},
    "organization": {"organization", "company", "business", "corporation", "institution"},
    "place": {"place", "location", "city", "country", "region", "building"},
    "event": {"event", "incident", "occurrence"},
    "disease": {"disease", "disorder", "syndrome", "condition"},
    "taxon": {"taxon", "species", "organism", "genus"},
    "work": {"work", "book", "film", "album", "song", "publication"},
    "product": {"product", "device", "vehicle", "software", "brand"},
}


def _rank_candidate_entities(
    candidates: list[WikidataEntity],
    mention: EntityMention,
    context: str,
) -> list[WikidataEntity]:
    surface_name = _normalized_name(mention.surface)
    surface_terms = _terms(mention.surface)
    context_terms = _terms(context)

    def score(index_and_candidate: tuple[int, WikidataEntity]) -> tuple[int, int, float, int, float, int]:
        index, candidate = index_and_candidate
        label_name = _normalized_name(candidate.label)
        label_terms = _terms(candidate.label)
        union = surface_terms | label_terms
        label_overlap = len(surface_terms & label_terms) / len(union) if union else 0.0
        candidate_text = " ".join(filter(None, (candidate.label, candidate.description)))
        context_overlap = len(context_terms & _terms(candidate_text))
        return (
            1 if surface_name and label_name == surface_name else 0,
            _candidate_type_alignment(candidate, mention.entity_type),
            label_overlap,
            context_overlap,
            candidate.score or 0.0,
            -index,
        )

    return [
        candidate
        for _, candidate in sorted(
            enumerate(candidates),
            key=score,
            reverse=True,
        )
    ]


def _candidate_type_alignment(candidate: WikidataEntity, entity_type: str | None) -> int:
    normalized_type = str(entity_type or "").casefold().strip()
    roots = _WIKIDATA_TYPE_ROOTS.get(normalized_type)
    if not roots:
        return 0
    type_terms = _WIKIDATA_TYPE_TERMS.get(normalized_type, _terms(normalized_type))
    for statement in candidate.statements:
        for edge in _statement_edges(statement):
            if edge.get("property_id") not in {"P31", "P279"}:
                continue
            if edge.get("object_id") in roots:
                return 2
            if type_terms & _terms(edge.get("object_label") or ""):
                return 1
    if type_terms & _terms(" ".join(filter(None, (candidate.label, candidate.description)))):
        return 1
    return 0


def _append_statement_relationships(
    subject_id: str,
    subject_label: str,
    statements: list[dict[str, Any]],
    relationships: list[WikidataRelationship],
    seen: set[tuple[str, str, str]],
    labels: dict[str, str],
) -> None:
    for statement in statements:
        for edge in _statement_edges(statement):
            object_id = edge.get("object_id")
            if not object_id:
                continue
            property_id = edge.get("property_id") or "P?"
            key = (subject_id, property_id, object_id)
            if key in seen:
                continue
            seen.add(key)
            object_label = edge.get("object_label") or labels.get(object_id) or object_id
            if object_label != object_id:
                labels.setdefault(object_id, object_label)
            relationships.append(
                WikidataRelationship(
                    subject_id=subject_id,
                    subject_label=subject_label,
                    property_id=property_id,
                    property_label=edge.get("property_label") or property_id,
                    object_id=object_id,
                    object_label=object_label,
                )
            )


def _relationship_adjacency(
    relationships: list[WikidataRelationship],
) -> dict[str, list[tuple[str, WikidataRelationship]]]:
    adjacency: dict[str, list[tuple[str, WikidataRelationship]]] = {}
    for relationship in relationships:
        adjacency.setdefault(relationship.subject_id, []).append((relationship.object_id, relationship))
        adjacency.setdefault(relationship.object_id, []).append((relationship.subject_id, relationship))
    return adjacency


def _shortest_relationship_path(
    adjacency: dict[str, list[tuple[str, WikidataRelationship]]],
    source_id: str,
    target_id: str,
    max_hops: int,
    blocked: set[str],
) -> list[WikidataRelationship]:
    queue: deque[tuple[str, list[WikidataRelationship], set[str]]] = deque(
        [(source_id, [], {source_id})]
    )
    while queue:
        node_id, path, visited = queue.popleft()
        if len(path) >= max_hops:
            continue
        for neighbor_id, relationship in adjacency.get(node_id, []):
            if neighbor_id in visited or (neighbor_id in blocked and neighbor_id != target_id):
                continue
            next_path = [*path, relationship]
            if neighbor_id == target_id:
                return next_path
            queue.append((neighbor_id, next_path, {*visited, neighbor_id}))
    return []


def _contextual_query(surface: str, context: str) -> str:
    return surface


def _mention_context(
    context: str,
    mention: EntityMention,
) -> str:
    """Return sentence-local context for one resolved mention."""
    if mention.start is None or mention.end is None:
        return context
    previous_boundaries = [context.rfind(mark, 0, mention.start) for mark in ".!?\n"]
    start = max(previous_boundaries) + 1
    following = [
        position
        for mark in ".!?\n"
        if (position := context.find(mark, mention.end)) >= 0
    ]
    end = min(following) + 1 if following else len(context)
    return context[start:end]


def _normalized_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _terms(text: str) -> set[str]:
    stopwords = {"a", "an", "the", "is", "are", "was", "were", "not", "from", "of", "in", "on", "to", "and", "or"}
    return {
        term
        for term in re.findall(r"[a-z][a-z-]+", text.casefold())
        if term not in stopwords and len(term) > 2
    }


def _statement_edges(statement: dict[str, Any]) -> list[dict[str, str]]:
    direct = _direct_statement_edge(statement)
    if direct:
        return [direct]
    edges: list[dict[str, str]] = []
    for key in ("values", "objects", "targets"):
        values = statement.get(key)
        if not isinstance(values, list):
            continue
        for value in values:
            if not isinstance(value, dict):
                continue
            merged = {**statement, **value}
            edge = _direct_statement_edge(merged)
            if edge:
                edges.append(edge)
    return edges


def _direct_statement_edge(statement: dict[str, Any]) -> dict[str, str] | None:
    property_id = _first_string(statement, "property_id", "property", "predicate_id", "predicate")
    property_label = _first_string(statement, "property_label", "predicate_label", "property_name")
    object_id = _first_qid(statement, "object_id", "value_id", "target_id", "entity_id", "object")
    object_label = _first_string(statement, "object_label", "value_label", "target_label", "label")

    if not object_id:
        value = statement.get("value")
        if isinstance(value, dict):
            object_id = _first_qid(value, "id", "entity_id", "qid")
            object_label = object_label or _first_string(value, "label", "name")
    if not object_id:
        return None
    return {
        "property_id": property_id or "P?",
        "property_label": property_label or property_id or "related to",
        "object_id": object_id,
        "object_label": object_label or object_id,
    }


def _first_string(data: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, dict):
            nested = value.get("id") or value.get("label") or value.get("value")
            if isinstance(nested, str) and nested:
                return nested
    return None


def _first_qid(data: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str):
            match = re.search(r"(Q\d+)", value)
            if match:
                return match.group(1)
        if isinstance(value, dict):
            nested = value.get("id") or value.get("entity_id") or value.get("qid")
            if isinstance(nested, str) and re.fullmatch(r"Q\d+", nested):
                return nested
    return None
