# Analyze

`POST /analyze` triggers the Wikidata agent.

## Request

```json
{
  "text": "Mango is not a fruit from a tree.",
  "idempotence_key": "optional"
}
```

## Behavior

1. The LLM extracts entity and concept mentions.
2. The service resolves each mention through the configured Wikidata MCP `search_items` tool.
3. The service fetches structural information through the Wikidata MCP `get_statements` tool.
4. Direct relationships among resolved entities are retained as evidence.
5. The LLM receives the text, resolved entities, and relationships, then returns RDF/Turtle.

## Response

```json
{
  "text": "Mango is not a fruit from a tree.",
  "entities": [
    {
      "mention": {"surface": "Mango", "start": 0, "end": 5},
      "id": "Q...",
      "iri": "http://www.wikidata.org/entity/Q...",
      "label": "Mango",
      "description": "...",
      "score": 1.0,
      "statements": []
    }
  ],
  "relationships": [
    {
      "subject_id": "Q...",
      "property_id": "P...",
      "object_id": "Q..."
    }
  ],
  "rdf": "@prefix ex: <http://example.org/hybrid/> .",
  "llm": {
    "entity_extraction": "{}"
  }
}
```
