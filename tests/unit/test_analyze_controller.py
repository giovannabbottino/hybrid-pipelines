from flask import Flask

from hybrid_pipelines.application.services import RDFValidationError
from hybrid_pipelines.controllers.analyze_controller import create_analyze_blueprint
from hybrid_pipelines.domain.models import AnalyzeRequest, AnalyzeResponse


class FailingRDFService:
    def analyze(self, request: AnalyzeRequest) -> AnalyzeResponse:
        raise RDFValidationError(
            "RDF parsing failed.",
            attempts=request.max_rdf_attempts,
            last_error="at line 12 of <>: invalid Turtle",
        )

    def health(self) -> dict:
        return {"llm": {"status": "ok"}, "wikidata_mcp": {"status": "ok"}}


def _client():
    app = Flask(__name__)
    app.register_blueprint(create_analyze_blueprint(FailingRDFService()))
    return app.test_client()


def test_returns_422_after_rdf_attempts_are_exhausted() -> None:
    response = _client().post(
        "/analyze",
        json={"text": "Mango is a fruit.", "max_rdf_attempts": 3},
    )

    assert response.status_code == 422
    assert response.get_json() == {
        "error": "RDF parsing failed.",
        "attempts": 3,
        "details": "at line 12 of <>: invalid Turtle",
    }


def test_rejects_non_integer_rdf_attempt_count() -> None:
    response = _client().post(
        "/analyze",
        json={"text": "Mango is a fruit.", "max_rdf_attempts": "3"},
    )

    assert response.status_code == 400
    assert response.get_json() == {
        "error": "Field 'max_rdf_attempts' must be an integer."
    }


def test_rejects_non_string_idempotence_key() -> None:
    response = _client().post(
        "/analyze",
        json={"text": "Mango is a fruit.", "idempotence_key": 123},
    )

    assert response.status_code == 400
    assert response.get_json() == {
        "error": "Field 'idempotence_key' must be a string."
    }


def test_rejects_non_object_json_body() -> None:
    response = _client().post("/analyze", json=["not", "an", "object"])

    assert response.status_code == 400
    assert response.get_json() == {"error": "Request body must be a JSON object."}
