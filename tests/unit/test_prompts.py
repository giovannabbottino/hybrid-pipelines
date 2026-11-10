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
    assert "P31/P279 type statements" in prompt
    assert "graph_context" not in prompt
    assert "Never return the schema itself" in prompt
    assert "never selects a candidate automatically" in prompt


def test_rdf_and_system_prompts_require_json_rdf_and_exact_prefixes():
    from hybrid_pipelines.domain.structured_rdf import RDF_PREFIXES

    for name in ("prompt/system/agent.txt", "prompt/prompts/rdf-build.txt"):
        prompt = Path(name).read_text(encoding="utf-8")
        assert '"rdf"' in prompt
        assert "non-empty" in prompt
        assert "Turtle string" in prompt
        assert RDF_PREFIXES.strip() in prompt
        assert "Never use a QID as label text" in prompt
        assert "snake_case" in prompt
    rdf_prompt = Path("prompt/prompts/rdf-build.txt").read_text(encoding="utf-8")
    assert "${PAYLOAD}" in rdf_prompt
    assert "subject and object QIDs" in rdf_prompt
    assert "Materialize every provided relationship exactly once" in rdf_prompt
