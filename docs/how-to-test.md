# How to test

Tests are organized under `tests/unit/`.

- `test_agent_service.py` covers service orchestration, entity extraction parsing, Wikidata resolution calls, relationship handling, RDF build prompting, and response shape.
- `test_prompts.py` checks required prefixes and verifies that the RDF-build and system prompts share the canonical prefix-binding and Turtle-punctuation rules.

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

## Continuous integration

GitHub Actions runs `.github/workflows/ci.yml` for every push, pull request, and manual dispatch. The workflow:

- runs Ruff, Pyright, and the test suite on Python 3.12;
- caches downloaded pip packages;
- cancels an older run when a newer commit is pushed to the same branch.

Run the same checks locally with:

```bash
python -m pip install -e ".[test,lint]"
python -m ruff check .
python -m pyright
python -m pytest
```

## Notes

- The project uses the standard `src` layout. Tests use `pythonpath = src` from `pytest.ini`, so run them from the repository root.
- Pytest writes temporary files under `.pytest-runtime` through the configured `--basetemp`.
- The automated tests do not require Ollama or live Wikidata access; external services are stubbed.
