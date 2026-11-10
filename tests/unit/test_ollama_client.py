import json
from pathlib import Path

from hybrid_pipelines.infrastructure.ollama_client import OllamaClient, OllamaClientConfig


class StubResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"response": "{}", "done": True}


def test_structured_stages_enable_ollama_json_contract(monkeypatch, tmp_path: Path):
    payloads = []

    def fake_post(url, json, timeout):
        payloads.append(json)
        return StubResponse()

    monkeypatch.setattr("hybrid_pipelines.infrastructure.ollama_client.requests.post", fake_post)
    client = OllamaClient(
        OllamaClientConfig(
            url="http://ollama:11434",
            jsonl_path=tmp_path / "responses.jsonl",
        )
    )

    client.generate("system", "prompt", "entity_extraction")
    client.generate("system", "prompt", "candidate_disambiguation")
    client.generate("system", "prompt", "rdf_build")

    assert payloads[0]["format"] == "json"
    disambiguation_schema = payloads[1]["format"]
    assert disambiguation_schema["required"] == ["selections"]
    assert disambiguation_schema["additionalProperties"] is False
    selection_schema = disambiguation_schema["properties"]["selections"]
    assert selection_schema["minItems"] == 1
    assert selection_schema["items"]["required"] == ["mention_index", "selected_id"]
    assert selection_schema["items"]["additionalProperties"] is False
    assert selection_schema["items"]["properties"]["selected_id"]["pattern"] == "^Q[1-9][0-9]*$"
    assert payloads[1]["options"]["num_predict"] == 512
    rdf_schema = payloads[2]["format"]
    assert rdf_schema["required"] == ["rdf"]
    assert rdf_schema["additionalProperties"] is False
    assert rdf_schema["properties"]["rdf"] == {"type": "string", "minLength": 1}


def test_generation_respects_both_ollama_timeout_and_request_budget(monkeypatch, tmp_path):
    timeouts = []

    def fake_post(url, json, timeout):
        timeouts.append(timeout)
        return StubResponse()

    monkeypatch.setattr("hybrid_pipelines.infrastructure.ollama_client.requests.post", fake_post)
    client = OllamaClient(OllamaClientConfig(jsonl_path=tmp_path / "responses.jsonl", timeout_seconds=300))
    client.generate("system", "prompt", "rdf_build", timeout_seconds=540)
    client.generate("system", "prompt", "rdf_build", timeout_seconds=10)
    assert timeouts == [300, 10]


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
    assert client.generate("system", "prompt", "rdf_build") == "{}"
    assert client._logging_disabled
