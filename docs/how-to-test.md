# Testing

Run from the repository root:

```powershell
uv pip install --python .venv/Scripts/python.exe -e ".[test,lint]"
.venv/Scripts/python.exe -m pytest
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m pyright
```

Unit tests cover model-directed tool calls, MCP schema discovery and session cleanup, NED evidence and offsets, unresolved mentions, retries, limits, RDF validation, HTTP responses, and logging. External calls are mocked so tests do not require Ollama or Wikidata.

For a live check, run Ollama with a tool-capable model and start the API following [running](how-to-run.md). Check `GET /health`, then send a short text to `POST /analyze`. Inspect `tool_calls` in the response to confirm which consultations the model actually requested.
