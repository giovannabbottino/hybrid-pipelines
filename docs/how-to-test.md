# How to test

Tests are organized by scope:
- `tests/unit/` for service and RDF-building behavior
- `tests/controllers/` for Flask blueprints using a test client with stubbed dependencies
- `tests/infrastructure/` for the Ollama client and CSV logger
- `tests/integration/` for end-to-end request flows through the app factory with mocked Wikidata/Ollama calls

## Running tests

```bash
python -m pytest
```

## Notes

- The automated tests do not require Ollama or live Wikidata MCP access.
- Tests stub the LLM and Wikidata client where external calls would normally happen.
