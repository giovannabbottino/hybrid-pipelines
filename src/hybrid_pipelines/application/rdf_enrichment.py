from __future__ import annotations

import re

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF, RDFS

from ..domain.models import EntityMention, WikidataEntity, WikidataRelationship
from ..domain.structured_rdf import KG, WD


def remove_unresolved_resources(
    graph: Graph,
    entities: list[WikidataEntity],
    relationships: list[WikidataRelationship],
) -> None:
    """Remove triples referencing Wikidata QIDs absent from the supplied evidence."""
    allowed_qids = {entity.id for entity in entities if entity.id and re.fullmatch(r"Q\d+", entity.id)}
    allowed_qids.update(
        entity_id
        for relationship in relationships
        for entity_id in (relationship.subject_id, relationship.object_id)
        if re.fullmatch(r"Q\d+", entity_id)
    )
    _remove_unresolved_wikidata_resources(graph, allowed_qids, WD)


def materialize_relationships(graph: Graph, relationships: list[WikidataRelationship]) -> None:
    """Add each direct Wikidata relationship with the pipeline predicate mapping."""
    for relationship in relationships:
        if re.fullmatch(r"Q\d+", relationship.subject_id) and re.fullmatch(r"Q\d+", relationship.object_id):
            graph.add(
                (
                    WD[relationship.subject_id],
                    _relationship_predicate(relationship, KG),
                    WD[relationship.object_id],
                )
            )


def ensure_resource_labels(
    graph: Graph,
    entities: list[WikidataEntity],
    relationships: list[WikidataRelationship],
) -> None:
    """Prefer mention labels and fill missing labels for graph resources."""
    graph.bind("rdfs", RDFS)
    graph.bind("wd", WD)
    graph.bind("kg", KG)
    known_labels: dict[str, str] = {}
    for entity in entities:
        if entity.id:
            # The mention is the human-readable form present in the input text.
            # Prefer it over Wikidata's canonical label so label-anchored queries
            # can find the generated graph using the wording the user supplied.
            text_label = _human_readable_label(entity.mention.surface)
            canonical_label = _human_readable_label(entity.label)
            if text_label or canonical_label:
                known_labels[entity.id] = text_label or canonical_label
                graph.add(
                    (
                        WD[entity.id],
                        RDFS.label,
                        Literal(text_label or canonical_label, lang="en"),
                    )
                )
    for relationship in relationships:
        subject_label = _human_readable_label(relationship.subject_label)
        object_label = _human_readable_label(relationship.object_label)
        if subject_label:
            known_labels.setdefault(relationship.subject_id, subject_label)
        if object_label:
            known_labels.setdefault(relationship.object_id, object_label)

    predicates = {predicate for _, predicate, _ in graph}
    resources = {node for node in graph.subjects() if isinstance(node, URIRef)}
    resources.update(node for node in graph.objects() if isinstance(node, URIRef))
    for resource in resources:
        if resource in predicates or resource in {RDF.type, RDFS.label}:
            continue
        label = _resource_label(resource, known_labels)
        existing_labels = list(graph.objects(resource, RDFS.label))
        for existing in existing_labels:
            if not _human_readable_label(str(existing)):
                graph.remove((resource, RDFS.label, existing))
        existing_labels = list(graph.objects(resource, RDFS.label))
        if not label or any(str(existing).casefold() == label.casefold() for existing in existing_labels):
            continue
        graph.add((resource, RDFS.label, Literal(label, lang="en")))


def _remove_unresolved_wikidata_resources(
    graph: Graph,
    allowed_qids: set[str],
    wd: Namespace,
) -> None:
    namespace = str(wd)
    for triple in list(graph):
        subject, predicate, object_ = triple
        wikidata_nodes = [
            node
            for node in (subject, predicate, object_)
            if isinstance(node, URIRef) and str(node).startswith(namespace)
        ]
        if any(str(node).removeprefix(namespace) not in allowed_qids for node in wikidata_nodes):
            graph.remove(triple)


