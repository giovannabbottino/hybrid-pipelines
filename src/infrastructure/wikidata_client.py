from __future__ import annotations

import itertools
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any

import requests

from ..domain.models import EntityMention, WikidataEntity, WikidataRelationship


@dataclass(frozen=True)
class WikidataMCPConfig:
    url: str = "https://wd-mcp.wmcloud.org/mcp/"
    language: str = "en"
    timeout_seconds: float = 60.0
    action_api_url: str = "https://www.wikidata.org/w/api.php"
    user_agent: str = "hybrid-pipelines-agent/1.0"
    allow_action_api_fallback: bool = True
    maxlag: int = 5
    max_retries: int = 2
    retry_backoff_seconds: float = 2.0

    @classmethod
    def from_env(cls) -> "WikidataMCPConfig":
        return cls(
            url=os.getenv("WIKIDATA_MCP_URL", "https://wd-mcp.wmcloud.org/mcp/"),
            language=os.getenv("WIKIDATA_LANGUAGE", "en"),
            timeout_seconds=_float_env("WIKIDATA_TIMEOUT_SECONDS", 60.0),
            action_api_url=os.getenv("WIKIDATA_ACTION_API_URL", "https://www.wikidata.org/w/api.php"),
            user_agent=os.getenv("WIKIDATA_USER_AGENT", "hybrid-pipelines-agent/1.0"),
            allow_action_api_fallback=_bool_env("WIKIDATA_ALLOW_ACTION_API_FALLBACK", True),
            maxlag=_int_env("WIKIDATA_MAXLAG", 5),
            max_retries=_int_env("WIKIDATA_MAX_RETRIES", 2),
            retry_backoff_seconds=_float_env("WIKIDATA_RETRY_BACKOFF_SECONDS", 2.0),
        )


class WikidataMCPClient:
    """
    Small client for the hosted Wikidata MCP streamable HTTP endpoint.

    The configured MCP server exposes tools such as search_items and
    get_statements. A Wikidata Action API fallback keeps local tests and
    development useful when the hosted MCP cannot be reached.
    """

    def __init__(self, config: WikidataMCPConfig):
        self.config = config
        self._rpc_ids = itertools.count(1)
        self._session_id: str | None = None
        self._initialized = False
        self._label_cache: dict[str, str] = {}

    def health(self) -> dict[str, Any]:
        try:
            self.search_items("Mango", limit=1)
            return {"status": "ok", "url": self.config.url}
        except requests.RequestException as exc:
            return {"status": "unavailable", "details": str(exc)}

    def resolve_entities(self, mentions: list[EntityMention], limit: int = 3) -> list[WikidataEntity]:
        entities: list[WikidataEntity] = []
        for mention in mentions:
            candidates = self.search_items(mention.surface, limit=limit)
            chosen = candidates[0] if candidates else {}
            entity_id = _entity_id(chosen)
            label = _entity_label(chosen) or mention.surface
            iri = f"http://www.wikidata.org/entity/{entity_id}" if entity_id else None
            statements = self.get_statements(entity_id) if entity_id else []
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
        try:
            result = self._call_tool("search_items", {"query": query, "lang": self.config.language})
            items = _coerce_items(result)
            return items[:limit]
        except requests.RequestException:
            if not self.config.allow_action_api_fallback:
                raise
            return self._search_items_action_api(query=query, limit=limit)

    def get_statements(self, entity_id: str) -> list[dict[str, Any]]:
        try:
            result = self._call_tool(
                "get_statements",
                {
                    "entity_id": entity_id,
                    "include_external_ids": False,
                    "lang": self.config.language,
                },
            )
            return _coerce_statements(result)
        except requests.RequestException:
            if not self.config.allow_action_api_fallback:
                raise
            return self._get_statements_action_api(entity_id)

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
        self._session_id = response.headers.get("Mcp-Session-Id") or response.headers.get("mcp-session-id") or self._session_id
        self._initialized = True
        try:
            self._post_jsonrpc({"jsonrpc": "2.0", "method": "notifications/initialized"})
        except requests.RequestException:
            pass

    def _post_jsonrpc(self, payload: dict[str, Any], initialize: bool = False) -> tuple[dict[str, Any], requests.Response]:
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
        if response.text.lstrip().startswith("data:"):
            return _parse_event_stream_json(response.text), response
        return response.json(), response

    def _search_items_action_api(self, query: str, limit: int) -> list[dict[str, Any]]:
        response = self._request_with_retries(
            "GET",
            self.config.action_api_url,
            params={
                "action": "wbsearchentities",
                "search": query,
                "language": self.config.language,
                "uselang": self.config.language,
                "limit": limit,
                "format": "json",
                "origin": "*",
                "maxlag": self.config.maxlag,
            },
            headers=self._wikimedia_headers(),
        )
        response.raise_for_status()
        items: list[dict[str, Any]] = []
        for hit in response.json().get("search") or []:
            entity_id = hit.get("id")
            if not entity_id:
                continue
            label = hit.get("label") or query
            self._label_cache[entity_id] = label
            items.append(
                {
                    "id": entity_id,
                    "label": label,
                    "description": hit.get("description"),
                    "score": hit.get("pageid"),
                }
            )
        return items

    def _get_statements_action_api(self, entity_id: str) -> list[dict[str, Any]]:
        response = self._request_with_retries(
            "GET",
            self.config.action_api_url,
            params={
                "action": "wbgetentities",
                "ids": entity_id,
                "props": "claims|labels",
                "languages": self.config.language,
                "format": "json",
                "origin": "*",
                "maxlag": self.config.maxlag,
            },
            headers=self._wikimedia_headers(),
        )
        response.raise_for_status()
        entity = (response.json().get("entities") or {}).get(entity_id) or {}
        label = (((entity.get("labels") or {}).get(self.config.language) or {}).get("value")) or entity_id
        self._label_cache[entity_id] = label
        statements: list[dict[str, Any]] = []
        for property_id, claims in (entity.get("claims") or {}).items():
            for claim in claims or []:
                value = (((claim.get("mainsnak") or {}).get("datavalue") or {}).get("value"))
                if not isinstance(value, dict):
                    continue
                object_id = value.get("id")
                if not isinstance(object_id, str) or not object_id.startswith("Q"):
                    continue
                statements.append(
                    {
                        "subject_id": entity_id,
                        "subject_label": label,
                        "property_id": property_id,
                        "property_label": property_id,
                        "object_id": object_id,
                        "object_label": self._label_cache.get(object_id, object_id),
                    }
                )
        return statements

    def _wikimedia_headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate",
            "User-Agent": self.config.user_agent,
        }

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


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value in (None, ""):
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


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
    for line in text.splitlines():
        if not line.startswith("data:"):
            continue
        payload = line.removeprefix("data:").strip()
        if payload and payload != "[DONE]":
            return json.loads(payload)
    return {}


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
    return []


def _coerce_statements(result: Any) -> list[dict[str, Any]]:
    if isinstance(result, dict):
        for key in ("statements", "claims", "triples", "results"):
            if isinstance(result.get(key), list):
                return [item for item in result[key] if isinstance(item, dict)]
    if isinstance(result, list):
        return [item for item in result if isinstance(item, dict)]
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
