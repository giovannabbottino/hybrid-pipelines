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
    ned_prompt_name: str = "prompts/ned.txt"
    rdf_prompt_name: str = "prompts/rdf-build.txt"
    analyze_log_path: str = "logs/analyze_log.jsonl"
    analyze_timeout_seconds: float = 540.0
    max_turns: int = 20
    max_tool_calls: int = 40

    @classmethod
    def from_env(cls) -> PipelineSettings:
        return cls(
            system_prompt_name=os.getenv("SYSTEM_PROMPT_NAME", cls.system_prompt_name),
            ned_prompt_name=os.getenv("NED_PROMPT_NAME", cls.ned_prompt_name),
            rdf_prompt_name=os.getenv("RDF_BUILD_PROMPT_NAME", cls.rdf_prompt_name),
            analyze_log_path=os.getenv("ANALYZE_LOG_PATH", cls.analyze_log_path),
            analyze_timeout_seconds=max(1.0, float_env("ANALYZE_TIMEOUT_SECONDS", 540.0)),
            max_turns=max(1, int_env("MCP_MAX_TURNS", 20)),
            max_tool_calls=max(1, int_env("MCP_MAX_TOOL_CALLS", 40)),
        )
