import pytest
from flask import Flask

from hybrid_pipelines.application.services import CandidateDisambiguationError, RDFValidationError
from hybrid_pipelines.controllers.analyze_controller import create_analyze_blueprint
from hybrid_pipelines.domain.models import AnalyzeRequest, AnalyzeResponse


class FailingRDFService:
    def analyze(self, request: AnalyzeRequest) -> AnalyzeResponse:
        raise RDFValidationError(
            "RDF parsing failed.",
            attempts=3,
            last_error="at line 12 of <>: invalid Turtle",
        )

    def health(self) -> dict:
        return {"llm": {"status": "ok"}, "wikidata_mcp": {"status": "ok"}}


class FailingDisambiguationService:
    def analyze(self, request: AnalyzeRequest) -> AnalyzeResponse:
        raise CandidateDisambiguationError(
            "Candidate disambiguation failed.",
            attempts=3,
            last_error="Missing selections for mention indices: 2",
        )

    def health(self) -> dict:
        return {"llm": {"status": "ok"}, "wikidata_mcp": {"status": "ok"}}


def _client(service=None):
    app = Flask(__name__)
    app.register_blueprint(create_analyze_blueprint(service or FailingRDFService()))
    return app.test_client()


def test_returns_422_after_rdf_attempts_are_exhausted() -> None:
    response = _client().post(
        "/analyze",
        json={"text": "Mango is a fruit."},
    )

    assert response.status_code == 422
    assert response.get_json() == {
        "error": "RDF parsing failed.",
        "attempts": 3,
        "details": "at line 12 of <>: invalid Turtle",
    }


def test_returns_422_after_disambiguation_attempts_are_exhausted() -> None:
    response = _client(FailingDisambiguationService()).post(
        "/analyze",
        json={"text": "Jaguar is a brand."},
    )

    assert response.status_code == 422
    assert response.get_json() == {
        "error": "Candidate disambiguation failed.",
        "attempts": 3,
        "details": "Missing selections for mention indices: 2",
    }


@pytest.mark.parametrize("value", [1, 3, "3", None, True])
def test_rejects_removed_rdf_attempt_field(value) -> None:
    response = _client().post(
        "/analyze",
        json={"text": "Mango is a fruit.", "max_rdf_attempts": value},
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "Field 'max_rdf_attempts' is not supported."}


def test_rejects_non_string_idempotence_key() -> None:
    response = _client().post(
        "/analyze",
        json={"text": "Mango is a fruit.", "idempotence_key": 123},
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "Field 'idempotence_key' must be a string."}


def test_rejects_non_object_json_body() -> None:
    response = _client().post("/analyze", json=["not", "an", "object"])

    assert response.status_code == 400
    assert response.get_json() == {"error": "Request body must be a JSON object."}


@pytest.mark.parametrize("timeout", ["10", True, 0, -1, [], {}, float("nan"), float("inf")])
def test_rejects_invalid_processing_budget(timeout) -> None:
    response = _client().post("/analyze", json={"text": "Mango.", "max_processing_seconds": timeout})
    assert response.status_code == 400
    assert response.get_json() == {"error": "Field 'max_processing_seconds' must be a positive finite number."}


def test_success_returns_ned_entities_rdf_and_mcp_trace():
    class SuccessService(FailingRDFService):
        def analyze(self, request: AnalyzeRequest) -> AnalyzeResponse:
            from hybrid_pipelines.domain.models import EntityMention, WikidataEntity

            return AnalyzeResponse(
                text=request.text,
                entities=[
                    WikidataEntity(EntityMention("Adams", 0, 5), "Q42", "http://www.wikidata.org/entity/Q42", "Adams")
                ],
                rdf="example Turtle",
                tool_calls=[{"name": "search_items", "arguments": {"query": "Adams"}, "result": {}}],
            )

    response = _client(SuccessService()).post("/analyze", json={"text": "Adams"})
    assert response.status_code == 200
    body = response.get_json()
    assert body["entities"][0]["id"] == "Q42"
    assert body["tool_calls"][0]["name"] == "search_items"
    assert set(body) == {"text", "entities", "rdf", "tool_calls", "source_attribution"}


@pytest.mark.parametrize("error,status", [(TimeoutError("budget"), 504), (RuntimeError("tool limit"), 502)])
def test_execution_errors_have_http_status(error, status):
    class ErrorService(FailingRDFService):
        def analyze(self, request: AnalyzeRequest) -> AnalyzeResponse:
            raise error

    response = _client(ErrorService()).post("/analyze", json={"text": "Adams"})
    assert response.status_code == status
