"""NED with model-directed MCP calls, followed by RDF generation."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any
from uuid import uuid4

from mcp import ClientSession
from rdflib import URIRef
from rdflib.exceptions import ParserError

from ..domain.models import AnalyzeRequest, AnalyzeResponse, EntityMention, WikidataEntity
from ..domain.structured_rdf import RDF_RESPONSE_SCHEMA, WD, model_response_to_graph, serialize_graph
from ..infrastructure import OllamaClient, PromptRepository, RequestLogger, WikidataMCPClient


class RDFValidationError(RuntimeError):
    def __init__(self, message: str, attempts: int, last_error: str | None = None):
        super().__init__(message)
        self.attempts = attempts
        self.last_error = last_error


class CandidateDisambiguationError(RDFValidationError):
    """The model did not produce a valid NED result."""


class HybridAgentService:
    def __init__(
        self,
        llm: OllamaClient,
        wikidata: WikidataMCPClient,
        prompt_repository: PromptRepository,
        system_prompt_name: str = "system/agent.txt",
        ned_prompt_name: str = "prompts/ned.txt",
        rdf_prompt_name: str = "prompts/rdf-build.txt",
        request_logger: RequestLogger | None = None,
        analyze_timeout_seconds: float = 540.0,
        max_turns: int = 20,
        max_tool_calls: int = 40,
    ):
        self.llm = llm
        self.wikidata = wikidata
        self.prompt_repository = prompt_repository
        self.system_prompt_name = system_prompt_name
        self.ned_prompt_name = ned_prompt_name
        self.rdf_prompt_name = rdf_prompt_name
        self.request_logger = request_logger
        self.analyze_timeout_seconds = analyze_timeout_seconds
        self.max_turns = max_turns
        self.max_tool_calls = max_tool_calls

    def analyze(self, request: AnalyzeRequest) -> AnalyzeResponse:
        key = request.idempotence_key or str(uuid4())
        self._log(key, "analyze_started", {"text": request.text})
        try:
            response = asyncio.run(self._analyze(request, key))
        except ExceptionGroup as group:
            # SDK task groups may wrap a domain or transport error during cleanup.
            error: BaseException = group
            while isinstance(error, BaseExceptionGroup) and len(error.exceptions) == 1:
                error = error.exceptions[0]
            if isinstance(error, BaseExceptionGroup):
                raise RuntimeError("MCP session failed; inspect the server connection.") from group
            raise error from group
        self._log(key, "analyze_completed", response.to_dict())
        return response

    async def _analyze(self, request: AnalyzeRequest, key: str) -> AnalyzeResponse:
        budget = request.max_processing_seconds or self.analyze_timeout_seconds
        deadline = asyncio.get_running_loop().time() + budget
        messages = [
            {"role": "system", "content": self.prompt_repository.load_prompt(self.system_prompt_name)},
            {
                "role": "user",
                "content": self.prompt_repository.load_prompt(self.ned_prompt_name).replace("${TEXT}", request.text),
            },
        ]
        async with asyncio.timeout(budget):
            async with self.wikidata.connect() as session:
                tools = await self.wikidata.list_tools(session)
                entities, tool_calls = await self._resolve_entities(
                    request.text, messages, session, tools, deadline, key
                )
            payload = {"text": request.text, "entities": [entity.to_dict() for entity in entities]}
            messages.append(
                {
                    "role": "user",
                    "content": self.prompt_repository.load_prompt(self.rdf_prompt_name).replace(
                        "${PAYLOAD}", json.dumps(payload, ensure_ascii=False)
                    ),
                }
            )
            rdf = await self._build_rdf(messages, entities, deadline, key)
        return AnalyzeResponse(text=request.text, entities=entities, rdf=rdf, tool_calls=tool_calls)

    async def _resolve_entities(
        self,
        text: str,
        messages: list[dict[str, Any]],
        session: ClientSession,
        tools: list[dict[str, Any]],
        deadline: float,
        key: str,
    ) -> tuple[list[WikidataEntity], list[dict[str, Any]]]:
        allowed_names = {tool["function"]["name"] for tool in tools}
        tool_calls: list[dict[str, Any]] = []
        invalid_answers = 0

        for _ in range(self.max_turns):
            message = await self._chat(messages, tools, deadline)
            messages.append(message)
            self._log(key, "llm_ned_response", message)
            calls = message.get("tool_calls") or []
            if not isinstance(calls, list):
                raise RuntimeError("Ollama tool_calls must be an array.")
            if calls:
                if len(tool_calls) + len(calls) > self.max_tool_calls:
                    raise RuntimeError("MCP tool call limit exceeded.")
                for call in calls:
                    function = call.get("function", {}) if isinstance(call, dict) else {}
                    if not isinstance(function, dict):
                        raise RuntimeError("Invalid tool call from Ollama.")
                    name, arguments = function.get("name"), function.get("arguments")
                    if not isinstance(name, str) or name not in allowed_names or not isinstance(arguments, dict):
                        raise RuntimeError("The model requested an unavailable tool or invalid arguments.")
                    self._log(key, "mcp_tool_request", {"name": name, "arguments": arguments})
                    result = await session.call_tool(name, arguments=arguments)
                    output = result.model_dump(mode="json", by_alias=True, exclude_none=True)
                    record = {"name": name, "arguments": arguments, "result": output}
                    tool_calls.append(record)
                    self._log(key, "mcp_tool_result", record)
                    tool_message = {"role": "tool", "tool_name": name, "content": json.dumps(output)}
                    if call.get("id"):
                        tool_message["tool_call_id"] = call["id"]
                    messages.append(tool_message)
                continue

            try:
                successful_results = [call["result"] for call in tool_calls if not call["result"].get("isError")]
                if not successful_results:
                    raise ValueError("Consult the available MCP tools successfully before completing NED.")
                evidence = json.dumps(successful_results, ensure_ascii=False)
                entities = _parse_ned(message.get("content", ""), text, set(re.findall(r"\bQ[1-9]\d*\b", evidence)))
            except (ValueError, TypeError) as exc:
                invalid_answers += 1
                if invalid_answers == 3:
                    raise CandidateDisambiguationError("Candidate disambiguation failed.", 3, str(exc)) from exc
                messages.append(
                    {"role": "user", "content": f"NED validation error: {str(exc)[:1200]}\nCorrect the JSON."}
                )
                continue
            self._log(key, "ned_validated", {"entities": [entity.to_dict() for entity in entities]})
            return entities, tool_calls

        raise CandidateDisambiguationError("NED turn limit exceeded.", self.max_turns)

    async def _build_rdf(
        self, messages: list[dict[str, Any]], entities: list[WikidataEntity], deadline: float, key: str
    ) -> str:
        resolved_ids = {entity.id for entity in entities if entity.id}
        for attempt in range(1, 4):
            message = await self._chat(messages, [], deadline, RDF_RESPONSE_SCHEMA)
            messages.append(message)
            self._log(key, "llm_rdf_response", message)
            try:
                if message.get("tool_calls"):
                    raise ValueError("RDF generation must return JSON, not tool calls.")
                graph = model_response_to_graph(message.get("content", ""))
                graph_ids = {
                    str(node).removeprefix(str(WD))
                    for triple in graph
                    for node in triple
                    if isinstance(node, URIRef) and str(node).startswith(str(WD))
                }
                if graph_ids != resolved_ids:
                    raise ValueError("RDF must use exactly the Wikidata IDs resolved during NED.")
                self._log(key, "rdf_validated", {"attempts": attempt})
                return serialize_graph(graph)
            except (ValueError, TypeError, SyntaxError, ParserError) as exc:
                if attempt == 3:
                    raise RDFValidationError("RDF parsing failed.", attempt, str(exc)) from exc
                messages.append(
                    {"role": "user", "content": f"RDF validation error: {str(exc)[:1200]}\nCorrect the JSON."}
                )
        raise AssertionError("Unreachable")

    async def _chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        deadline: float,
        response_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TimeoutError("Analysis exceeded its processing budget.")
        # Do not block the MCP session reader while Ollama generates a response.
        return await asyncio.to_thread(self.llm.chat, messages, tools, remaining, response_schema)

    def health(self) -> dict[str, Any]:
        return {"llm": self.llm.health_check(), "wikidata_mcp": self.wikidata.health()}

    def _log(self, key: str, event: str, payload: dict[str, Any]) -> None:
        if self.request_logger:
            self.request_logger.log(key, event, payload)


def _parse_ned(raw: str, text: str, evidence_ids: set[str]) -> list[WikidataEntity]:
    """Validate model decisions without choosing or ranking candidates locally."""
    payload = json.loads(raw)
    if not isinstance(payload, dict) or set(payload) != {"entities"} or not isinstance(payload["entities"], list):
        raise ValueError("NED must return a JSON object containing an entities array.")
    if not payload["entities"]:
        raise ValueError("NED returned no entity mentions.")
    entities = []
    spans = set()
    for item in payload["entities"]:
        if not isinstance(item, dict) or set(item) != {"surface", "start", "end", "id", "label"}:
            raise ValueError("Each entity requires exactly surface, start, end, id and label.")
        surface, start, end, qid, label = (item[field] for field in ("surface", "start", "end", "id", "label"))
        if (
            not isinstance(surface, str)
            or not surface
            or type(start) is not int
            or type(end) is not int
            or not 0 <= start < end <= len(text)
            or text[start:end] != surface
        ):
            raise ValueError("Entity offsets must delimit the exact surface in the original text.")
        if (start, end) in spans:
            raise ValueError("NED returned a duplicate mention.")
        if qid is not None and (not isinstance(qid, str) or qid not in evidence_ids):
            raise ValueError("An entity ID must occur in successful MCP results, or be null when unresolved.")
        if not isinstance(label, str) or not label.strip():
            raise ValueError("Each entity needs a non-empty label.")
        spans.add((start, end))
        entities.append(
            WikidataEntity(
                mention=EntityMention(surface=surface, start=start, end=end),
                id=qid,
                iri=f"{WD}{qid}" if qid else None,
                label=label,
            )
        )
    return entities
