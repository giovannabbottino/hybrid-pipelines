# Analyze

## `GET /health`

Checks the two external dependencies used by the agent:

- `llm` - Ollama `/api/tags`
- `wikidata_mcp` - Wikidata lookup through the configured MCP endpoint

The endpoint returns `200` when every dependency reports `status: ok`; otherwise it returns `503`.

Example response:

```json
{
  "llm": {
    "status": "ok",
    "model": "llama3.1:8b"
  },
  "wikidata_mcp": {
    "status": "ok",
    "url": "https://wd-mcp.wmcloud.org/mcp/"
  }
}
```

## `POST /analyze`

Runs the hybrid Wikidata-grounded knowledge graph pipeline.

### Request body

```json
{
  "text": "Mango is not a fruit from a tree.",
  "idempotence_key": "optional-stable-key",
  "max_rdf_attempts": 3
}
```

Fields:

| Field | Required | Description |
|-------|----------|-------------|
| `text` | Yes | Source text to analyze. Leading and trailing whitespace is stripped. |
| `idempotence_key` | No | Stable key used to group events in `ANALYZE_LOG_PATH`. If omitted, the service generates a UUID. |
| `max_rdf_attempts` | No | Number of RDF generation attempts, clamped from 1 to 3. Defaults to 3. Every retry uses the same LLM stage. |

### Behavior

1. Load the system prompt and entity extraction prompt.
2. Replace `${TEXT}` with the request text.
3. Ask the LLM to return strict JSON with entity/concept mentions.
4. Strictly parse the JSON response. Invalid JSON, a non-object response, or no usable mentions fails the request.
5. Realign model mentions to the text and supplement supported descriptor and numbered-concept patterns.
6. Deduplicate mentions and keep at most `ENTITY_MENTION_LIMIT` (10 in the current `.env` and application default).
7. Retrieve a candidate group for every mention through Wikidata MCP `search_items`.
8. Fetch statements for every candidate through MCP `get_statements` and use P31/P279 evidence to rank ontology-compatible candidates.

Wikidata MCP is the only evidence source. An MCP failure fails the request.
9. Build paths of at most two hops between candidates from different mention groups. Intermediate nodes above the configured local-degree threshold are excluded.
10. Translate each path to text and ask the LLM to select exactly one supplied QID for every non-empty candidate group. Valid partial selections are accumulated and only pending groups are retried, for at most three attempts. The service never chooses a candidate automatically; an unknown, malformed, duplicate, extra, cross-group, or ultimately missing QID returns HTTP 422.
11. Keep direct relationships where a selected entity statement points to another selected entity.
12. Load the RDF build prompt, inject a JSON payload with text, source attribution, compact selected entities, and relationships.
13. Ask the LLM to return RDF/Turtle and strip code fences or trailing notes when present.
14. Strictly validate the RDF with `rdflib.Graph.parse(format="turtle")`. If parsing fails, retry the same model stage with both the parser error and the previous invalid RDF. Return immediately when parsing succeeds; no local RDF repair or deterministic substitute is attempted.
15. Return the analysis response, including auditable NED candidates and paths, and write request events/LLM CSV logs when configured.

### Success response

```json
{
  "text": "Mango is not a fruit from a tree.",
  "entities": [
    {
      "mention": {
        "surface": "Mango",
        "start": 0,
        "end": 5,
        "entity_type": "Entity",
        "confidence": 0.2
      },
      "id": "Q3919027",
      "iri": "http://www.wikidata.org/entity/Q3919027",
      "label": "mango",
      "description": "edible stone fruit of Mangifera",
      "score": 1.0,
      "statements": []
    }
  ],
  "relationships": [
    {
      "subject_id": "Q3919027",
      "subject_label": "mango",
      "property_id": "P31",
      "property_label": "instance of",
      "object_id": "Q3314483",
      "object_label": "fruit",
      "source": "wikidata"
    }
  ],
  "rdf": "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n@prefix wd: <http://www.wikidata.org/entity/> .\n@prefix kg: <https://example.org/wikidata-description/> .\n...",
  "source_attribution": "Source: Wikidata",
  "ned": {
    "candidate_groups": [],
    "paths": []
  },
  "llm": {
    "entity_extraction": "{\"entities\":[{\"surface\":\"Mango\",\"start\":0,\"end\":5}]}",
    "candidate_disambiguation": "{\"selections\":[{\"mention_index\":0,\"selected_id\":\"Q3919027\"}]}"
  }
}
```

### Error responses

| Status | Cause | Response shape |
|--------|-------|----------------|
| `400` | Missing or blank `text`, or invalid local prompt path | `{ "error": "..." }` |
| `502` | External service request failed, model request failed, or runtime generation error | `{ "error": "...", "details": "..." }` |
| `422` | The model did not complete strict candidate disambiguation or did not return valid Turtle RDF after the configured attempts | `{ "error": "Candidate disambiguation failed.", "attempts": 3, "details": "..." }` or `{ "error": "RDF parsing failed.", "attempts": 3, "details": "..." }` |
| `504` | External service timeout | `{ "error": "External service request timed out.", "details": "...", "hint": "..." }` |

## Logs

- Ollama generations are written to `OLLAMA_CSV_PATH` with `stage`, `model`, `prompt`, `response`, `created_at`, `done`, and `total_duration`.
- Agent events are written to `ANALYZE_LOG_PATH` when configured. Events share the provided or generated `idempotence_key`.
