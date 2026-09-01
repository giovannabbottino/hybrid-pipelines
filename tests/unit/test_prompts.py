from pathlib import Path

from hybrid_pipelines.infrastructure.prompt_repository import PromptRepository


def _mandatory_turtle_rules(prompt: str) -> str:
    return prompt.split("Mandatory Turtle syntax rules:\n", 1)[1].split("\n\n", 1)[0]


def test_prompt_repository_loads_project_prompts():
    repository = PromptRepository()

    assert repository.load_prompt("system/agent.txt")
    assert repository.load_prompt("prompts/entity-extraction.txt")
    assert repository.load_prompt("prompts/candidate-disambiguation.txt")
    assert repository.load_prompt("prompts/rdf-build.txt")


def test_entity_prompt_preserves_distinct_ambiguous_mentions():
    prompt = Path("prompt/prompts/entity-extraction.txt").read_text(encoding="utf-8")

    assert "same surface form denotes different entities" in prompt
    assert "each occurrence with its own character offsets" in prompt
    assert "at most 16 entities" in prompt


def test_disambiguation_prompt_restricts_selections_to_supplied_candidates():
    prompt = Path("prompt/prompts/candidate-disambiguation.txt").read_text(encoding="utf-8")

    assert "exactly one selection for every candidate group" in prompt
    assert "selected_id must be one of" in prompt
    assert "Never invent, alter, normalize, or substitute a Wikidata ID" in prompt
    assert "summarized graph_context" in prompt
    assert "never selects a candidate automatically" in prompt


def test_rdf_prompt_requires_only_turtle_and_expected_prefixes():
    prompt = Path("prompt/prompts/rdf-build.txt").read_text(encoding="utf-8")

    assert 'The first character of the response must be "@"' in prompt
    assert "Do not write introductions" in prompt
    assert "standard RDF/Turtle grammar" in prompt
    assert "valid Turtle syntax takes precedence" in prompt
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
    assert "standard RDF/Turtle grammar" in prompt
    assert "valid Turtle syntax takes precedence" in prompt


def test_rdf_and_system_prompts_share_mandatory_turtle_rules():
    rdf_prompt = Path("prompt/prompts/rdf-build.txt").read_text(encoding="utf-8")
    system_prompt = Path("prompt/system/agent.txt").read_text(encoding="utf-8")
    rules = _mandatory_turtle_rules(rdf_prompt)

    assert rules == _mandatory_turtle_rules(system_prompt)
    assert "Every prefix used in a triple MUST be declared" in rules
    assert "include exactly `@prefix kg:" in rules
    assert "Never write two objects next to each other" in rules
    assert "Use `,` only to separate multiple objects of the same predicate" in rules
    assert "Use `;` to continue the same subject with a different predicate" in rules
    assert "never `subject kg:is a object`" in rules
