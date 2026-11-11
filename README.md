# Hybrid Pipelines

Flask API that builds knowledge graphs with Ollama and Wikidata evidence through MCP. Requires Python 3.12, uv, Ollama, and access to Wikidata MCP.

## Run

```powershell
uv venv --python 3.12 --seed .venv
.\.venv\Scripts\Activate.ps1
uv pip install --python .venv/Scripts/python.exe -e ".[test,lint]"
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

`POST /analyze` has two stages: NED with model-directed MCP consultations, then RDF generation. The LLM chooses the tools and query arguments, resolves mentions, and produces `{"rdf":"<Turtle>"}` using the evidence. The application validates the output without adding heuristic triples. The response includes the NED entities and a trace of MCP calls. See the [API contract and migration notes](docs/analyze.md).

The model must support native tool calling through Ollama's `/api/chat`. The MCP Python SDK manages the remote session and discovers tool schemas; the configured allowlist defaults to `search_items,get_statements`. `GET /health` checks connectivity and MCP discovery.

RDF generation allows up to three total attempts, including the first, when the response is empty or invalid. The attempt limit is fixed internally. External failures and the processing time budget may stop execution earlier. Logs are written to `logs/analyze_log.jsonl` and `logs/ollama_responses.jsonl`.

## Checks

```powershell
python -m pytest
python -m ruff check .
python -m pyright
```

## Structure

- `src/hybrid_pipelines/application/`: NED tool loop and RDF generation.
- `src/hybrid_pipelines/domain/`: models and RDF validation.
- `src/hybrid_pipelines/infrastructure/`: clients, prompts, and logging.
- `prompt/`: LLM instructions.
- `tests/unit/`: tests without external services.

See [running](docs/how-to-run.md), [API](docs/analyze.md), [prompts](docs/prompt.md), and [testing](docs/how-to-test.md).
