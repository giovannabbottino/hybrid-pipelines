# Prompt documentation

The hybrid pipeline uses one system prompt and three task prompts:

- `prompt/system/agent.txt`
- `prompt/prompts/entity-extraction.txt`
- `prompt/prompts/candidate-disambiguation.txt`
- `prompt/prompts/rdf-build.txt`

The LLM calls extract entity mentions, disambiguate Wikidata candidate groups, and build structured RDF triples from the selected entities and Wikidata evidence. The application serializes the triples to Turtle.

## System prompt

File: `prompt/system/agent.txt`

The system prompt defines the model as a Wikidata-grounded knowledge graph construction agent. It enforces machine-readable output:

- return only the format requested by the task prompt;
- do not include markdown fences;
- for RDF construction, return only the structured triple JSON object;
- avoid introductions, explanations, examples, templates, and notes;
- when building RDF, include `rdfs:label` for every subject and object resource;
- prefer entity-to-entity triples and a small stable predicate vocabulary so generated graphs can be evaluated with label-based SPARQL.

This prompt is used for entity extraction, candidate disambiguation, and RDF construction.

## Entity extraction prompt

File: `prompt/prompts/entity-extraction.txt`

This prompt asks the model to extract entity and concept mentions from the input text.

Runtime placeholder:

```text
${TEXT}
```

The service replaces `${TEXT}` with the request `text` before sending the prompt to Ollama.

Expected output:

```json
{
  "entities": [
    {
      "surface": "exact text span",
      "start": 0,
      "end": 5,
      "entity_type": "Entity|Class|Concept|Person|Organization|Place|Event|Disease|Taxon|Work|Product",
      "confidence": 0.0
    }
  ]
}
```

The service parses this JSON strictly. Invalid JSON, a non-object response, or an empty set of usable mentions fails the request.

Entity extraction always calls the LLM. The service realigns model mentions with nonempty surfaces to the source text and supplements supported descriptor and numbered-concept patterns. Mentions are deduplicated by case-insensitive surface form and offsets, then limited by `ENTITY_MENTION_LIMIT`. Both the application default and the current `.env` use 10.

## Candidate disambiguation prompt

File: `prompt/prompts/candidate-disambiguation.txt`

This prompt receives the original text, indexed candidate groups, compact P31/P279 evidence, and textual paths of at most two hops. It must return exactly one supplied QID for every group. The Ollama request supplies the same strict JSON Schema through its `format` field, preventing RDF, JSON-LD, descriptive fields, and other response shapes at generation time. The service still validates every index and QID, accumulates valid selections, and retries only pending groups for up to three attempts. It never selects a candidate automatically; remaining missing, malformed, duplicate, extra, or cross-group selections produce HTTP 422.

Runtime placeholder:

```text
${PAYLOAD}
```

Expected output:

```json
{
  "selections": [
    {
      "mention_index": 0,
      "selected_id": "Q312"
    }
  ]
}
```

## RDF build prompt

File: `prompt/prompts/rdf-build.txt`

This prompt asks the model to build structured RDF triples using a JSON payload prepared by the service.

Runtime placeholder:

```text
${PAYLOAD}
```

The payload includes:

```json
{
  "text": "original input text",
  "source_attribution": "Source: Wikidata",
  "entities": [],
  "relationships": []
}
```

Entities are compacted before they are sent to the model. Statement lists are sorted so priority properties appear first, then truncated to keep the prompt smaller.

The RDF-build response is a JSON object with a non-empty `triples` array. Every item has
`subject`, `predicate`, `object`, and `object_type`; literal items may also have `language` or
`datatype`.

Important structured RDF rules:

- return JSON only without Turtle, prefixes, Markdown, or prose;
- use `object_type: "resource"` for identifiers and `object_type: "literal"` for values;
- use `rdfs:label` literals for every `wd:Q...` and generated `kg:` resource;
- prefer entity-to-entity triples over literal-only descriptions so SPARQL evaluation can traverse from a subject label to an answer label;
- use `kg:is` for "entity is description entity", type, class, category, and instance-of relationships unless a more specific provided Wikidata relationship is directly supported;
- prefer the shared predicate vocabulary when it fits the text: `kg:is`, `kg:of`, `kg:from`, `kg:in`, `kg:on`, `kg:to`, `kg:with`, `kg:has_part`, `kg:part_of`, `kg:located_in`, and `kg:instance_of`;
- do not invent Wikidata QIDs;
- use only QIDs from the provided entities or relationships;
- use `kg:negated true` when the input sentence expresses negation.

## Evaluation-oriented output

The hybrid RDF is compared with dataset-derived SPARQL questions. Those queries are answer-oriented and use `rdfs:label` as the stable comparison surface, so the generated graph does not need to copy the reference RDF's exact QIDs or predicate names to receive credit. It does need to expose the same answer as a labeled reachable resource.

For good evaluation behavior, the RDF build prompt should keep these properties stable:

- every `wd:Q...` or generated `kg:` subject/object has an `rdfs:label`;
- the main resolved entity is connected to answer entities or concepts through direct or short paths;
- relationship predicates stay close to the shared `kg:` vocabulary;
- Wikidata QIDs are used only when provided by the entities or relationships payload;
- generated `kg:` resources are acceptable for concepts that are present in the text but not resolved to provided QIDs.

## Runtime construction and validation

The structured response is validated, unsafe `kg:` names are normalized, and terms are added to an
RDFLib graph. Existing hybrid post-processing preserves approved Wikidata IDs, materializes supplied
relationships, and completes human-readable labels. The graph is serialized to Turtle and parsed
before it is returned. Invalid structured responses are retried up to three times.

## Editing guidelines

When editing these prompts:

- keep `${TEXT}` in the entity extraction prompt;
- keep `${PAYLOAD}` in the candidate disambiguation prompt;
- keep `${PAYLOAD}` in the RDF build prompt;
- keep the structured triple fields identical to the prompt-based and ontology-based pipelines;
- keep the entity extraction output as strict JSON;
- keep structured JSON output instructions concise and strict;
- keep labels mandatory for all subject/object resources;
- keep entity-to-entity relationships preferred for facts that downstream SPARQL should be able to test;
- keep `kg:is` as the default classification/type predicate;
- update both prompt-based and hybrid prompt docs together when changing the shared predicate vocabulary;
- do not ask for markdown fences, explanations, or examples in the final output.
