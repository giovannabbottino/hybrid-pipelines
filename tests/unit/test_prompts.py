from hybrid_pipelines.infrastructure.prompt_repository import PromptRepository


def test_current_prompts_define_separate_ned_and_rdf_stages():
    repository = PromptRepository()
    system = repository.load_prompt("system/agent.txt")
    ned = repository.load_prompt("prompts/ned.txt")
    rdf = repository.load_prompt("prompts/rdf-build.txt")
    assert "Named Entity Disambiguation" in system
    assert "${TEXT}" in ned
    assert "You choose the tools" in ned
    assert "id: null" in ned
    assert "${PAYLOAD}" in rdf
    assert "Do not call tools" in rdf
    assert "Cooccurrence alone is not evidence" in rdf
