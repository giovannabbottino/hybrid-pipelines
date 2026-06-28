from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AnalyzeRequest:
    text: str
    idempotence_key: str | None = None


@dataclass(frozen=True)
class EntityMention:
    surface: str
    start: int | None = None
    end: int | None = None
    entity_type: str | None = None
    confidence: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "surface": self.surface,
            "start": self.start,
            "end": self.end,
            "entity_type": self.entity_type,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class WikidataEntity:
    mention: EntityMention
    id: str | None
    iri: str | None
    label: str
    description: str | None = None
    score: float | None = None
    statements: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mention": self.mention.to_dict(),
            "id": self.id,
            "iri": self.iri,
            "label": self.label,
            "description": self.description,
            "score": self.score,
            "statements": self.statements,
        }


@dataclass(frozen=True)
class WikidataRelationship:
    subject_id: str
    subject_label: str
    property_id: str
    property_label: str
    object_id: str
    object_label: str
    source: str = "wikidata"

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject_id": self.subject_id,
            "subject_label": self.subject_label,
            "property_id": self.property_id,
            "property_label": self.property_label,
            "object_id": self.object_id,
            "object_label": self.object_label,
            "source": self.source,
        }


@dataclass(frozen=True)
class AnalyzeResponse:
    text: str
    entities: list[WikidataEntity]
    relationships: list[WikidataRelationship]
    rdf: str
    source_attribution: str = "Source: Wikidata"
    llm: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "entities": [entity.to_dict() for entity in self.entities],
            "relationships": [relationship.to_dict() for relationship in self.relationships],
            "rdf": self.rdf,
            "source_attribution": self.source_attribution,
            "llm": self.llm,
        }
