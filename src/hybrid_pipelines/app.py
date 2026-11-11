from __future__ import annotations

from dotenv import load_dotenv
from flask import Flask

from .application.services import HybridAgentService
from .configuration import PipelineSettings
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

    settings = PipelineSettings.from_env()
    request_logger = RequestLogger(settings.analyze_log_path)

    service = HybridAgentService(
        llm=llm,
        wikidata=wikidata,
        prompt_repository=prompt_repository,
        system_prompt_name=settings.system_prompt_name,
        ned_prompt_name=settings.ned_prompt_name,
        rdf_prompt_name=settings.rdf_prompt_name,
        request_logger=request_logger,
        analyze_timeout_seconds=settings.analyze_timeout_seconds,
        max_turns=settings.max_turns,
        max_tool_calls=settings.max_tool_calls,
    )
    app.register_blueprint(create_analyze_blueprint(service))
    return app


def main() -> None:
    create_app().run(host="127.0.0.1", port=5050, debug=True)


if __name__ == "__main__":
    main()
