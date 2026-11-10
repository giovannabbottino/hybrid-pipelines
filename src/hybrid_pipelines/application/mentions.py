from __future__ import annotations

import re
from typing import Any

from ..domain.models import EntityMention
from .response_parsing import optional_float, optional_int


def mention_from_item(item: dict[str, Any]) -> EntityMention:
    return EntityMention(
        surface=str(item.get("surface") or "").strip(),
        start=optional_int(item.get("start")),
        end=optional_int(item.get("end")),
        entity_type=str(item.get("entity_type") or item.get("label") or "Entity"),
        confidence=optional_float(item.get("confidence")),
    )


_DESCRIPTOR_HEADS = (
    "brand|car|vehicle|boat|ship|company|corporation|label|name|"
    "musician|singer-songwriter|language|service|services|outlet|"
    "fashion|line|lines|range"
)


def supplement_mentions(
    text: str,
    mentions: list[EntityMention],
    limit: int,
) -> list[EntityMention]:
    """Fill omitted relation/class concepts without inventing text spans."""
    supplemental: list[EntityMention] = []
    descriptor_pattern = re.compile(
        rf"\b(?P<modifier>[A-Za-z][A-Za-z'-]*)\s+"
        rf"(?P<head>{_DESCRIPTOR_HEADS})\b",
        flags=re.IGNORECASE,
    )
    head_priority = {
        "name": 0,
        "label": 0,
        "car": 1,
        "boat": 1,
        "ship": 1,
    }
    descriptor_matches = sorted(
        descriptor_pattern.finditer(text),
        key=lambda match: (
            head_priority.get(match.group("head").casefold(), 2),
            match.start(),
        ),
    )
    ignored_modifiers = {
        "a",
        "an",
        "and",
        "as",
        "in",
        "its",
        "of",
        "on",
        "or",
        "the",
        "to",
    }
    for match in descriptor_matches:
        modifier = match.group("modifier")
        if len(modifier) < 2 or modifier.casefold() in ignored_modifiers:
            continue
        supplemental.append(
            EntityMention(
                surface=modifier,
                start=match.start("modifier"),
                end=match.end("modifier"),
                entity_type="Concept",
                confidence=0.5,
                source="lexical_rule",
            )
        )
        supplemental.append(
            EntityMention(
                surface=match.group(0),
                start=match.start(),
                end=match.end(),
                entity_type="Class",
                confidence=0.5,
                source="lexical_rule",
            )
        )

    for match in re.finditer(
        r"\b(?:type|class|model|series|no\.?)\s+(?P<value>\d+[A-Za-z]?)\b",
        text,
        flags=re.IGNORECASE,
    ):
        supplemental.append(
            EntityMention(
                surface=match.group("value"),
                start=match.start("value"),
                end=match.end("value"),
                entity_type="Concept",
                confidence=0.5,
                source="lexical_rule",
            )
        )

    return dedupe_mentions([*mentions, *supplemental])[: max(1, int(limit))]


def dedupe_mentions(mentions: list[EntityMention]) -> list[EntityMention]:
    seen: set[tuple[str, int | None, int | None]] = set()
    deduped: list[EntityMention] = []
    for mention in mentions:
        key = (mention.surface.casefold(), mention.start, mention.end)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(mention)
    return deduped


def realign_mentions(text: str, mentions: list[EntityMention]) -> list[EntityMention]:
    """Realign model-provided offsets to exact, successive text occurrences."""
    occupied_spans: list[tuple[int, int]] = []
    cursor = 0
    aligned: list[EntityMention] = []
    for mention in mentions:
        matches = list(re.finditer(re.escape(mention.surface), text, flags=re.IGNORECASE))
        available = [
            match
            for match in matches
            if not any(
                match.start() < occupied_end and match.end() > occupied_start
                for occupied_start, occupied_end in occupied_spans
            )
        ]
        if not available:
            continue
        match = next((item for item in available if item.start() >= cursor), available[0])
        occupied_spans.append((match.start(), match.end()))
        cursor = max(cursor, match.end())
        aligned.append(
            EntityMention(
                surface=mention.surface,
                start=match.start(),
                end=match.end(),
                entity_type=mention.entity_type,
                confidence=mention.confidence,
                source=mention.source,
            )
        )
    return aligned
