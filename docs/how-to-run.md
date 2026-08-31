# How to run

## Environment variables

The application loads `.env` when running locally and also accepts regular environment variables. Docker Compose sets defaults for the containerized service.

- `SYSTEM_PROMPT_NAME=system/agent.txt`
- `ENTITY_EXTRACTION_PROMPT_NAME=prompts/entity-extraction.txt`
- `RDF_BUILD_PROMPT_NAME=prompts/rdf-build.txt`
- `OLLAMA_API_URL=http://localhost:11434`
- `OLLAMA_MODEL=llama3.1:8b`
- `OLLAMA_CSV_PATH=data/ollama_responses.csv`
- `OLLAMA_TIMEOUT_SECONDS=300`
- `OLLAMA_TEMPERATURE=0`
- `OLLAMA_NUM_PREDICT=1536`
- `ENTITY_MENTION_LIMIT=16`
- `ANALYZE_LOG_PATH=data/analyze_log.jsonl`
- `WIKIDATA_MCP_URL=https://wd-mcp.wmcloud.org/mcp/`
- `WIKIDATA_LANGUAGE=en`
- `WIKIDATA_TIMEOUT_SECONDS=60`
- `WIKIDATA_CANDIDATE_LIMIT=3`
- `WIKIDATA_USER_AGENT=hybrid-pipelines-agent/1.0`
- `WIKIDATA_MAX_RETRIES=2`
- `WIKIDATA_RETRY_BACKOFF_SECONDS=2`

Optional Ollama generation options are ignored when blank: `OLLAMA_SEED`, `OLLAMA_TEMPERATURE`, `OLLAMA_TOP_K`, `OLLAMA_TOP_P`, `OLLAMA_MIN_P`, `OLLAMA_STOP`, `OLLAMA_NUM_CTX`, and `OLLAMA_NUM_PREDICT`.

Entity extraction is always assigned to the LLM, so the normal successful path uses two LLM calls. The configured mention limit is 16, `OLLAMA_NUM_PREDICT=1536` bounds RDF output length, and `OLLAMA_TEMPERATURE=0` reduces sampling variability.

`POST /analyze` uses three RDF attempts by default. Set `max_rdf_attempts` from `1` to `3` to control how many times the same model-based RDF stage runs after strict parse failures.

## Requirements

- Python 3.12
- Ollama with the configured model installed
- Network access to the configured Wikidata MCP endpoint

## Run with Docker Compose

From the repository root:

```powershell
docker compose up --build -d hybrid-pipelines
```

On first run, pull the configured model into the Ollama container:

```powershell
docker exec -it kg-ollama ollama pull llama3.1:8b
```

Check the service:

```powershell
Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:5050/health"
```

Analyze text:

```powershell
$body = @{
  text = "Mango is not a fruit from a tree."
  idempotence_key = "demo-mango-1"
} | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:5050/analyze" `
  -ContentType "application/json" `
  -Body $body
```

Docker Compose writes service data under `hybrid-pipelines/data/`.

## Run locally

Run commands from `hybrid-pipelines/`.

### 1. Install Python dependencies

Linux / macOS:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. Configure Ollama

```bash
ollama pull llama3.1:8b
ollama serve
```

### 3. Configure Wikidata access

The default MCP endpoint is:

```powershell
$env:WIKIDATA_MCP_URL="https://wd-mcp.wmcloud.org/mcp/"
$env:WIKIDATA_USER_AGENT="hybrid-pipelines-agent/1.0"
```

Wikidata MCP is mandatory and is the only evidence source. If it is unavailable, the request fails explicitly.

### 4. Run the API

```bash
python -m hybrid_pipelines
```

The service listens on `http://127.0.0.1:5050`.

## Troubleshooting

- `/health` returns `503`: inspect the `llm` and `wikidata_mcp` sections to see which dependency is unavailable.
- Ollama timeout: increase `OLLAMA_TIMEOUT_SECONDS` or reduce `OLLAMA_NUM_PREDICT`.
- Wikidata timeout or rate limiting: increase `WIKIDATA_TIMEOUT_SECONDS`, keep a descriptive `WIKIDATA_USER_AGENT`, or adjust the MCP retry settings.
- Empty or weak RDF: inspect `ANALYZE_LOG_PATH` and `OLLAMA_CSV_PATH` to see entity extraction output, resolved entities, and RDF prompt content.
