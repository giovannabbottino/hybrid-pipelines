# Hybrid Pipelines Wikidata Agent

Flask API that builds a knowledge graph by combining LLM prompts with Wikidata evidence. The service extracts entity mentions from input text, resolves them exclusively through Wikidata MCP, finds direct relationships among resolved entities, and asks the LLM to generate RDF/Turtle. Dependency or validation failures are returned explicitly; the pipeline has no alternate data source or local recovery path.

This is the most explicitly grounded variant in the evaluation workspace. In
contrast with the prompt-only and ontology-focused services, it materializes
resolved entities, statements, and direct relationships before RDF generation.

## Quick start

Requirements: Python 3.12, Ollama with `llama3.1:8b`, and outbound
HTTPS access to the configured Wikidata MCP endpoint.

```bash
ollama pull llama3.1:8b
python -m pip install -e ".[test,lint]"
python -m hybrid_pipelines
```

In another terminal:

```bash
curl -X POST http://127.0.0.1:5050/analyze \
  -H "Content-Type: application/json" \
  -d '{"text":"Mango is a tropical fruit.","max_rdf_attempts":3}'
```

## Flow

1. The LLM extracts entity and concept mentions from the input text.
2. The service strictly parses the extraction response, realigns extracted mentions, supplements supported lexical patterns, and deduplicates them. Invalid JSON or an empty extraction fails the request.
3. Each mention is expanded to a Wikidata candidate group, limited by `WIKIDATA_CANDIDATE_LIMIT`; no candidate is selected yet.
4. Wikidata statements are fetched for every candidate, and cross-mention paths of at most two hops are built from the bounded local subgraph. High-degree intermediate nodes are excluded.
5. The LLM receives the original text, candidate groups, type evidence, and textual path context, then selects exactly one supplied QID per non-empty group. Missing or invalid selections are retried through the LLM for the pending groups only; the service never chooses a candidate automatically.
6. Direct relationships among the selected entities are retained as evidence.
7. The LLM receives the text, selected entity evidence, and relationships, then returns RDF/Turtle.
8. The RDF is normalized to remove response wrappers and strictly validated with `rdflib`. Invalid RDF is retried only through the same LLM stage when additional attempts were requested.

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
  "max_rdf_attempts": 3,
  "max_processing_seconds": 540
}
```

`max_rdf_attempts` is clamped to 1-3 and defaults to 3.
`max_processing_seconds` optionally overrides the service-wide processing
budget for that request.

Response:

```json
{
  "text": "Mango is not a fruit from a tree.",
  "entities": [
    {
      "mention": {"surface": "Mango", "start": 0, "end": 5},
      "id": "Q169",
      "label": "mango"
    }
  ],
  "relationships": [],
  "rdf": "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n...",
  "source_attribution": "Source: Wikidata",
  "llm": {
    "entity_extraction": "{\"entities\":[{\"surface\":\"Mango\",\"start\":0,\"end\":5}]}"
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
| `CANDIDATE_DISAMBIGUATION_PROMPT_NAME` | `prompts/candidate-disambiguation.txt` |
| `RDF_BUILD_PROMPT_NAME` | `prompts/rdf-build.txt` |
| `WIKIDATA_MCP_URL` | `https://wd-mcp.wmcloud.org/mcp/` |
| `WIKIDATA_LANGUAGE` | `en` |
| `WIKIDATA_TIMEOUT_SECONDS` | `60` |
| `WIKIDATA_CANDIDATE_LIMIT` | `3` |
| `WIKIDATA_MAX_PATH_HOPS` | `2` |
| `WIKIDATA_HUB_DEGREE_THRESHOLD` | `25` |
| `WIKIDATA_PATH_EXPANSION_LIMIT` | `30` |
| `WIKIDATA_PATH_LIMIT` | `24` |
| `WIKIDATA_USER_AGENT` | `hybrid-pipelines-agent/1.0` |
| `WIKIDATA_MAX_RETRIES` | `2` |
| `WIKIDATA_RETRY_BACKOFF_SECONDS` | `2` |
| `OLLAMA_API_URL` | `http://localhost:11434` |
| `OLLAMA_MODEL` | `llama3.1:8b` |
| `OLLAMA_CSV_PATH` | `data/ollama_responses.csv` |
| `OLLAMA_TIMEOUT_SECONDS` | `300` |
| `OLLAMA_DISAMBIGUATION_NUM_PREDICT` | `512` |
| `ENTITY_MENTION_LIMIT` | `10` (lower values reduce sequential Wikidata lookups) |
| `ANALYZE_TIMEOUT_SECONDS` | `540` |
| `ANALYZE_LOG_PATH` | `data/analyze_log.jsonl` |

Optional Ollama generation options are also supported: `OLLAMA_SEED`, `OLLAMA_TEMPERATURE`, `OLLAMA_TOP_K`, `OLLAMA_TOP_P`, `OLLAMA_MIN_P`, `OLLAMA_STOP`, `OLLAMA_NUM_CTX`, and `OLLAMA_NUM_PREDICT`.

### Configured profile

Entity extraction always uses the LLM. The current `.env` configures the following generation and mention limits:

```env
ENTITY_MENTION_LIMIT=10
OLLAMA_NUM_CTX=8192
OLLAMA_NUM_PREDICT=1536
OLLAMA_DISAMBIGUATION_NUM_PREDICT=512
OLLAMA_TEMPERATURE=0
```

With this profile, a successful request normally makes three LLM calls: entity
extraction, candidate disambiguation, and RDF generation. Incomplete candidate
disambiguation is retried up to three times for pending groups only; there is no
heuristic candidate fallback. A client can set
`max_rdf_attempts` from `1` to `3`; retries repeat only the model-based RDF stage
after strict parser feedback. The service does not repair invalid Turtle or
substitute a deterministic graph locally.
Its system and RDF-build prompts use the same mandatory prefix-binding and
Turtle-punctuation block as the prompt-based and ontology-based pipelines.

## Run

```bash
python -m pip install -e .
python -m hybrid_pipelines
```

The service listens on `http://127.0.0.1:5050`.

See [docs/how-to-run.md](docs/how-to-run.md) for Docker Compose and local setup details.

Run the checks with:

```bash
python -m pytest
python -m ruff check .
python -m pyright
```
