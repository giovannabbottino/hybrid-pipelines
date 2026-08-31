import json

from hybrid_pipelines.infrastructure.request_logger import RequestLogger


def test_request_logger_writes_correlated_jsonl_event(tmp_path):
    log_path = tmp_path / "analyze.jsonl"
    logger = RequestLogger(log_path)

    logger.log("request-123", "rdf_validated", {"attempt": 2})

    entry = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert entry["idempotence_key"] == "request-123"
    assert entry["event"] == "rdf_validated"
    assert entry["payload"] == {"attempt": 2}
    assert entry["timestamp"].endswith("+00:00")
