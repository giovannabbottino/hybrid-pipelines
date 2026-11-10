from __future__ import annotations

import json
import re
from typing import Any


def parse_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as original_error:
        fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", cleaned, flags=re.IGNORECASE | re.DOTALL)
        if fenced:
            payload = json.loads(fenced.group(1))
        else:
            if cleaned.startswith("{"):
                raise original_error
            decoder = json.JSONDecoder()
            for match in re.finditer(r"\{", cleaned):
                try:
                    payload, _ = decoder.raw_decode(cleaned[match.start() :])
                    break
                except json.JSONDecodeError:
                    continue
            else:
                raise original_error
    if not isinstance(payload, dict):
        raise ValueError("The LLM response must contain a JSON object.")
    return payload


def parse_entity_response(text: str) -> dict[str, Any]:
    payload = parse_json_object(text)
    if not isinstance(payload.get("entities"), list):
        raise ValueError("The LLM entity response must contain an entities array.")
    return payload


def optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
