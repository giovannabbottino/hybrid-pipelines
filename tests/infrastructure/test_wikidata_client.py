from src.infrastructure.wikidata_client import WikidataConfig, WikidataGateway


class DummyResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self.payload


def entity(label: str, claims: dict | None = None) -> dict:
    return {
        "labels": {"en": {"value": label}},
        "claims": claims or {},
    }


def statement(property_id: str, target_id: str) -> dict:
    return {
        "mainsnak": {
            "property": property_id,
            "datavalue": {
                "value": {
                    "entity-type": "item",
                    "numeric-id": int(target_id[1:]),
                    "id": target_id,
                }
            },
        }
    }


def test_shortest_path_finds_direct_wikidata_claim(monkeypatch):
    entities = {
        "Q1": entity("Source", {"P31": [statement("P31", "Q2")]}),
        "Q2": entity("Target"),
    }

    def fake_get(url, params=None, **kwargs):  # type: ignore[override]
        entity_id = params["ids"]
        return DummyResponse({"entities": {entity_id: entities[entity_id]}})

    monkeypatch.setattr("src.infrastructure.wikidata_client.requests.get", fake_get)
    gateway = WikidataGateway(WikidataConfig(api_url="https://example.test/w/api.php"))

    path = gateway.shortest_path("http://www.wikidata.org/entity/Q1", "http://www.wikidata.org/entity/Q2")

    assert path is not None
    assert len(path) == 1
    assert path[0].subject_iri == "http://www.wikidata.org/entity/Q1"
    assert path[0].subject_label == "Source"
    assert path[0].predicate == "P31"
    assert path[0].object_iri == "http://www.wikidata.org/entity/Q2"
    assert path[0].object_label == "Target"


def test_shortest_path_finds_two_hop_wikidata_claim_path(monkeypatch):
    entities = {
        "Q1": entity("Source", {"P279": [statement("P279", "Q2")]}),
        "Q2": entity("Middle", {"P361": [statement("P361", "Q3")]}),
        "Q3": entity("Target"),
    }

    def fake_get(url, params=None, **kwargs):  # type: ignore[override]
        entity_id = params["ids"]
        return DummyResponse({"entities": {entity_id: entities[entity_id]}})

    monkeypatch.setattr("src.infrastructure.wikidata_client.requests.get", fake_get)
    gateway = WikidataGateway(WikidataConfig(api_url="https://example.test/w/api.php"))

    path = gateway.shortest_path("http://www.wikidata.org/entity/Q1", "http://www.wikidata.org/entity/Q3", max_hops=2)

    assert path is not None
    assert [step.subject_label for step in path] == ["Source", "Middle"]
    assert [step.object_label for step in path] == ["Middle", "Target"]
    assert [step.predicate for step in path] == ["P279", "P361"]


def test_shortest_path_respects_hub_threshold(monkeypatch):
    entities = {
        "Q1": entity("Source", {"P31": [statement("P31", "Q2"), statement("P31", "Q3")]}),
        "Q2": entity("Other"),
        "Q3": entity("Target"),
    }

    def fake_get(url, params=None, **kwargs):  # type: ignore[override]
        entity_id = params["ids"]
        return DummyResponse({"entities": {entity_id: entities[entity_id]}})

    monkeypatch.setattr("src.infrastructure.wikidata_client.requests.get", fake_get)
    gateway = WikidataGateway(WikidataConfig(api_url="https://example.test/w/api.php"))

    path = gateway.shortest_path(
        "http://www.wikidata.org/entity/Q1",
        "http://www.wikidata.org/entity/Q3",
        hub_threshold=1,
    )

    assert path is None
