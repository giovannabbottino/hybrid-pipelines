import asyncio
import copy
import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp.types import CallToolResult, TextContent
from rdflib import Graph

from hybrid_pipelines.application.services import (
    CandidateDisambiguationError,
    HybridAgentService,
    RDFValidationError,
    _parse_ned,
)
from hybrid_pipelines.domain.models import AnalyzeRequest
from hybrid_pipelines.domain.structured_rdf import RDF_PREFIXES
from hybrid_pipelines.infrastructure import PromptRepository, RequestLogger


def answer(payload):
    return {"role": "assistant", "content": json.dumps(payload)}


def consultation(name="search_items", arguments=None):
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"id": "call-1", "function": {"name": name, "arguments": arguments or {"query": "Douglas Adams"}}}
        ],
    }


def ned(qid: str | None = "Q42"):
    return answer({"entities": [{"surface": "Adams", "start": 0, "end": 5, "id": qid, "label": "Douglas Adams"}]})


def rdf(turtle=None):
    return answer({"rdf": RDF_PREFIXES + (turtle or 'wd:Q42 rdfs:label "Adams" .')})


def setup_service(responses, **options):
    llm = MagicMock()
    llm.history = []
    queue = iter(responses)

    def chat(messages, tools, timeout, schema):
        llm.history.append(copy.deepcopy({"messages": messages, "tools": tools, "schema": schema}))
        return next(queue)

    llm.chat.side_effect = chat
    session = MagicMock()
    session.call_tool = AsyncMock(
        return_value=CallToolResult(
            content=[TextContent(type="text", text="Q42: Douglas Adams — writer")],
        )
    )
    wikidata = MagicMock()

    @asynccontextmanager
    async def connect():
        yield session

    wikidata.connect = connect
    wikidata.list_tools = AsyncMock(
        return_value=[
            {"type": "function", "function": {"name": name, "parameters": {"type": "object"}}}
            for name in ("search_items", "get_statements")
        ]
    )
    service = HybridAgentService(llm, wikidata, PromptRepository(), **options)
    return service, llm, session


def test_model_directs_queries_then_ned_precedes_rdf(tmp_path):
    service, llm, session = setup_service(
        [consultation(), consultation("get_statements", {"entity_id": "Q42"}), ned(), rdf()],
        request_logger=RequestLogger(tmp_path / "events.jsonl"),
    )
    result = service.analyze(AnalyzeRequest(text="Adams", idempotence_key="test"))
    assert [call.args[0] for call in session.call_tool.await_args_list] == ["search_items", "get_statements"]
    assert session.call_tool.await_args_list[0].kwargs == {"arguments": {"query": "Douglas Adams"}}
    assert result.entities[0].id == "Q42"
    assert len(result.tool_calls) == 2
    final = llm.history[-1]
    assert final["tools"] == []
    assert final["schema"]["required"] == ["rdf"]
    tool_messages = [m for m in final["messages"] if m["role"] == "tool"]
    assert len(tool_messages) == 2
    assert tool_messages[0]["tool_call_id"] == "call-1"
    assert "Douglas Adams" in tool_messages[0]["content"]
    assert set(Graph().parse(data=result.rdf, format="turtle")) == set(
        Graph().parse(data=json.loads(rdf()["content"])["rdf"], format="turtle")
    )
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert {event["idempotence_key"] for event in events} == {"test"}
    assert events[-1]["event"] == "analyze_completed"


def test_no_fixed_search_order_and_multiple_tool_calls_are_returned():
    first = consultation("get_statements", {"entity_id": "Q42"})
    first["tool_calls"].append(consultation()["tool_calls"][0])
    service, llm, session = setup_service([first, ned(), rdf()])
    service.analyze(AnalyzeRequest(text="Adams"))
    assert session.call_tool.await_count == 2
    assert session.call_tool.await_args_list[0].args == ("get_statements",)
    assert len([m for m in llm.history[1]["messages"] if m["role"] == "tool"]) == 2


def test_ned_retry_keeps_evidence_and_rejects_unseen_id():
    service, llm, _ = setup_service([consultation(), ned("Q999"), ned(), rdf()])
    result = service.analyze(AnalyzeRequest(text="Adams"))
    assert result.entities[0].id == "Q42"
    assert "NED validation error" in llm.history[2]["messages"][-1]["content"]


@pytest.mark.parametrize(
    "responses",
    [
        [ned(), ned(), ned()],
        [consultation(), ned("Q999"), ned("Q999"), ned("Q999")],
    ],
)
def test_ned_fails_after_three_invalid_answers(responses):
    service, llm, _ = setup_service(responses)
    with pytest.raises(CandidateDisambiguationError) as exc:
        service.analyze(AnalyzeRequest(text="Adams"))
    assert exc.value.attempts == 3
    assert all(call["schema"] is None for call in llm.history)


