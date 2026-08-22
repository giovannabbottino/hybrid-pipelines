# Hybrid Pipelines Wikidata Agent

Flask API that builds a knowledge graph by combining LLM prompts with Wikidata evidence. The service extracts entity mentions from input text, resolves them through Wikidata MCP or the Wikidata Action API fallback, finds direct relationships among resolved entities, and asks the LLM to generate RDF/Turtle.

## Flow

1. The LLM extracts entity and concept mentions from the input text.
2. The service realigns extracted mentions, supplements supported lexical patterns, and deduplicates them. If the parsed response contains no nonempty mention surfaces, it first recovers mentions heuristically from the text.
3. Each mention is resolved to Wikidata candidates, limited by `WIKIDATA_CANDIDATE_LIMIT`.
4. Statements for resolved entities are fetched from Wikidata.
5. Direct relationships among resolved entities are retained as evidence.
6. The LLM receives the text, compact entity evidence, and relationships, then returns RDF/Turtle.
7. The RDF is cleaned, validated with `rdflib`, and retried or lightly repaired before being returned.

## Project Layout

- `src/hybrid_pipelines/` - importable application package, organized into controller, application, domain, and infrastructure layers.
- `prompt/system/` - System prompt for the Wikidata-grounded agent.
- `prompt/prompts/` - Task prompts for entity extraction and RDF construction.
- `docs/` - Endpoint, run, test, prompt, and sequence documentation.
- `tests/` - Unit tests for service behavior and prompt constraints.

## API Summary

### `GET /health`

Checks Ollama and Wikidata availability. Returns `200` only when all checked parts report `status: ok`; otherwise returns `503`.

### `POST /analyze`

Request:

```json
{
  "text": "Mango is not a fruit from a tree.",
  "idempotence_key": "optional-stable-key",
  "max_rdf_attempts": 1
}
```

Response:

```json
{
  "text": "Mango is not a fruit from a tree.",
  "entities": [],
  "relationships": [],
  "rdf": "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n...",
  "source_attribution": "Source: Wikidata",
  "llm": {
    "entity_extraction": "{\"entities\": []}"
  }
}
```

See [docs/analyze.md](docs/analyze.md) for the full endpoint contract.
See [docs/prompt.md](docs/prompt.md) for the prompt structure and editing guidelines.

## Configuration

| Variable | Default |
|---|---|
| `SYSTEM_PROMPT_NAME` | `system/agent.txt` |
| `ENTITY_EXTRACTION_PROMPT_NAME` | `prompts/entity-extraction.txt` |
| `RDF_BUILD_PROMPT_NAME` | `prompts/rdf-build.txt` |
| `WIKIDATA_MCP_URL` | `https://wd-mcp.wmcloud.org/mcp/` |
| `WIKIDATA_LANGUAGE` | `en` |
| `WIKIDATA_TIMEOUT_SECONDS` | `60` |
| `WIKIDATA_ACTION_API_URL` | `https://www.wikidata.org/w/api.php` |
| `WIKIDATA_CANDIDATE_LIMIT` | `3` |
| `WIKIDATA_ALLOW_ACTION_API_FALLBACK` | `true` |
| `WIKIDATA_USER_AGENT` | `hybrid-pipelines-agent/1.0` |
| `WIKIDATA_MAXLAG` | `5` |
| `WIKIDATA_MAX_RETRIES` | `2` |
| `WIKIDATA_RETRY_BACKOFF_SECONDS` | `2` |
| `OLLAMA_API_URL` | `http://localhost:11434` |
| `OLLAMA_MODEL` | `llama3:8b` |
| `OLLAMA_CSV_PATH` | `data/ollama_responses.csv` |
| `OLLAMA_TIMEOUT_SECONDS` | `300` |
| `ENTITY_MENTION_LIMIT` | `10` (lower values reduce sequential Wikidata lookups) |
| `ANALYZE_LOG_PATH` | `data/analyze_log.jsonl` |

Optional Ollama generation options are also supported: `OLLAMA_SEED`, `OLLAMA_TEMPERATURE`, `OLLAMA_TOP_K`, `OLLAMA_TOP_P`, `OLLAMA_MIN_P`, `OLLAMA_STOP`, `OLLAMA_NUM_CTX`, and `OLLAMA_NUM_PREDICT`.

### Configured profile

Entity extraction always uses the LLM. The current `.env` configures the following generation and mention limits:

```env
ENTITY_MENTION_LIMIT=16
OLLAMA_NUM_PREDICT=1536
OLLAMA_TEMPERATURE=0
```

With this profile, a successful request normally makes two LLM calls: one for entity extraction and one for RDF generation. RDF generation defaults to one attempt. A client can explicitly set `max_rdf_attempts` to `2` or `3` when additional repair attempts are needed. When deterministic RDF is explicitly preferred and succeeds, only the entity-extraction LLM call is made.

## Run

```bash
python -m pip install -e .
python -m hybrid_pipelines
```

The service listens on `http://127.0.0.1:5050`.

See [docs/how-to-run.md](docs/how-to-run.md) for Docker Compose and local setup details.
