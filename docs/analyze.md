# API

## GET /health

Checks Ollama connectivity and MCP initialization/tool discovery. Returns 200 when both are reachable, otherwise 503. Connectivity alone does not certify that the selected model supports tool calling.

## POST /analyze

```json
{"text":"Mango is a fruit.","max_processing_seconds":540,"idempotence_key":"demo-1"}
```

The text is required and trimmed. The optional processing budget must be positive and finite. The idempotence key groups logs; it does not prevent reprocessing.

1. **NED:** the LLM identifies mentions, chooses MCP tools and arguments, and resolves Wikidata IDs. The host discovers tool schemas from the server and forwards tool results into the conversation. There is no local search sequence or candidate ranking.
2. **RDF:** the LLM receives the validated entities and keeps the MCP evidence in the conversation. Tools are disabled during this stage.

NED must return at least one mention with exact text offsets, a label, and an ID found in successful MCP results (or null when unresolved). It must make at least one successful MCP consultation. Membership in retrieved evidence does not prove that an ID is semantically correct for a mention. Three invalid NED answers stop processing; separate defaults limit NED to 20 turns and 40 tool calls.

RDF must be a JSON object containing only a non-empty `rdf` Turtle string, with the required `rdfs`, `wd`, and `kg` declarations. Its Wikidata resources must match the IDs resolved during NED. Invalid output receives feedback, up to three total RDF attempts. The application does not add triples, infer cooccurrence relations, or repair labels.

The response contains `text`, `entities`, `rdf`, `tool_calls` (names, arguments, results), and `source_attribution`. Each entity contains `mention` (surface, start, end), `id`, `iri`, and `label`. Unresolved IDs and IRIs are null. Offsets are zero-based, end-exclusive character indices in the trimmed text.

**Migration:** the former `relationships`, `ned`, and `llm` response fields were removed. Relationships are represented in the RDF; MCP evidence is available in `tool_calls`.

| Status | Meaning |
|---|---|
| 200 | Analysis completed. |
| 400 | Invalid request. |
| 422 | NED or RDF validation limit reached. |
| 502 | External failure, unavailable tool, or tool-call limit exceeded. |
| 504 | Timeout or processing budget exhausted. |

See [.env.example](../.env.example) for configuration. Logs are written to `logs/analyze_log.jsonl` and `logs/ollama_responses.jsonl`.
