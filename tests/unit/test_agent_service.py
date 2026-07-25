import json

import pytest
import requests
from rdflib import Graph, URIRef

from hybrid_pipelines.application.services import (
    HybridAgentService,
    _dedupe_mentions,
    _json_from_text,
    _realign_mentions,
    _strip_code_fence,
    _supplement_mentions,
)
from hybrid_pipelines.domain.models import (
    AnalyzeRequest,
    EntityMention,
    WikidataEntity,
    WikidataRelationship,
)


class StubLLM:
    def __init__(self):
        self.calls = []

    def generate(self, system_prompt: str, prompt: str, stage: str, timeout_seconds=None) -> str:
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
            WikidataEntity(
                mention=mentions[0],
                id="Q1054564",
                iri="http://www.wikidata.org/entity/Q1054564",
                label="Mango",
            ),
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

    response = service.analyze(AnalyzeRequest(text="Mango is not a fruit from a tree."))

    assert [entity.mention.surface for entity in response.entities] == ["Mango", "fruit", "tree"]
    assert response.relationships[0].property_id == "P31"
    assert response.rdf.startswith("@prefix")


def test_agent_can_skip_llm_entity_extraction():
    llm = StubLLM()
    wikidata = StubWikidata()
    service = HybridAgentService(
        llm=llm,
        wikidata=wikidata,
        prompt_repository=StubPromptRepository(),
        llm_entity_extraction_enabled=False,
        mention_limit=3,
    )

    response = service.analyze(AnalyzeRequest(text="Mango is not a fruit from a tree."))

    assert [call["stage"] for call in llm.calls] == ["rdf_build"]
    assert [entity.mention.surface for entity in response.entities] == ["Mango", "fruit", "tree"]
    assert response.llm["entity_extraction"] == ""


def test_agent_does_not_add_heuristic_words_when_llm_found_entities():
    class FocusedLLM(StubLLM):
        def generate(self, system_prompt: str, prompt: str, stage: str, timeout_seconds=None) -> str:
            if stage == "entity_extraction":
                return json.dumps({"entities": [{"surface": "Mango"}]})
            return super().generate(system_prompt, prompt, stage, timeout_seconds)

    class FocusedWikidata(StubWikidata):
        def resolve_entities(self, mentions, limit=3, context=None):
            self.mentions = mentions
            return [
                WikidataEntity(
                    mention=mentions[0],
                    id="Q1054564",
                    iri="http://www.wikidata.org/entity/Q1054564",
                    label="Mango",
                )
            ]

        def find_relationships(self, entities):
            return []

    llm = FocusedLLM()
    wikidata = FocusedWikidata()
    service = HybridAgentService(
        llm=llm,
        wikidata=wikidata,
        prompt_repository=StubPromptRepository(),
    )

    service.analyze(AnalyzeRequest(text="Mango is not a fruit from a tree."))

    assert [mention.surface for mention in wikidata.mentions] == ["Mango"]


def test_dedupe_keeps_repeated_surface_at_distinct_offsets_for_ambiguity():
    mentions = [
        EntityMention(surface="Jaguar", start=0, end=6),
        EntityMention(surface="Jaguar", start=150, end=156),
        EntityMention(surface="Jaguar", start=150, end=156),
    ]

    deduped = _dedupe_mentions(mentions)

    assert [(item.start, item.end) for item in deduped] == [(0, 6), (150, 156)]


def test_entity_json_parser_recovers_truncated_wrapper_and_top_level_array():
    truncated = '{"entities": [{"surface": "Apple Records", "start": 20, "end": 33}]'
    top_level = '[{"surface": "technology"}]'

    assert _json_from_text(truncated)["entities"][0]["surface"] == "Apple Records"
    assert _json_from_text(top_level)["entities"][0]["surface"] == "technology"


def test_realign_mentions_uses_successive_exact_occurrences():
    text = "Jaguar is a brand. Jaguar was a torpedo boat."
    mentions = [
        EntityMention(surface="Jaguar", start=999, end=1005),
        EntityMention(surface="Jaguar", start=0, end=6),
    ]

    aligned = _realign_mentions(text, mentions)

    assert [(item.start, item.end) for item in aligned] == [(0, 6), (19, 25)]


