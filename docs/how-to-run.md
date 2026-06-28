# How To Run

## Install

```bash
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Configure Ollama

```bash
ollama pull llama3:8b
ollama serve
```

Optional variables:

```powershell
$env:OLLAMA_API_URL="http://localhost:11434"
$env:OLLAMA_MODEL="llama3:8b"
$env:OLLAMA_TIMEOUT_SECONDS="300"
$env:SYSTEM_PROMPT_NAME="system/agent.txt"
$env:ENTITY_EXTRACTION_PROMPT_NAME="prompts/entity-extraction.txt"
$env:RDF_BUILD_PROMPT_NAME="prompts/rdf-build.txt"
```

## Configure Wikidata MCP

The default endpoint is:

```powershell
$env:WIKIDATA_MCP_URL="https://wd-mcp.wmcloud.org/mcp/"
$env:WIKIDATA_USER_AGENT="hybrid-pipelines-agent/1.0"
$env:WIKIDATA_MAXLAG="5"
```

This matches the MCP client configuration:

```json
{
  "mcpServers": {
    "wikidata": {
      "type": "streamable_http",
      "url": "https://wd-mcp.wmcloud.org/mcp/"
    }
  }
}
```

## Start

```bash
python -m src.app
```

## Call

```bash
curl -X POST http://127.0.0.1:5050/analyze \
  -H "Content-Type: application/json" \
  -d "{\"text\":\"Mango is not a fruit from a tree.\"}"
```