def test_tool_error_is_returned_to_model_but_is_not_evidence():
    service, llm, session = setup_service([consultation(), ned(), ned(), ned()])
    session.call_tool.return_value = CallToolResult(
        isError=True, content=[TextContent(type="text", text="Failed to fetch Q42")]
    )
    with pytest.raises(CandidateDisambiguationError):
        service.analyze(AnalyzeRequest(text="Adams"))
    tool_message = next(m for m in llm.history[1]["messages"] if m["role"] == "tool")
    assert json.loads(tool_message["content"])["isError"] is True


@pytest.mark.parametrize(
    "invalid",
    [
        answer({}),
        rdf("wd:Q999 kg:is wd:Q42 ."),
        rdf('kg:thing rdfs:label "thing" .'),
    ],
)
def test_rdf_retries_without_local_repair(invalid):
    service, llm, _ = setup_service([consultation(), ned(), invalid, rdf()])
    result = service.analyze(AnalyzeRequest(text="Adams"))
    assert "Q999" not in result.rdf
    assert "RDF validation error" in llm.history[-1]["messages"][-1]["content"]
    assert llm.history[-1]["tools"] == []


def test_invalid_rdf_fails_after_three_attempts():
    service, _, _ = setup_service([consultation(), ned(), answer({}), answer({}), answer({})])
    with pytest.raises(RDFValidationError) as exc:
        service.analyze(AnalyzeRequest(text="Adams"))
    assert exc.value.attempts == 3


def test_unresolved_mention_remains_null():
    service, _, session = setup_service([consultation(), ned(None), rdf('kg:adams rdfs:label "Adams" .')])
    session.call_tool.return_value = CallToolResult(content=[TextContent(type="text", text="No results")])
    result = service.analyze(AnalyzeRequest(text="Adams"))
    assert result.entities[0].id is None
    assert "wd:Q" not in result.rdf


@pytest.mark.parametrize(
    "call",
    [
        consultation("delete_item"),
        {"role": "assistant", "tool_calls": [{"function": None}]},
        {"role": "assistant", "tool_calls": [{"function": {"name": "search_items", "arguments": "invalid"}}]},
    ],
)
def test_unavailable_or_malformed_calls_never_reach_mcp(call):
    service, _, session = setup_service([call])
    with pytest.raises(RuntimeError):
        service.analyze(AnalyzeRequest(text="Adams"))
    session.call_tool.assert_not_awaited()


def test_tool_and_turn_limits_stop_execution():
    service, _, session = setup_service([consultation(), consultation()], max_tool_calls=1)
    with pytest.raises(RuntimeError, match="call limit"):
        service.analyze(AnalyzeRequest(text="Adams"))
    assert session.call_tool.await_count == 1
    service, _, _ = setup_service([consultation()], max_turns=1)
    with pytest.raises(CandidateDisambiguationError, match="turn limit"):
        service.analyze(AnalyzeRequest(text="Adams"))


def test_processing_budget_cancels_waiting_mcp_call():
    service, _, session = setup_service([consultation()])

    async def slow_tool(*args, **kwargs):
        await asyncio.sleep(5)

    session.call_tool.side_effect = slow_tool
    with pytest.raises(TimeoutError):
        service.analyze(AnalyzeRequest(text="Adams", max_processing_seconds=0.05))


def test_sdk_exception_group_preserves_domain_error():
    service, _, session = setup_service([consultation()])
    session.call_tool.side_effect = ExceptionGroup("SDK", [TimeoutError("expired")])
    with pytest.raises(TimeoutError, match="expired"):
        service.analyze(AnalyzeRequest(text="Adams"))


@pytest.mark.parametrize(
    "change",
    [
        {"start": True},
        {"end": 20},
        {"surface": "other"},
        {"id": "Q999"},
        {"label": ""},
    ],
)
def test_ned_validation_rejects_invalid_spans_labels_and_ids(change):
    payload = json.loads(ned()["content"])
    payload["entities"][0].update(change)
    with pytest.raises(ValueError):
        _parse_ned(json.dumps(payload), "Adams", {"Q42"})


def test_repeated_surfaces_can_resolve_to_different_entities():
    payload = {
        "entities": [
            {"surface": "Apple", "start": 0, "end": 5, "id": "Q312", "label": "Apple Inc."},
            {"surface": "Apple", "start": 7, "end": 12, "id": "Q89", "label": "apple"},
        ]
    }
    result = _parse_ned(json.dumps(payload), "Apple. Apple.", {"Q312", "Q89"})
    assert [entity.id for entity in result] == ["Q312", "Q89"]
    payload["entities"].append(payload["entities"][0])
    with pytest.raises(ValueError, match="duplicate"):
        _parse_ned(json.dumps(payload), "Apple. Apple.", {"Q312", "Q89"})