def test_agent_retries_until_rdf_is_valid():
    class RetryLLM(StubLLM):
        def __init__(self):
            super().__init__()
            self.rdf_responses = [
                "not rdf",
                "@prefix ex: <http://example.org/hybrid/> .\nex:doc ex:mentions ex:mango .",
            ]

        def generate(self, system_prompt: str, prompt: str, stage: str, timeout_seconds=None) -> str:
            if stage == "rdf_build":
                self.calls.append({"system_prompt": system_prompt, "prompt": prompt, "stage": stage})
                return self.rdf_responses.pop(0)
            return super().generate(system_prompt, prompt, stage)

    class EmptyWikidata(StubWikidata):
        def resolve_entities(self, mentions, limit=3, context=None):
            return []

        def find_relationships(self, entities):
            return []

    llm = RetryLLM()
    service = HybridAgentService(
        llm=llm,
        wikidata=EmptyWikidata(),
        prompt_repository=StubPromptRepository(),
    )

    response = service.analyze(
        AnalyzeRequest(text="Mango is not a fruit from a tree.", max_rdf_attempts=3)
    )

    rdf_prompts = [call["prompt"] for call in llm.calls if call["stage"] == "rdf_build"]
    assert "ex:mentions" in response.rdf
    assert '"doc"@en' in response.rdf
    assert '"mango"@en' in response.rdf
    assert len(rdf_prompts) == 2
    assert "previous answer was not valid Turtle RDF" in rdf_prompts[1]


def test_agent_uses_deterministic_fallback_when_rdf_has_only_prefixes():
    class PrefixOnlyRetryLLM(StubLLM):
        def __init__(self):
            super().__init__()
            self.rdf_responses = [
                (
                    "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
                    "@prefix wd: <http://www.wikidata.org/entity/> .\n"
                    "@prefix kg: <https://example.org/wikidata-description/> ."
                ),
                (
                    "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
                    "@prefix wd: <http://www.wikidata.org/entity/> .\n"
                    "@prefix kg: <https://example.org/wikidata-description/> .\n"
                    'wd:Q1054564 rdfs:label "Mango" .'
                ),
            ]

        def generate(self, system_prompt: str, prompt: str, stage: str, timeout_seconds=None) -> str:
            if stage == "rdf_build":
                self.calls.append({"system_prompt": system_prompt, "prompt": prompt, "stage": stage})
                return self.rdf_responses.pop(0)
            return super().generate(system_prompt, prompt, stage)

    llm = PrefixOnlyRetryLLM()
    service = HybridAgentService(llm=llm, wikidata=StubWikidata(), prompt_repository=StubPromptRepository())

    response = service.analyze(
        AnalyzeRequest(text="Mango is not a fruit from a tree.", max_rdf_attempts=3)
    )

    rdf_prompts = [call["prompt"] for call in llm.calls if call["stage"] == "rdf_build"]
    assert 'rdfs:label "Mango"' in response.rdf
    assert "kg:is" in response.rdf
    assert len(rdf_prompts) == 1


def test_agent_repairs_doubled_literal_quotes():
    class QuoteRepairLLM(StubLLM):
        def generate(self, system_prompt: str, prompt: str, stage: str, timeout_seconds=None) -> str:
            if stage == "rdf_build":
                return (
                    "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
                    "@prefix wd: <http://www.wikidata.org/entity/> .\n"
                    "@prefix kg: <https://example.org/wikidata-description/> .\n"
                    'wd:Q1054564 rdfs:label ""Mango"" .'
                )
            return super().generate(system_prompt, prompt, stage)

    service = HybridAgentService(
        llm=QuoteRepairLLM(),
        wikidata=StubWikidata(),
        prompt_repository=StubPromptRepository(),
    )

    response = service.analyze(
        AnalyzeRequest(text="Mango is not a fruit from a tree.", max_rdf_attempts=3)
    )

    assert '""Mango""' not in response.rdf
    assert '"Mango"' in response.rdf


