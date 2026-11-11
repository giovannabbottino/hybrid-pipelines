from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

from ..configuration import float_env, int_env

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OllamaClientConfig:
    url: str = "http://localhost:11434"
    model: str = "llama3.1:8b"
    jsonl_path: Path = Path("logs/ollama_responses.jsonl")
    timeout_seconds: float = 300.0
    options: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> OllamaClientConfig:
        options: dict[str, Any] = {}
        for key, env_name, reader in (
            ("seed", "OLLAMA_SEED", int_env),
            ("temperature", "OLLAMA_TEMPERATURE", float_env),
            ("top_k", "OLLAMA_TOP_K", int_env),
            ("top_p", "OLLAMA_TOP_P", float_env),
            ("min_p", "OLLAMA_MIN_P", float_env),
            ("num_ctx", "OLLAMA_NUM_CTX", int_env),
            ("num_predict", "OLLAMA_NUM_PREDICT", int_env),
        ):
            value = reader(env_name)
            if value is not None:
                options[key] = value
        stop = os.getenv("OLLAMA_STOP")
        if stop:
            options["stop"] = stop

        return cls(
            url=os.getenv("OLLAMA_API_URL", "http://localhost:11434"),
            model=os.getenv("OLLAMA_MODEL", "llama3.1:8b"),
            jsonl_path=Path(os.getenv("OLLAMA_JSONL_PATH", "logs/ollama_responses.jsonl")),
            timeout_seconds=float_env("OLLAMA_TIMEOUT_SECONDS") or 300.0,
            options=options,
        )


class OllamaClient:
    def __init__(self, config: OllamaClientConfig):
        self.config = config
        self._logging_disabled = False

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        timeout_seconds: float,
        response_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Preserve native tool calls and the complete conversation for the MCP host."""
        base_url = self.config.url.rstrip("/").removesuffix("/api/generate").removesuffix("/api/chat")
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "tools": tools,
            "stream": False,
        }
        if response_schema is not None:
            payload["format"] = response_schema
        if self.config.options:
            payload["options"] = dict(self.config.options)
        response = requests.post(
            f"{base_url}/api/chat",
            json=payload,
            timeout=min(timeout_seconds, self.config.timeout_seconds),
        )
        response.raise_for_status()
        data = response.json()
        message = data.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            raise RuntimeError("Ollama returned no assistant message for MCP tool calling.")
        self._log("mcp_agent", json.dumps(messages, ensure_ascii=False), {**data, "response": message})
        return message

    def health_check(self) -> dict[str, Any]:
        try:
            base_url = self.config.url.rstrip("/").removesuffix("/api/generate").removesuffix("/api/chat")
            response = requests.get(f"{base_url}/api/tags", timeout=5)
            response.raise_for_status()
            return {"status": "ok", "model": self.config.model}
        except requests.RequestException as exc:
            return {"status": "unavailable", "details": str(exc)}

    def _log(self, stage: str, prompt: str, response: dict[str, Any]) -> None:
        if self._logging_disabled:
            return
        try:
            self.config.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "stage": stage,
                "model": response.get("model", self.config.model),
                "prompt": prompt,
                "response": response.get("response"),
                "created_at": response.get("created_at"),
                "done": response.get("done"),
                "total_duration": response.get("total_duration"),
            }
            with self.config.jsonl_path.open("a", encoding="utf-8") as fp:
                fp.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except (OSError, TypeError, ValueError) as exc:
            self._logging_disabled = True
            logger.warning("Disabling Ollama JSONL logging after write failure: %s", exc)
