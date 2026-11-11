from __future__ import annotations

from typing import Any, Protocol

import httpx
import requests
from flask import Blueprint, jsonify, request
from mcp.shared.exceptions import McpError

from ..application.services import CandidateDisambiguationError, RDFValidationError
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
        try:
            analyze_request = AnalyzeRequest.from_payload(request.get_json(silent=True))
            response = service.analyze(analyze_request)
        except (requests.Timeout, httpx.TimeoutException, TimeoutError) as exc:
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
        except (requests.RequestException, httpx.HTTPError, McpError) as exc:
            return jsonify({"error": "External service request failed.", "details": str(exc)}), 502
        except CandidateDisambiguationError as exc:
            return (
                jsonify(
                    {
                        "error": "Candidate disambiguation failed.",
                        "attempts": exc.attempts,
                        "details": exc.last_error,
                    }
                ),
                422,
            )
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