def test_agent_adds_missing_labels_for_every_entity_resource():
    class MissingLabelsLLM(StubLLM):
        def generate(self, system_prompt: str, prompt: str, stage: str, timeout_seconds=None) -> str:
            if stage == "rdf_build":
                return (
                    "@prefix wd: <http://www.wikidata.org/entity/> .\n"
                    "@prefix kg: <https://example.org/wikidata-description/> .\n"
                    "wd:Q1054564 kg:is wd:Q1364 ."
                )
            return super().generate(system_prompt, prompt, stage, timeout_seconds)

    service = HybridAgentService(
        llm=MissingLabelsLLM(),
        wikidata=StubWikidata(),
        prompt_repository=StubPromptRepository(),
    )

    response = service.analyze(AnalyzeRequest(text="Mango is not a fruit from a tree."))

    assert '"Mango"@en' in response.rdf
    assert '"fruit"@en' in response.rdf


def test_agent_materializes_wikidata_relationship_and_removes_qid_labels():
    class IncompleteRDFLLM(StubLLM):
        def generate(self, system_prompt: str, prompt: str, stage: str, timeout_seconds=None) -> str:
            if stage == "rdf_build":
                return (
                    "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
                    "@prefix wd: <http://www.wikidata.org/entity/> .\n"
                    'wd:Q1054564 rdfs:label "Q1054564"@en .'
                )
            return super().generate(system_prompt, prompt, stage, timeout_seconds)

    service = HybridAgentService(
        llm=IncompleteRDFLLM(),
        wikidata=StubWikidata(),
        prompt_repository=StubPromptRepository(),
    )

    response = service.analyze(AnalyzeRequest(text="Mango is not a fruit from a tree."))

    assert '"Q1054564"' not in response.rdf
    assert 'wd:Q1054564' in response.rdf
    assert 'kg:is wd:Q1364' in response.rdf
    assert '"Mango"@en' in response.rdf
    assert '"fruit"@en' in response.rdf


def test_agent_links_resolved_entities_in_same_text_segment_and_drops_unknown_qids():
    class HallucinatingLLM(StubLLM):
        def generate(self, system_prompt: str, prompt: str, stage: str, timeout_seconds=None) -> str:
            if stage == "rdf_build":
                return (
                    "@prefix wd: <http://www.wikidata.org/entity/> .\n"
                    "@prefix kg: <https://example.org/wikidata-description/> .\n"
                    "wd:Q999999 kg:is wd:Q888888 ."
                )
            return super().generate(system_prompt, prompt, stage, timeout_seconds)

    service = HybridAgentService(
        llm=HallucinatingLLM(),
        wikidata=StubWikidata(),
        prompt_repository=StubPromptRepository(),
    )

    response = service.analyze(AnalyzeRequest(text="Mango is not a fruit from a tree."))

    graph = Graph().parse(data=response.rdf, format="turtle")
    wd = "http://www.wikidata.org/entity/"
    kg = "https://example.org/wikidata-description/"
    assert (URIRef(f"{wd}Q1054564"), URIRef(f"{kg}related_to"), URIRef(f"{wd}Q1364")) in graph
    assert (URIRef(f"{wd}Q1054564"), URIRef(f"{kg}is"), URIRef(f"{wd}Q1364")) in graph
    assert "Q999999" not in response.rdf
    assert "Q888888" not in response.rdf


def test_agent_prefers_human_readable_mention_from_text_over_wikidata_label():
    class CanonicalLabelWikidata(StubWikidata):
        def resolve_entities(self, mentions, limit=3, context=None):
            entities = super().resolve_entities(mentions, limit, context)
            return [
                WikidataEntity(
                    mention=entities[0].mention,
                    id=entities[0].id,
                    iri=entities[0].iri,
                    label="Mangifera indica fruit",
                ),
                *entities[1:],
            ]

    class MissingLabelsLLM(StubLLM):
        def generate(self, system_prompt: str, prompt: str, stage: str, timeout_seconds=None) -> str:
            if stage == "rdf_build":
                return (
                    "@prefix wd: <http://www.wikidata.org/entity/> .\n"
                    "@prefix kg: <https://example.org/wikidata-description/> .\n"
                    "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
                    "wd:Q1054564 rdfs:label \"Mangifera indica fruit\"@en ; "
                    "kg:is wd:Q1364 ."
                )
            return super().generate(system_prompt, prompt, stage, timeout_seconds)

    service = HybridAgentService(
        llm=MissingLabelsLLM(),
        wikidata=CanonicalLabelWikidata(),
        prompt_repository=StubPromptRepository(),
    )

    response = service.analyze(AnalyzeRequest(text="Mango is not a fruit from a tree."))

    assert '"Mango"@en' in response.rdf
    assert '"Mangifera indica fruit"@en' in response.rdf


