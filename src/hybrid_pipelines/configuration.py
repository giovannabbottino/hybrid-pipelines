"""Shared environment parsing and application settings."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import overload


@overload
def int_env(name: str, default: int) -> int: ...
@overload
def int_env(name: str, default: None = None) -> int | None: ...


def int_env(name: str, default: int | None = None) -> int | None:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


@overload
def float_env(name: str, default: float) -> float: ...
@overload
def float_env(name: str, default: None = None) -> float | None: ...


def float_env(name: str, default: float | None = None) -> float | None:
    try:
        value = float(os.environ[name])
    except (KeyError, ValueError):
        return default
    return value if math.isfinite(value) else default


@dataclass(frozen=True)
class PipelineSettings:
    system_prompt_name: str = "system/agent.txt"
    entity_prompt_name: str = "prompts/entity-extraction.txt"
    disambiguation_prompt_name: str = "prompts/candidate-disambiguation.txt"
    rdf_prompt_name: str = "prompts/rdf-build.txt"
    analyze_log_path: str = "logs/analyze_log.jsonl"
    candidate_limit: int = 3
    analyze_timeout_seconds: float = 540.0
    mention_limit: int = 10

    @classmethod
    def from_env(cls) -> PipelineSettings:
        return cls(
            system_prompt_name=os.getenv("SYSTEM_PROMPT_NAME", "system/agent.txt"),
            entity_prompt_name=os.getenv("ENTITY_EXTRACTION_PROMPT_NAME", "prompts/entity-extraction.txt"),
            disambiguation_prompt_name=os.getenv(
                "CANDIDATE_DISAMBIGUATION_PROMPT_NAME",
                "prompts/candidate-disambiguation.txt",
            ),
            rdf_prompt_name=os.getenv("RDF_BUILD_PROMPT_NAME", "prompts/rdf-build.txt"),
            analyze_log_path=os.getenv("ANALYZE_LOG_PATH", "logs/analyze_log.jsonl"),
            candidate_limit=max(1, int_env("WIKIDATA_CANDIDATE_LIMIT", 3)),
            analyze_timeout_seconds=max(1.0, float_env("ANALYZE_TIMEOUT_SECONDS", 540.0)),
            mention_limit=max(1, int_env("ENTITY_MENTION_LIMIT", 10)),
        )
