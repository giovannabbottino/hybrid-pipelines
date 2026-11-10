# API

## GET /health

Checks Ollama and Wikidata MCP. Returns 200 when both are available; otherwise, returns 503 with each dependency's status.

## POST /analyze

```json
{"text":"Mango is a fruit.","max_processing_seconds":540,"idempotence_key":"demo-1"}
```

`text` is required and has leading and trailing whitespace removed. `max_processing_seconds` must be a positive finite number and overrides the request's processing time budget. `idempotence_key` is an optional string for grouping logs; it does not prevent reprocessing.

The pipeline extracts mentions, retrieves Wikidata candidates and statements, asks the LLM to select QIDs, and identifies direct relationships. Disambiguation allows up to three attempts, resending only pending groups and never selecting candidates automatically.

For RDF generation, the LLM must return a JSON object containing only the `rdf` key, with a non-empty valid Turtle string and the required `rdfs`, `wd`, and `kg` prefixes. Empty or invalid JSON or Turtle triggers another attempt, up to a fixed limit of three total attempts, subject to the processing time budget. Each retry includes the validation error.

After validation, the application removes unresolved QIDs, materializes Wikidata relationships, adds text cooccurrence relationships, and completes labels. The response includes `text`, `entities`, `relationships`, `rdf`, `source_attribution`, `ned`, and `llm`. The `rdf` field is a Turtle string.

| Status | Meaning |
|---|---|
| 200 | Analysis completed. |
| 400 | Invalid request. |
| 422 | Disambiguation or RDF validation attempts exhausted. |
| 502 | External service failure. |
| 504 | Timeout or processing time budget exhausted. |

Use [.env.example](../.env.example) as the configuration reference; the application loads `.env`. Logs are written to `logs/analyze_log.jsonl` and `logs/ollama_responses.jsonl`.