def test_agent_raises_timeout_when_max_processing_seconds_is_too_low():
    class TimeoutAwareLLM(StubLLM):
        def generate(self, system_prompt: str, prompt: str, stage: str, timeout_seconds=None) -> str:
            if timeout_seconds is not None and timeout_seconds < 0.05:
                raise requests.Timeout("Analyze request exceeded configured max processing time.")
            return super().generate(system_prompt, prompt, stage, timeout_seconds)

    service = HybridAgentService(
        llm=TimeoutAwareLLM(),
        wikidata=StubWikidata(),
        prompt_repository=StubPromptRepository(),
        analyze_timeout_seconds=1,
    )

    request = AnalyzeRequest(text="Mango is not a fruit from a tree.", max_processing_seconds=0.01)

    with pytest.raises(requests.Timeout, match="max processing time"):
        service.analyze(request)


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

def test_supplement_mentions_adds_explicit_descriptor_concepts():
    text = (
        "A sports car used Type 24. The technology company used a trade name. "
        "Apple Records is a record label."
    )

    mentions = _supplement_mentions(text, [], limit=16)
    surfaces = {mention.surface.casefold() for mention in mentions}

    assert {"sports car", "24", "technology", "trade", "record"} <= surfaces


def test_agent_uses_deterministic_rdf_when_llm_turtle_is_always_invalid():
    class InvalidRDFLLM(StubLLM):
        def generate(self, system_prompt: str, prompt: str, stage: str, timeout_seconds=None) -> str:
            if stage == "rdf_build":
                self.calls.append({
                    "system_prompt": system_prompt,
                    "prompt": prompt,
                    "stage": stage,
                })
                return "this is not Turtle"
            return super().generate(system_prompt, prompt, stage, timeout_seconds)

    llm = InvalidRDFLLM()
    service = HybridAgentService(
        llm=llm,
        wikidata=StubWikidata(),
        prompt_repository=StubPromptRepository(),
    )

    response = service.analyze(
        AnalyzeRequest(text="Mango is not a fruit from a tree.", max_rdf_attempts=3)
    )

    graph = Graph().parse(data=response.rdf, format="turtle")
    wd = "http://www.wikidata.org/entity/"
    kg = "https://example.org/wikidata-description/"
    assert (URIRef(f"{wd}Q1054564"), URIRef(f"{kg}is"), URIRef(f"{wd}Q1364")) in graph
    assert len([call for call in llm.calls if call["stage"] == "rdf_build"]) == 1

def test_agent_can_prefer_deterministic_rdf_without_rdf_llm_call():
    llm = StubLLM()
    service = HybridAgentService(
        llm=llm,
        wikidata=StubWikidata(),
        prompt_repository=StubPromptRepository(),
    )

    response = service.analyze(
        AnalyzeRequest(
            text="Mango is not a fruit from a tree.",
            prefer_deterministic_rdf=True,
        )
    )

    graph = Graph().parse(data=response.rdf, format="turtle")
    wd = "http://www.wikidata.org/entity/"
    kg = "https://example.org/wikidata-description/"
    assert (URIRef(f"{wd}Q1054564"), URIRef(f"{kg}is"), URIRef(f"{wd}Q1364")) in graph
    assert [call["stage"] for call in llm.calls] == ["entity_extraction"]

def test_supplement_mentions_prioritizes_name_and_label_descriptors():
    text = (
        "A technology company offers online services. "
        "It uses a trade name and operates a record label."
    )

    mentions = _supplement_mentions(text, [], limit=4)
    surfaces = [mention.surface.casefold() for mention in mentions]

    assert surfaces == ["trade", "trade name", "record", "record label"]
    assert "and" not in surfaces

