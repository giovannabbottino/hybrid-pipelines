# How to run

## Environment variables

The application loads `.env` when running locally and also accepts regular environment variables. Docker Compose sets defaults for the containerized service.

- `SYSTEM_PROMPT_NAME=system/agent.txt`
- `ENTITY_EXTRACTION_PROMPT_NAME=prompts/entity-extraction.txt`
- `RDF_BUILD_PROMPT_NAME=prompts/rdf-build.txt`
- `OLLAMA_API_URL=http://localhost:11434`
- `OLLAMA_MODEL=llama3:8b`
- `OLLAMA_CSV_PATH=data/ollama_responses.csv`
- `OLLAMA_TIMEOUT_SECONDS=300`
- `ANALYZE_LOG_PATH=data/analyze_log.jsonl`
- `WIKIDATA_MCP_URL=https://wd-mcp.wmcloud.org/mcp/`
- `WIKIDATA_LANGUAGE=en`
- `WIKIDATA_TIMEOUT_SECONDS=60`
- `WIKIDATA_ACTION_API_URL=https://www.wikidata.org/w/api.php`
- `WIKIDATA_CANDIDATE_LIMIT=3`
- `WIKIDATA_ALLOW_ACTION_API_FALLBACK=true`
- `WIKIDATA_USER_AGENT=hybrid-pipelines-agent/1.0`
- `WIKIDATA_MAXLAG=5`
- `WIKIDATA_MAX_RETRIES=2`
- `WIKIDATA_RETRY_BACKOFF_SECONDS=2`

Optional Ollama generation options are ignored when blank: `OLLAMA_SEED`, `OLLAMA_TEMPERATURE`, `OLLAMA_TOP_K`, `OLLAMA_TOP_P`, `OLLAMA_MIN_P`, `OLLAMA_STOP`, `OLLAMA_NUM_CTX`, and `OLLAMA_NUM_PREDICT`.

## Requirements

- Python >=3.10
- Ollama with the configured model installed
- Network access to Wikidata MCP or to the Wikidata Action API fallback

## Run with Docker Compose

From the repository root:

```powershell
docker compose up --build -d hybrid-pipelines
```

On first run, pull the configured model into the Ollama container:

```powershell
docker exec -it kg-ollama ollama pull llama3:8b
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
pip install -r requirements.txt
```

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. Configure Ollama

```bash
ollama pull llama3:8b
ollama serve
```

### 3. Configure Wikidata access

The default MCP endpoint is:

```powershell
$env:WIKIDATA_MCP_URL="https://wd-mcp.wmcloud.org/mcp/"
$env:WIKIDATA_USER_AGENT="hybrid-pipelines-agent/1.0"
$env:WIKIDATA_MAXLAG="5"
```

The client also supports a Wikidata Action API fallback. Disable it only when you want MCP failures to fail the request immediately:

```powershell
$env:WIKIDATA_ALLOW_ACTION_API_FALLBACK="false"
```

### 4. Run the API

```bash
python -m src.app
```

The service listens on `http://127.0.0.1:5050`.

## Troubleshooting

- `/health` returns `503`: inspect the `llm` and `wikidata_mcp` sections to see which dependency is unavailable.
- Ollama timeout: increase `OLLAMA_TIMEOUT_SECONDS` or reduce `OLLAMA_NUM_PREDICT`.
- Wikidata timeout or rate limiting: increase `WIKIDATA_TIMEOUT_SECONDS`, keep a descriptive `WIKIDATA_USER_AGENT`, and leave `WIKIDATA_MAXLAG` enabled.
- Empty or weak RDF: inspect `ANALYZE_LOG_PATH` and `OLLAMA_CSV_PATH` to see entity extraction output, resolved entities, and RDF prompt content.
