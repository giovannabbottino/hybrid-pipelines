from __future__ import annotations

from typing import Any

from ..domain.models import WikidataCandidateGroup
from .response_parsing import optional_int, parse_json_object


def disambiguation_evidence(group: WikidataCandidateGroup) -> dict[str, Any]:
    return {
        "mention": {
            "surface": group.mention.surface,
            "entity_type": group.mention.entity_type,
        },
        "candidates": [
            {
                "id": candidate.id,
                "label": candidate.label,
                "description": candidate.description,
                "types": [
                    {
                        "relation": statement.get("property_label"),
                        "label": statement.get("object_label"),
                        "id": statement.get("object_id"),
                    }
                    for statement in candidate.statements[:2]
                    if statement.get("property_id") in {"P31", "P279"}
                ],
            }
            for candidate in group.candidates
        ],
    }


def validate_selections(
    raw: str,
    groups: list[WikidataCandidateGroup],
    pending_indices: set[int],
) -> dict[int, str]:
    result = parse_json_object(raw)
    items = result.get("selections")
    if not isinstance(items, list):
        raise ValueError("The LLM disambiguation response must contain a selections array.")

    selections: dict[int, str] = {}
    for selection in items:
        if not isinstance(selection, dict):
            raise ValueError("Every disambiguation selection must be a JSON object.")
        if set(selection) != {"mention_index", "selected_id"}:
            raise ValueError("Every disambiguation selection must contain only mention_index and selected_id.")
        mention_index = optional_int(selection.get("mention_index"))
        selected_id = selection.get("selected_id")
        if mention_index is None or not isinstance(selected_id, str):
            raise ValueError("Every disambiguation selection must contain a valid index and QID string.")
        if mention_index not in pending_indices:
            raise ValueError(f"The LLM returned an unexpected selection for mention {mention_index}.")
        if mention_index in selections:
            raise ValueError(f"The LLM returned duplicate selections for mention {mention_index}.")
        if not any(candidate.id == selected_id for candidate in groups[mention_index].candidates):
            allowed = ", ".join(candidate.id or "" for candidate in groups[mention_index].candidates)
            raise ValueError(
                f"The LLM selected an invalid Wikidata ID for mention {mention_index}; allowed IDs: {allowed}."
            )
        selections[mention_index] = selected_id
    return selections


def candidate_group_evidence(group: WikidataCandidateGroup) -> dict[str, Any]:
    return {
        "mention": group.mention.to_dict(),
        "candidates": [
            {
                "id": candidate.id,
                "label": candidate.label,
                "description": candidate.description,
                "score": candidate.score,
                "type_statements": [
                    statement
                    for statement in candidate.statements
                    if str(statement.get("property_id") or statement.get("property") or "") in {"P31", "P279"}
                ][:4],
            }
            for candidate in group.candidates
        ],
    }
