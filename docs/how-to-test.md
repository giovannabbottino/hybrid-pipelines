# Testing

Run commands from the repository root with the virtual environment active.

```powershell
python -m pip install -e ".[test,lint]"
python -m pytest
python -m ruff check .
python -m pyright
```

Tests in `tests/unit/` cover extraction, disambiguation, the `{"rdf":"<Turtle>"}` contract, required prefixes, retries, HTTP errors, and logging. They do not require live Ollama or Wikidata access. GitHub Actions runs the same checks on Python 3.12.
