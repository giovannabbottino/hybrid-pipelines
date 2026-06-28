from __future__ import annotations

import requests
from flask import Blueprint, jsonify, request

from ..application.services import HybridAgentService
from ..domain.models import AnalyzeRequest


def create_analyze_blueprint(service: HybridAgentService) -> Blueprint:
    blueprint = Blueprint("analyze", __name__)

    @blueprint.get("/health")
    def health() -> tuple:
        status = service.health()
        ok = all(part.get("status") == "ok" for part in status.values() if isinstance(part, dict))
        return jsonify(status), 200 if ok else 503

    @blueprint.post("/analyze")
    def analyze() -> tuple:
        payload = request.get_json(silent=True) or {}
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            return jsonify({"error": "Field 'text' is required."}), 400

        try:
            response = service.analyze(
                AnalyzeRequest(
                    text=text.strip(),
                    idempotence_key=payload.get("idempotence_key"),
                )
            )
        except requests.Timeout as exc:
            return (
                jsonify(
                    {
                        "error": "External service request timed out.",
                        "details": str(exc),
                        "hint": "Increase OLLAMA_TIMEOUT_SECONDS or reduce OLLAMA_NUM_PREDICT if the timeout is from Ollama.",
                    }
                ),
                504,
            )
        except requests.RequestException as exc:
            return jsonify({"error": "External service request failed.", "details": str(exc)}), 502
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 502
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        return jsonify(response.to_dict()), 200

    return blueprint
