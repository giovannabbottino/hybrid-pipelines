from pathlib import Path

from hybrid_pipelines.infrastructure.ollama_client import OllamaClient, OllamaClientConfig


class StubResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"response": "{}", "done": True}


def test_structured_stages_enable_ollama_json_mode(monkeypatch, tmp_path: Path):
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
    assert payloads[1]["format"] == "json"
    assert payloads[1]["options"]["num_predict"] == 512
    assert "format" not in payloads[2]
