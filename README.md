# Hybrid Pipelines Wikidata Agent

Flask API that exposes a single agent endpoint:

```bash
curl -X POST http://127.0.0.1:5050/analyze \
  -H "Content-Type: application/json" \
  -d "{\"text\":\"Mango is not a fruit from a tree.\"}"
```

The agent flow is intentionally simple:

1. Use the LLM to extract entities and concepts from the input text.
2. Use the configured Wikidata MCP server to search and inspect those entities.
3. Find direct Wikidata relationships between the resolved entities.
4. Ask the LLM to build RDF/Turtle from the text and Wikidata evidence.

## Configuration

The Wikidata MCP server is configured with:

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

The API uses the same endpoint through environment variables:

| Variable | Default |
|---|---|
| `SYSTEM_PROMPT_NAME` | `system/agent.txt` |
| `ENTITY_EXTRACTION_PROMPT_NAME` | `prompts/entity-extraction.txt` |
| `RDF_BUILD_PROMPT_NAME` | `prompts/rdf-build.txt` |
| `WIKIDATA_MCP_URL` | `https://wd-mcp.wmcloud.org/mcp/` |
| `WIKIDATA_LANGUAGE` | `en` |
| `WIKIDATA_TIMEOUT_SECONDS` | `60` |
| `WIKIDATA_CANDIDATE_LIMIT` | `3` |
| `WIKIDATA_ALLOW_ACTION_API_FALLBACK` | `true` |
| `WIKIDATA_USER_AGENT` | `hybrid-pipelines-agent/1.0` |
| `WIKIDATA_MAXLAG` | `5` |
| `WIKIDATA_MAX_RETRIES` | `2` |
| `WIKIDATA_RETRY_BACKOFF_SECONDS` | `2` |
| `OLLAMA_API_URL` | `http://localhost:11434` |
| `OLLAMA_MODEL` | `llama3:8b` |
| `OLLAMA_CSV_PATH` | `data/ollama_responses.csv` |
| `ANALYZE_LOG_PATH` | `data/analyze_log.jsonl` |

Prompt files live under `prompt/`. Keep reusable task prompts in `prompt/prompts/` and system prompts in `prompt/system/`.

Wikidata access follows the public access guidance: use Wikidata MCP for agent workflows, send a clear User-Agent, request gzip/deflate responses, pass `maxlag` to Action API fallback calls, and back off on `429 Too Many Requests`.

## Run

```bash
pip install -r requirements.txt
python -m src.app
```

The service listens on `http://127.0.0.1:5050`.

## Response Shape

```json
{
  "text": "Mango is not a fruit from a tree.",
  "entities": [],
  "relationships": [],
  "rdf": "@prefix ...",
  "llm": {}
}
```
