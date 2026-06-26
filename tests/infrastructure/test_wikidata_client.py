from src.domain.models import EntityMention
from src.infrastructure.wikidata_client import WikidataMCPClient, WikidataMCPConfig


class StubMCPClient(WikidataMCPClient):
    def __init__(self):
        super().__init__(WikidataMCPConfig(url="https://example.test/mcp/", allow_action_api_fallback=False))

    def search_items(self, query: str, limit: int = 5):
        mapping = {
            "Mango": [{"id": "Q1054564", "label": "Mango"}],
            "fruit": [{"id": "Q1364", "label": "fruit"}],
        }
        return mapping[query]

    def get_statements(self, entity_id: str):
        if entity_id == "Q1054564":
            return [
                {
                    "property_id": "P31",
                    "property_label": "instance of",
                    "object_id": "Q1364",
                    "object_label": "fruit",
                }
            ]
        return []


def test_resolve_entities_and_find_direct_relationships():
    client = StubMCPClient()
    mentions = [
        EntityMention(surface="Mango", start=0, end=5),
        EntityMention(surface="fruit", start=15, end=20),
    ]

    entities = client.resolve_entities(mentions)
    relationships = client.find_relationships(entities)

    assert [entity.id for entity in entities] == ["Q1054564", "Q1364"]
    assert len(relationships) == 1
    assert relationships[0].subject_id == "Q1054564"
    assert relationships[0].property_id == "P31"
    assert relationships[0].object_id == "Q1364"


def test_action_api_fallback_sends_wikimedia_etiquette_headers_and_maxlag(monkeypatch):
    captured = {}

    class DummyResponse:
        status_code = 200
        headers = {}
        text = "{}"

        def raise_for_status(self):
            return None

        def json(self):
            return {"search": [{"id": "Q60", "label": "New York City", "pageid": 60}]}

    def fake_request(method, url, **kwargs):
        captured["method"] = method
        captured["url"] = url
        captured["kwargs"] = kwargs
        return DummyResponse()

    monkeypatch.setattr("src.infrastructure.wikidata_client.requests.request", fake_request)
    client = WikidataMCPClient(
        WikidataMCPConfig(
            action_api_url="https://www.wikidata.org/w/api.php",
            user_agent="hybrid-pipelines-agent/1.0 test@example.org",
            maxlag=3,
        )
    )

    items = client._search_items_action_api("New York", limit=1)

    assert items[0]["id"] == "Q60"
    assert captured["method"] == "GET"
    assert captured["kwargs"]["params"]["maxlag"] == 3
    assert captured["kwargs"]["headers"]["User-Agent"] == "hybrid-pipelines-agent/1.0 test@example.org"
    assert captured["kwargs"]["headers"]["Accept-Encoding"] == "gzip, deflate"
