from __future__ import annotations

import csv
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)


def _int_env(name: str) -> int | None:
    value = os.getenv(name)
    if value in (None, ""):
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _float_env(name: str) -> float | None:
    value = os.getenv(name)
    if value in (None, ""):
        return None
    try:
        return float(value)
    except ValueError:
        return None


@dataclass(frozen=True)
class OllamaClientConfig:
    url: str = "http://localhost:11434"
    model: str = "llama3:8b"
    csv_path: Path = Path("data/ollama_responses.csv")
    timeout_seconds: float = 300.0
    options: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "OllamaClientConfig":
        options: dict[str, Any] = {}
        for key, env_name, reader in (
            ("seed", "OLLAMA_SEED", _int_env),
            ("temperature", "OLLAMA_TEMPERATURE", _float_env),
            ("top_k", "OLLAMA_TOP_K", _int_env),
            ("top_p", "OLLAMA_TOP_P", _float_env),
            ("min_p", "OLLAMA_MIN_P", _float_env),
            ("num_ctx", "OLLAMA_NUM_CTX", _int_env),
            ("num_predict", "OLLAMA_NUM_PREDICT", _int_env),
        ):
            value = reader(env_name)
            if value is not None:
                options[key] = value
        stop = os.getenv("OLLAMA_STOP")
        if stop:
            options["stop"] = stop

        return cls(
            url=os.getenv("OLLAMA_API_URL", "http://localhost:11434"),
            model=os.getenv("OLLAMA_MODEL", "llama3:8b"),
            csv_path=Path(os.getenv("OLLAMA_CSV_PATH", "data/ollama_responses.csv")),
            timeout_seconds=_float_env("OLLAMA_TIMEOUT_SECONDS") or 300.0,
            options=options,
        )


class OllamaClient:
    def __init__(self, config: OllamaClientConfig):
        self.config = config
        self._logging_disabled = False

    def generate(self, system_prompt: str, prompt: str, stage: str) -> str:
        base_url = self.config.url.rstrip("/")
        target_url = base_url if base_url.endswith("/api/generate") else f"{base_url}/api/generate"
        payload: dict[str, Any] = {
            "model": self.config.model,
            "system": system_prompt,
            "prompt": prompt,
            "stream": False,
        }
        if self.config.options:
            payload["options"] = self.config.options

        response = requests.post(target_url, json=payload, timeout=self.config.timeout_seconds)
        response.raise_for_status()
        data = response.json()
        text = str(data.get("response") or "")
        self._log(stage=stage, prompt=prompt, response=data)
        return text

    def health_check(self) -> dict[str, Any]:
        try:
            response = requests.get(f"{self.config.url.rstrip('/')}/api/tags", timeout=5)
            response.raise_for_status()
            return {"status": "ok", "model": self.config.model}
        except requests.RequestException as exc:
            return {"status": "unavailable", "details": str(exc)}

    def _log(self, stage: str, prompt: str, response: dict[str, Any]) -> None:
        if self._logging_disabled:
            return
        try:
            self.config.csv_path.parent.mkdir(parents=True, exist_ok=True)
            write_header = not self.config.csv_path.exists()
            with self.config.csv_path.open("a", encoding="utf-8", newline="") as fp:
                writer = csv.DictWriter(
                    fp,
                    fieldnames=[
                        "stage",
                        "model",
                        "prompt",
                        "response",
                        "created_at",
                        "done",
                        "total_duration",
                    ],
                )
                if write_header:
                    writer.writeheader()
                writer.writerow(
                    {
                        "stage": stage,
                        "model": response.get("model", self.config.model),
                        "prompt": prompt,
                        "response": response.get("response"),
                        "created_at": response.get("created_at"),
                        "done": response.get("done"),
                        "total_duration": json.dumps(response.get("total_duration")),
                    }
                )
        except OSError as exc:
            self._logging_disabled = True
            logger.warning("Disabling Ollama CSV logging after write failure: %s", exc)
