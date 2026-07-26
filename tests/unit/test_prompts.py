from pathlib import Path

from hybrid_pipelines.infrastructure.prompt_repository import PromptRepository


def test_prompt_repository_loads_project_prompts():
    repository = PromptRepository()

    assert repository.load_prompt("system/agent.txt")
    assert repository.load_prompt("prompts/entity-extraction.txt")
    assert repository.load_prompt("prompts/rdf-build.txt")


def test_entity_prompt_preserves_distinct_ambiguous_mentions():
    prompt = Path("prompt/prompts/entity-extraction.txt").read_text(encoding="utf-8")

    assert "same surface form denotes different entities" in prompt
    assert "each occurrence with its own character offsets" in prompt
    assert "at most 16 entities" in prompt


def test_rdf_prompt_requires_only_turtle_and_expected_prefixes():
    prompt = Path("prompt/prompts/rdf-build.txt").read_text(encoding="utf-8")

    assert 'The first character of the response must be "@"' in prompt
    assert "Do not write introductions" in prompt
    assert "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> ." in prompt
    assert "@prefix wd: <http://www.wikidata.org/entity/> ." in prompt
    assert "@prefix kg: <https://example.org/wikidata-description/> ." in prompt
    assert "@prefix ex:" not in prompt


def test_rdf_prompt_requires_resolved_ids_and_human_labels():
    prompt = Path("prompt/prompts/rdf-build.txt").read_text(encoding="utf-8")

    assert "corresponding RDF resource MUST be `wd:Q...`" in prompt
    assert "Its QID must appear directly in at least one relationship triple" in prompt
    assert "`subject_id` and `object_id`" in prompt
    assert 'wd:<id> rdfs:label "<mention.surface>"@en' in prompt
    assert "A QID is only the resource identifier" in prompt
    assert 'Never emit labels such as `"Q42"`, `"wd:Q42"`' in prompt
    assert "Every `rdfs:label` value must be a human-readable entity name" in prompt
    assert "Materialize every provided relationship exactly once" in prompt
    assert "Do not create unrelated triples" in prompt
    assert "Do not invent, alter, normalize, or omit Wikidata QIDs" in prompt


def test_system_prompt_forbids_qids_as_labels():
    prompt = Path("prompt/system/agent.txt").read_text(encoding="utf-8")

    assert "A Wikidata ID such as Q42 is an identifier" in prompt
    assert 'never emit `rdfs:label "Q42"`' in prompt
    assert "use the canonical Wikidata label" in prompt
