from __future__ import annotations

from typing import Any, Protocol

import requests
from flask import Blueprint, jsonify, request

from ..application.services import RDFValidationError
from ..domain.models import AnalyzeRequest, AnalyzeResponse


class AnalyzeService(Protocol):
    def analyze(self, request: AnalyzeRequest) -> AnalyzeResponse: ...

    def health(self) -> dict[str, Any]: ...


def create_analyze_blueprint(service: AnalyzeService) -> Blueprint:
    blueprint = Blueprint("analyze", __name__)

    @blueprint.get("/health")
    def health() -> tuple:
        status = service.health()
        ok = all(part.get("status") == "ok" for part in status.values() if isinstance(part, dict))
        return jsonify(status), 200 if ok else 503

    @blueprint.post("/analyze")
    def analyze() -> tuple:
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "Request body must be a JSON object."}), 400
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            return jsonify({"error": "Field 'text' is required."}), 400
        idempotence_key = payload.get("idempotence_key")
        if idempotence_key is not None and not isinstance(idempotence_key, str):
            return jsonify({"error": "Field 'idempotence_key' must be a string."}), 400
        max_rdf_attempts = payload.get("max_rdf_attempts", 3)
        if not isinstance(max_rdf_attempts, int) or isinstance(max_rdf_attempts, bool):
            return jsonify({"error": "Field 'max_rdf_attempts' must be an integer."}), 400

        try:
            response = service.analyze(
                AnalyzeRequest(
                    text=text.strip(),
                    idempotence_key=idempotence_key,
                    max_rdf_attempts=max_rdf_attempts,
                    max_processing_seconds=payload.get("max_processing_seconds"),
                )
            )
        except requests.Timeout as exc:
            return (
                jsonify(
                    {
                        "error": "External service request timed out.",
                        "details": str(exc),
                        "hint": (
                            "Increase OLLAMA_TIMEOUT_SECONDS or reduce OLLAMA_NUM_PREDICT "
                            "if the timeout is from Ollama."
                        ),
                    }
                ),
                504,
            )
        except requests.RequestException as exc:
            return jsonify({"error": "External service request failed.", "details": str(exc)}), 502
        except RDFValidationError as exc:
            return (
                jsonify(
                    {
                        "error": "RDF parsing failed.",
                        "attempts": exc.attempts,
                        "details": exc.last_error,
                    }
                ),
                422,
            )
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 502
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        return jsonify(response.to_dict()), 200

    return blueprint
