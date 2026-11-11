import json
from pathlib import Path

from hybrid_pipelines.infrastructure.ollama_client import OllamaClient, OllamaClientConfig


class StubResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"message": {"role": "assistant", "content": "{}"}, "done": True}


def test_chat_passes_tools_and_preserves_native_tool_calls(monkeypatch, tmp_path: Path):
    payloads = []
    message = {
        "role": "assistant",
        "tool_calls": [{"function": {"name": "search_items", "arguments": {"query": "Adams"}}}],
    }

    class ToolResponse(StubResponse):
        def json(self):
            return {"message": message}

    def fake_post(url, json, timeout):
        assert url == "http://ollama:11434/api/chat"
        payloads.append(json)
        return ToolResponse()

    monkeypatch.setattr("hybrid_pipelines.infrastructure.ollama_client.requests.post", fake_post)
    client = OllamaClient(
        OllamaClientConfig(url="http://ollama:11434/api/generate", jsonl_path=tmp_path / "responses.jsonl")
    )
    tools = [{"type": "function", "function": {"name": "search_items"}}]
    assert client.chat([{"role": "user", "content": "Adams"}], tools, 30) == message
    assert payloads[0]["tools"] == tools
    assert "format" not in payloads[0]


def test_rdf_schema_and_timeout_are_forwarded(monkeypatch, tmp_path):
    from hybrid_pipelines.domain.structured_rdf import RDF_RESPONSE_SCHEMA

    calls = []

    def fake_post(url, json, timeout):
        calls.append((json, timeout))
        return StubResponse()

    monkeypatch.setattr("hybrid_pipelines.infrastructure.ollama_client.requests.post", fake_post)
    client = OllamaClient(OllamaClientConfig(jsonl_path=tmp_path / "responses.jsonl", timeout_seconds=300))
    client.chat([], [], 540, RDF_RESPONSE_SCHEMA)
    client.chat([], [], 10, RDF_RESPONSE_SCHEMA)
    assert [call[1] for call in calls] == [300, 10]
    assert calls[0][0]["format"] == RDF_RESPONSE_SCHEMA


def test_jsonl_log_appends_one_record_per_line_with_native_types(tmp_path):
    log_path = tmp_path / "logs" / "responses.jsonl"
    client = OllamaClient(OllamaClientConfig(jsonl_path=log_path))
    response = {
        "model": "test-model",
        "response": '{"rdf":"line one\\nline two"}',
        "created_at": "2026-11-09T12:00:00Z",
        "done": True,
        "total_duration": 12345,
    }
    for stage in ("entity_extraction", "rdf_build"):
        client._log(stage, "Ação\nsecond line", response)
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    entries = [json.loads(line) for line in lines]
    assert [entry["stage"] for entry in entries] == ["entity_extraction", "rdf_build"]
    assert entries[0] == {"stage": "entity_extraction", "prompt": "Ação\nsecond line", **response}
    assert entries[1]["done"] is True
    assert entries[1]["total_duration"] == 12345


def test_log_write_failure_disables_logging_without_failing_generation(tmp_path, monkeypatch):
    blocked_path = tmp_path / "blocked"
    blocked_path.write_text("file instead of directory", encoding="utf-8")
    client = OllamaClient(OllamaClientConfig(jsonl_path=blocked_path / "responses.jsonl"))
    monkeypatch.setattr("hybrid_pipelines.infrastructure.ollama_client.requests.post", lambda *a, **kw: StubResponse())
    assert client.chat([], [], 10)["content"] == "{}"
    assert client._logging_disabled
