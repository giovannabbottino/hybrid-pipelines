from pathlib import Path


def test_rdf_prompt_requires_only_turtle_and_expected_prefixes():
    prompt = Path("prompt/prompts/rdf-build.txt").read_text(encoding="utf-8")

    assert 'The first character of the response must be "@"' in prompt
    assert "Do not write introductions" in prompt
    assert "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> ." in prompt
    assert "@prefix wd: <http://www.wikidata.org/entity/> ." in prompt
    assert "@prefix kg: <https://example.org/wikidata-description/> ." in prompt
    assert "@prefix ex:" not in prompt
