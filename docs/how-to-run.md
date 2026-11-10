# Running

Requires Python 3.12, uv, a running Ollama instance, and access to Wikidata MCP. Run commands from the repository root.

```powershell
uv venv --python 3.12 --seed .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[test,lint]"
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
ollama pull llama3.1:8b
python -m hybrid_pipelines
```

If `.env` already exists, edit it without overwriting your settings. Use [.env.example](../.env.example) as the reference; there is no need to set parameters in the terminal. The application loads `.env` and listens on `http://127.0.0.1:5050`.

```powershell
Invoke-RestMethod http://127.0.0.1:5050/health
```

If the health check returns 503, check which dependency is unavailable. Adjust the model, URLs, limits, and timeouts in `.env`. Runtime logs are written to `logs/`.
