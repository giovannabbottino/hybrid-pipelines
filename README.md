# Hybrid Pipelines

Flask API that builds knowledge graphs with Ollama and Wikidata evidence through MCP. Requires Python 3.12, uv, Ollama, and access to Wikidata MCP.

## Run

```powershell
uv venv --python 3.12 --seed .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[test,lint]"
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
ollama pull llama3.1:8b
python -m hybrid_pipelines
```

Keep Ollama running. If `.env` already exists, edit it without overwriting your settings. The API listens on `http://127.0.0.1:5050`. Use [.env.example](.env.example) as the configuration reference; the application loads `.env` automatically.

## Usage

```powershell
$body = @{ text = "Mango is a tropical fruit." } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:5050/analyze -ContentType application/json -Body $body
```

`GET /health` checks Ollama and Wikidata. `POST /analyze` extracts mentions, retrieves Wikidata candidates, asks the LLM to select QIDs, and generates RDF. For RDF generation, the LLM returns `{"rdf":"<Turtle>"}`. The application validates the JSON, prefixes, and Turtle, applies graph rules, and returns `rdf` as a string.

RDF generation allows up to three total attempts, including the first, when the response is empty or invalid. The attempt limit is fixed internally. External failures and the processing time budget may stop execution earlier. Logs are written to `logs/analyze_log.jsonl` and `logs/ollama_responses.jsonl`.

## Checks

```powershell
python -m pytest
python -m ruff check .
python -m pyright
```

## Structure

- `src/hybrid_pipelines/application/`: orchestration, mentions, disambiguation, and graph enrichment.
- `src/hybrid_pipelines/domain/`: models and RDF validation.
- `src/hybrid_pipelines/infrastructure/`: clients, prompts, and logging.
- `prompt/`: LLM instructions.
- `tests/unit/`: tests without external services.

See [running](docs/how-to-run.md), [API](docs/analyze.md), [prompts](docs/prompt.md), and [testing](docs/how-to-test.md).
