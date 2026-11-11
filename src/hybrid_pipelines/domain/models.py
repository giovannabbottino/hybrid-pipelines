from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AnalyzeRequest:
    text: str
    idempotence_key: str | None = None
    max_processing_seconds: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("Field 'text' is required.")
        if self.idempotence_key is not None and not isinstance(self.idempotence_key, str):
            raise ValueError("Field 'idempotence_key' must be a string.")
        object.__setattr__(self, "text", self.text.strip())
        timeout = self.max_processing_seconds
        if timeout is not None:
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
                raise ValueError("Field 'max_processing_seconds' must be a positive finite number.")
            if not math.isfinite(timeout) or timeout <= 0:
                raise ValueError("Field 'max_processing_seconds' must be a positive finite number.")

    @classmethod
    def from_payload(cls, payload: Any) -> AnalyzeRequest:
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object.")
        if "max_rdf_attempts" in payload:
            raise ValueError("Field 'max_rdf_attempts' is not supported.")
        return cls(
            text=payload.get("text", ""),
            idempotence_key=payload.get("idempotence_key"),
            max_processing_seconds=payload.get("max_processing_seconds"),
        )


@dataclass(frozen=True)
class EntityMention:
    surface: str
    start: int
    end: int

    def to_dict(self) -> dict[str, Any]:
        return {"surface": self.surface, "start": self.start, "end": self.end}


@dataclass(frozen=True)
class WikidataEntity:
    mention: EntityMention
    id: str | None
    iri: str | None
    label: str

    def to_dict(self) -> dict[str, Any]:
        return {"mention": self.mention.to_dict(), "id": self.id, "iri": self.iri, "label": self.label}


@dataclass(frozen=True)
class AnalyzeResponse:
    text: str
    entities: list[WikidataEntity]
    rdf: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    source_attribution: str = "Source: Wikidata"

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "entities": [entity.to_dict() for entity in self.entities],
            "rdf": self.rdf,
            "tool_calls": self.tool_calls,
            "source_attribution": self.source_attribution,
        }
