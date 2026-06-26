from flask import Flask

from src.controllers.analyze_controller import create_analyze_blueprint
from src.domain.models import AnalyzeResponse


class StubService:
    def __init__(self):
        self.request = None

    def analyze(self, request):
        self.request = request
        return AnalyzeResponse(text=request.text, entities=[], relationships=[], rdf="@prefix ex: <http://example.org/hybrid/> .")

    def health(self):
        return {"llm": {"status": "ok"}, "wikidata_mcp": {"status": "ok"}}


def make_client():
    service = StubService()
    app = Flask(__name__)
    app.register_blueprint(create_analyze_blueprint(service))
    app.config.update(TESTING=True)
    return app.test_client(), service


def test_analyze_accepts_text_and_returns_agent_payload():
    client, service = make_client()

    response = client.post("/analyze", json={"text": "Mango is not a fruit from a tree."})

    assert response.status_code == 200
    data = response.get_json()
    assert data["text"] == "Mango is not a fruit from a tree."
    assert data["rdf"].startswith("@prefix")
    assert service.request.text == "Mango is not a fruit from a tree."


def test_analyze_requires_text():
    client, _ = make_client()

    response = client.post("/analyze", json={})

    assert response.status_code == 400
    assert "text" in response.get_json()["error"]


def test_health_ok():
    client, _ = make_client()

    response = client.get("/health")

    assert response.status_code == 200
