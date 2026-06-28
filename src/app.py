from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask

from .application.services import HybridAgentService
from .controllers.analyze_controller import create_analyze_blueprint
from .infrastructure import (
    OllamaClient,
    OllamaClientConfig,
    PromptRepository,
    RequestLogger,
    WikidataMCPClient,
    WikidataMCPConfig,
)


def create_app() -> Flask:
    load_dotenv()
    app = Flask(__name__)

    llm = OllamaClient(OllamaClientConfig.from_env())
    wikidata = WikidataMCPClient(WikidataMCPConfig.from_env())
    prompt_repository = PromptRepository()

    analyze_log_path = os.getenv("ANALYZE_LOG_PATH", "data/analyze_log.jsonl")
    request_logger = RequestLogger(Path(analyze_log_path)) if analyze_log_path else None
    candidate_limit = _int_env("WIKIDATA_CANDIDATE_LIMIT", 3)

    service = HybridAgentService(
        llm=llm,
        wikidata=wikidata,
        prompt_repository=prompt_repository,
        system_prompt_name=os.getenv("SYSTEM_PROMPT_NAME", "system/agent.txt"),
        entity_prompt_name=os.getenv("ENTITY_EXTRACTION_PROMPT_NAME", "prompts/entity-extraction.txt"),
        rdf_prompt_name=os.getenv("RDF_BUILD_PROMPT_NAME", "prompts/rdf-build.txt"),
        request_logger=request_logger,
        candidate_limit=candidate_limit,
    )
    app.register_blueprint(create_analyze_blueprint(service))
    return app


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value in (None, ""):
        return default
    try:
        return int(value)
    except ValueError:
        return default


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=5050, debug=True)
