from hybrid_pipelines.domain.models import EntityMention
from hybrid_pipelines.infrastructure.wikidata_client import (
    WikidataMCPClient,
    WikidataMCPConfig,
    _choose_candidate,
)


def test_exact_label_tie_preserves_search_order_before_context_overlap():
    candidates = [
        {"id": "Q601401", "label": "trade", "description": "exchange of goods"},
        {"id": "QOTHER", "label": "trade", "description": "trade name terminology"},
    ]

    chosen = _choose_candidate(
        candidates,
        context="better known by its trade name",
        surface="trade",
    )

    assert chosen["id"] == "Q601401"


def test_resolve_entities_caches_statements_by_qid_and_skips_supplements():
    client = WikidataMCPClient(WikidataMCPConfig())
    client.search_items = lambda query, limit=5: [
        {"id": "Q1", "label": query, "description": "test"}
    ]
    calls = []

    def get_statements(entity_id):
        calls.append(entity_id)
        return [{"property_id": "P31", "object_id": "Q2"}]

    client.get_statements = get_statements
    mentions = [
        EntityMention(surface="Main", confidence=1.0),
        EntityMention(surface="Alias", confidence=1.0),
        EntityMention(surface="Concept", confidence=0.5),
    ]

    entities = client.resolve_entities(mentions, context="Main Alias Concept")

    assert calls == ["Q1"]
    assert entities[0].statements
    assert entities[1].statements
    assert entities[2].statements == []
