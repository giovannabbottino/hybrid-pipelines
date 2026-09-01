# Prompt documentation

The hybrid pipeline uses one system prompt and three task prompts:

- `prompt/system/agent.txt`
- `prompt/prompts/entity-extraction.txt`
- `prompt/prompts/candidate-disambiguation.txt`
- `prompt/prompts/rdf-build.txt`

The LLM calls extract entity mentions, disambiguate Wikidata candidate groups, and build RDF/Turtle from the selected entities and Wikidata evidence.

## System prompt

File: `prompt/system/agent.txt`

The system prompt defines the model as a Wikidata-grounded knowledge graph construction agent. It enforces machine-readable output:

- return only the format requested by the task prompt;
- do not include markdown fences;
- when asked for Turtle/RDF, return Turtle syntax only;
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

This prompt asks the model to build RDF/Turtle using a JSON payload prepared by the service.

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

The RDF prompt requires the response to start with these prefixes:

```turtle
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix wd: <http://www.wikidata.org/entity/> .
@prefix kg: <https://example.org/wikidata-description/> .
```

Important RDF rules:

- the first character must be `@`;
- return valid Turtle only;
- do not use markdown or code fences;
- declare every used prefix in the same response before its first use;
- include the exact `kg:` declaration whenever a `kg:` name is used;
- never place two objects next to each other without Turtle punctuation;
- use `,` only for multiple objects of the same predicate;
- use `;` for a different predicate on the same subject and write that predicate after `;`;
- terminate each subject statement with `.`;
- use `rdfs:label` for every `wd:Q...` entity in the graph;
- use `rdfs:label` for every generated `kg:` entity, concept, class, place, person, organization, event, and object that appears as a subject or object;
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

## Runtime cleanup and validation

The RDF returned by Ollama has response wrappers removed and is then parsed strictly:

- fenced code blocks are unwrapped;
- text before the first `@prefix` is removed;
- trailing notes or explanations are removed when they start with common note markers.
- the candidate RDF is parsed with `rdflib.Graph.parse(format="turtle")`;
- when parsing fails and attempts remain, the same model stage is asked to return corrected Turtle using the parser error and previous invalid RDF. The API defaults to three attempts;
- no local syntax repair, statement salvage, or deterministic substitute is used after an LLM failure.

If every attempt fails, the API returns an RDF parse error instead of invalid Turtle.

## Editing guidelines

When editing these prompts:

- keep `${TEXT}` in the entity extraction prompt;
- keep `${PAYLOAD}` in the candidate disambiguation prompt;
- keep `${PAYLOAD}` in the RDF build prompt;
- keep the required RDF prefixes declared if the prompt or examples use them;
- avoid introducing undeclared prefixes such as `ex:`;
- keep the shared mandatory Turtle syntax block identical to the prompt-based and ontology-based pipelines;
- keep the entity extraction output as strict JSON;
- keep RDF output instructions concise and strict;
- keep labels mandatory for all subject/object resources;
- keep entity-to-entity relationships preferred for facts that downstream SPARQL should be able to test;
- keep `kg:is` as the default classification/type predicate;
- update both prompt-based and hybrid prompt docs together when changing the shared predicate vocabulary;
- do not ask for markdown fences, explanations, or examples in the final output.
