import requests

from hybrid_pipelines.domain.models import EntityMention, WikidataCandidateGroup, WikidataEntity
from hybrid_pipelines.infrastructure.wikidata_client import (
    WikidataMCPClient,
    WikidataMCPConfig,
    _choose_candidate,
    _coerce_items,
    _coerce_statements,
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


def test_mcp_parses_event_prefixed_sse_response(monkeypatch):
    client = WikidataMCPClient(WikidataMCPConfig())
    response = requests.Response()
    response.status_code = 200
    response._content = (
        b'event: message\n'
        b'data: {"jsonrpc":"2.0","id":1,"result":{"items":[]}}\n\n'
    )
    monkeypatch.setattr(client, "_request_with_retries", lambda *args, **kwargs: response)

    data, _ = client._post_jsonrpc({"jsonrpc": "2.0", "id": 1, "method": "tools/call"})

    assert data["result"] == {"items": []}


def test_mcp_text_results_are_parsed_into_entities_and_statements():
    items = _coerce_items("Q169: mango — fruit of the mango tree")
    statements = _coerce_statements(
        "mango (Q169): subclass of (P279): fruit (Q3314483)"
    )

    assert items == [
        {"id": "Q169", "label": "mango", "description": "fruit of the mango tree"}
    ]
    assert statements == [
        {
            "subject_label": "mango",
            "subject_id": "Q169",
            "property_label": "subclass of",
            "property_id": "P279",
            "object_label": "fruit",
            "object_id": "Q3314483",
        }
    ]


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


def test_candidate_search_preserves_multiple_results_and_ranks_wikidata_type():
    client = WikidataMCPClient(WikidataMCPConfig())
    client.search_items = lambda query, limit=5: [
        {"id": "Q100", "label": "Apple", "description": "fruit"},
        {"id": "Q200", "label": "Apple", "description": "technology company"},
        {"id": "Q300", "label": "Apple Foundation", "description": "organization"},
    ]
    client.get_statements = lambda entity_id: [
        {
            "property_id": "P31",
            "property_label": "instance of",
            "object_id": "Q43229" if entity_id in {"Q200", "Q300"} else "Q3314483",
            "object_label": "organization" if entity_id in {"Q200", "Q300"} else "fruit",
        }
    ]

    groups = client.search_candidate_groups(
        [EntityMention(surface="Apple", entity_type="Organization")],
        limit=2,
        context="Apple released a computer.",
    )

    assert [candidate.id for candidate in groups[0].candidates] == ["Q200", "Q300"]


def test_candidate_paths_find_two_hop_context_and_filter_local_hub():
    client = WikidataMCPClient(WikidataMCPConfig())
    left = WikidataEntity(
        mention=EntityMention(surface="Alpha"),
        id="Q1",
        iri="http://www.wikidata.org/entity/Q1",
        label="Alpha",
        statements=[
            {
                "property_id": "P31",
                "property_label": "instance of",
                "object_id": "Q999",
                "object_label": "shared class",
            }
        ],
    )
    right = WikidataEntity(
        mention=EntityMention(surface="Beta"),
        id="Q2",
        iri="http://www.wikidata.org/entity/Q2",
        label="Beta",
        statements=[
            {
                "property_id": "P31",
                "property_label": "instance of",
                "object_id": "Q999",
                "object_label": "shared class",
            }
        ],
    )
    groups = [
        WikidataCandidateGroup(mention=left.mention, candidates=[left]),
        WikidataCandidateGroup(mention=right.mention, candidates=[right]),
    ]

    paths = client.find_candidate_paths(groups, max_hops=2, expansion_limit=0, hub_degree_threshold=2)
    filtered = client.find_candidate_paths(groups, max_hops=2, expansion_limit=0, hub_degree_threshold=1)

    assert len(paths) == 1
    assert paths[0].hops == 2
    assert paths[0].to_text() == (
        "Alpha instance of shared class. Beta instance of shared class."
    )
    assert filtered == []
