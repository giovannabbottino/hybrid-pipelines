from pathlib import Path

from hybrid_pipelines.infrastructure.prompt_repository import PromptRepository


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

    assert "This is a JSON selection task. It is not an RDF generation task." in prompt
    assert "selections array length must equal candidate_groups length" in prompt
    assert '"required": ["selections"]' in prompt
    assert '"additionalProperties": false' in prompt
    assert "selected_id must be one of" in prompt
    assert "Never invent, alter, normalize, or substitute a Wikidata ID" in prompt
    assert "summarized graph_context" in prompt
    assert "Never return the schema itself" in prompt
    assert "never selects a candidate automatically" in prompt


def test_rdf_prompt_requires_only_structured_json():
    prompt = Path("prompt/prompts/rdf-build.txt").read_text(encoding="utf-8")

    assert "structured RDF triples" in prompt
    assert "Return exactly one JSON object" in prompt
    assert "Return JSON only" in prompt
    assert "Do not return Turtle" in prompt
    assert "`subject`" in prompt
    assert "`predicate`" in prompt
    assert "`object_type`" in prompt


def test_rdf_prompt_requires_resolved_ids_and_human_labels():
    prompt = Path("prompt/prompts/rdf-build.txt").read_text(encoding="utf-8")

    assert "Preserve every `entities[].id` QID" in prompt
    assert "Use each resolved resource in at least one semantic relationship triple" in prompt
    assert "subject and object QIDs" in prompt
    assert "Prefer `mention.surface`" in prompt
    assert "Never use a QID as label text" in prompt
    assert "Materialize every provided relationship exactly once" in prompt
    assert "do not add facts" in prompt
    assert "Never invent, change, normalize, or omit a provided QID" in prompt


def test_system_prompt_forbids_qids_as_labels():
    prompt = Path("prompt/system/agent.txt").read_text(encoding="utf-8")

    assert "never invent a QID" in prompt
    assert "using `mention.surface` before a canonical label" in prompt
    assert "Return JSON only" in prompt
    assert "serializes the" in prompt
    assert "triples to RDF/Turtle with rdflib" in prompt


def test_rdf_and_system_prompts_share_structured_triple_rules():
    rdf_prompt = Path("prompt/prompts/rdf-build.txt").read_text(encoding="utf-8")
    system_prompt = Path("prompt/system/agent.txt").read_text(encoding="utf-8")

    for term in ("subject", "predicate", "object", "object_type", "resource", "literal"):
        assert term in rdf_prompt
        assert term in system_prompt
    assert "snake_case" in rdf_prompt
    assert "snake_case" in system_prompt