def add_text_cooccurrence_relations(
    graph: Graph,
    text: str,
    entities: list[WikidataEntity],
    kg: Namespace = KG,
    wd: Namespace = WD,
) -> None:
    """Add text-local connectivity and directed class/type semantics."""
    segments = _discourse_segments(text)
    entities_by_segment: dict[int, list[tuple[URIRef, WikidataEntity]]] = {}
    for entity in entities:
        if not entity.id or not re.fullmatch(r"Q\d+", entity.id):
            continue
        start = entity.mention.start
        if start is None:
            continue
        segment_index = next(
            (
                index
                for index, (segment_start, segment_end) in enumerate(segments)
                if segment_start <= start < segment_end
            ),
            None,
        )
        if segment_index is None:
            continue
        resource = wd[entity.id]
        segment_entities = entities_by_segment.setdefault(segment_index, [])
        if not any(existing_resource == resource for existing_resource, _ in segment_entities):
            segment_entities.append((resource, entity))

    for segment_entities in entities_by_segment.values():
        for left_index, (left, left_entity) in enumerate(segment_entities):
            for right, right_entity in segment_entities[left_index + 1 :]:
                graph.add((left, kg.related_to, right))
                graph.add((right, kg.related_to, left))
                if _is_classlike_mention(right_entity.mention):
                    graph.add((left, kg["is"], right))
                if _is_classlike_mention(left_entity.mention):
                    graph.add((right, kg["is"], left))


def _is_classlike_mention(mention: EntityMention) -> bool:
    entity_type = str(mention.entity_type or "").casefold()
    return any(marker in entity_type for marker in ("class", "concept", "object", "nationality"))


def _discourse_segments(text: str) -> list[tuple[int, int]]:
    boundaries = [0]
    boundaries.extend(match.end() for match in re.finditer(r"(?<=[.!?])\s+(?=[A-Z])", text))
    boundaries.append(len(text))
    sentence_spans = [
        (boundaries[index], boundaries[index + 1])
        for index in range(len(boundaries) - 1)
        if text[boundaries[index] : boundaries[index + 1]].strip()
    ]
    continuation = re.compile(
        r"^(?:although|until|he|she|it|they|this|these|those|whose|which|the\s+(?:boat|brand|company))\b",
        flags=re.IGNORECASE,
    )
    segments: list[tuple[int, int]] = []
    for sentence_start, sentence_end in sentence_spans:
        sentence_text = text[sentence_start:sentence_end].strip()
        if segments and continuation.match(sentence_text):
            segments[-1] = (segments[-1][0], sentence_end)
        else:
            segments.append((sentence_start, sentence_end))
    return segments


def _resource_label(resource: URIRef, known_labels: dict[str, str]) -> str:
    value = str(resource)
    qid_match = re.search(r"(?:^|/)(Q\d+)$", value)
    if qid_match:
        qid = qid_match.group(1)
        return known_labels.get(qid, "")
    local_name = re.split(r"[/#]", value.rstrip("/#"))[-1]
    return _human_readable_label(re.sub(r"[_-]+", " ", local_name))


def _human_readable_label(value: str | None) -> str:
    label = str(value or "").strip()
    return "" if re.fullmatch(r"(?:wd:)?Q\d+", label, flags=re.IGNORECASE) else label


def _relationship_predicate(
    relationship: WikidataRelationship,
    kg: Namespace,
) -> URIRef:
    label = relationship.property_label.casefold().strip()
    predicate_names = {
        "instance of": "is",
        "subclass of": "is",
        "part of": "part_of",
        "has part": "has_part",
        "country": "located_in",
        "location": "located_in",
    }
    local_name = predicate_names.get(label)
    if not local_name:
        local_name = re.sub(r"[^a-z0-9]+", "_", label).strip("_") or "related_to"
    return kg[local_name]
