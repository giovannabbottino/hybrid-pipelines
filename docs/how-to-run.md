# Run guide

[README](../README.md) · [API](analyze.md) · [Testing](how-to-test.md)

## Requirements

Use Python 3.12. A running Ollama instance with a model that supports native tool calling, plus access to Wikidata MCP. Run all commands from `hybrid-pipelines/` in the combined workspace, or the repository root if this project is checked out separately.

## Install

PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

Bash (Linux/macOS):

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

## Configure and start

Copy [.env.example](../.env.example) only if `.env` does not exist:

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

Bash equivalent: `test -f .env || cp .env.example .env`.

Edit the model, endpoints, limits, and log paths in `.env`. The application loads this file automatically. Keep Ollama running (start `ollama serve` in a separate terminal if necessary), then run:

```powershell
ollama pull llama3.1:8b
python -m hybrid_pipelines
```

If you change the model in `.env`, pull that model instead. The default address is `http://127.0.0.1:5050`.

## Verify

From another terminal:

```powershell
Invoke-RestMethod http://127.0.0.1:5050/health
```

Bash equivalent: `curl http://127.0.0.1:5050/health`. Once healthy, send the [README request](../README.md#usage). See the [API](analyze.md) for error codes.

## Pipeline settings

The official MCP SDK discovers the tool schemas. `WIKIDATA_MCP_TOOLS` selects exposed tools; `MCP_MAX_TURNS` and `MCP_MAX_TOOL_CALLS` bound NED. `ANALYZE_TIMEOUT_SECONDS` is the complete analysis budget (540 seconds by default), while `OLLAMA_TIMEOUT_SECONDS` limits each call. RDF generation has a fixed limit of three attempts.

When migrating from the earlier pipeline, extraction, candidate-limit, mention-limit, and disambiguation-prompt settings are no longer used. Configure `NED_PROMPT_NAME` instead.

## Logs and troubleshooting

Runtime log directories are created automatically. Inspect `logs/analyze_log.jsonl` for request events and `logs/ollama_responses.jsonl` for model responses.

- For HTTP 503, inspect the health response and check the configured dependencies.
- For timeouts, check the service endpoints and adjust the relevant timeout in `.env`.
- For invalid or truncated RDF, inspect the validation error and generation token limit.
- After changing dependencies, reinstall with `python -m pip install -e ".[dev]"`.
