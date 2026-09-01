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
            csv_path=tmp_path / "responses.csv",
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
    assert "format" not in payloads[2]
