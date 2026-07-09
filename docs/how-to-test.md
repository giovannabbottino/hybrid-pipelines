# How to test

Tests are organized under `tests/unit/`.

- `test_agent_service.py` covers service orchestration, entity extraction parsing, Wikidata resolution calls, relationship handling, RDF build prompting, and response shape.
- `test_prompts.py` checks important RDF prompt constraints such as required prefixes and no undeclared `ex:` prefix.

## Run all tests

Run commands from `hybrid-pipelines/`.

```bash
python -m pytest
```

## Run focused tests

```bash
python -m pytest tests/unit/test_agent_service.py
python -m pytest tests/unit/test_prompts.py
```

## Notes

- Tests use the `pythonpath = .` setting from `pytest.ini`, so run them from the module root.
- Pytest writes temporary files under `.pytest-runtime` through the configured `--basetemp`.
- The automated tests do not require Ollama or live Wikidata access; external services are stubbed.
