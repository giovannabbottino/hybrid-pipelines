import json

from src.application.services import HybridAgentService, _strip_code_fence
from src.domain.models import EntityMention, WikidataEntity, WikidataRelationship


class StubLLM:
    def __init__(self):
        self.calls = []

    def generate(self, system_prompt: str, prompt: str, stage: str) -> str:
        self.calls.append({"system_prompt": system_prompt, "prompt": prompt, "stage": stage})
        if stage == "entity_extraction":
            return json.dumps(
                {
                    "entities": [
                        {"surface": "Mango", "start": 0, "end": 5, "entity_type": "Entity", "confidence": 0.95},
                        {"surface": "fruit", "start": 15, "end": 20, "entity_type": "Class", "confidence": 0.9},
                        {"surface": "tree", "start": 28, "end": 32, "entity_type": "Class", "confidence": 0.9},
                    ]
                }
            )
        return "@prefix ex: <http://example.org/hybrid/> .\nex:doc ex:mentions ex:mango ."

    def health_check(self):
        return {"status": "ok"}


class StubWikidata:
    def __init__(self):
        self.mentions = None

    def resolve_entities(self, mentions, limit=3, context=None):
        self.mentions = mentions
        return [
            WikidataEntity(mention=mentions[0], id="Q1054564", iri="http://www.wikidata.org/entity/Q1054564", label="Mango"),
            WikidataEntity(mention=mentions[1], id="Q1364", iri="http://www.wikidata.org/entity/Q1364", label="fruit"),
            WikidataEntity(mention=mentions[2], id="Q10884", iri="http://www.wikidata.org/entity/Q10884", label="tree"),
        ]

    def find_relationships(self, entities):
        return [
            WikidataRelationship(
                subject_id="Q1054564",
                subject_label="Mango",
                property_id="P31",
                property_label="instance of",
                object_id="Q1364",
                object_label="fruit",
            )
        ]

    def health(self):
        return {"status": "ok"}


class StubPromptRepository:
    def load_prompt(self, prompt_name: str) -> str:
        prompts = {
            "system/agent.txt": "System prompt",
            "prompts/entity-extraction.txt": "Extract ${TEXT}",
            "prompts/rdf-build.txt": "Build ${PAYLOAD}",
        }
        return prompts[prompt_name]


def test_agent_extracts_entities_resolves_wikidata_and_builds_rdf():
    service = HybridAgentService(llm=StubLLM(), wikidata=StubWikidata(), prompt_repository=StubPromptRepository())

    response = service.analyze(type("Request", (), {"text": "Mango is not a fruit from a tree.", "idempotence_key": None})())

    assert [entity.mention.surface for entity in response.entities] == ["Mango", "fruit", "tree"]
    assert response.relationships[0].property_id == "P31"
    assert response.rdf.startswith("@prefix")


def test_rdf_sanitizer_keeps_only_turtle_from_prose_and_fence():
    raw = """Here is the Turtle representation:

```turtle
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix wd: <http://www.wikidata.org/entity/> .
@prefix kg: <https://example.org/wikidata-description/> .

wd:Q169 rdfs:label "mango"@en .
```

Please note this is a template.
"""

    cleaned = _strip_code_fence(raw)

    assert cleaned.startswith("@prefix rdfs:")
    assert "Here is" not in cleaned
    assert "Please note" not in cleaned
