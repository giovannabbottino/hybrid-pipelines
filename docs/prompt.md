# Prompt documentation

The hybrid pipeline uses one system prompt and two task prompts:

- `prompt/system/agent.txt`
- `prompt/prompts/entity-extraction.txt`
- `prompt/prompts/rdf-build.txt`

The first LLM call extracts entity mentions as JSON. The second LLM call builds RDF/Turtle from the original text plus Wikidata evidence.

## System prompt

File: `prompt/system/agent.txt`

The system prompt defines the model as a Wikidata-grounded knowledge graph construction agent. It enforces machine-readable output:

- return only the format requested by the task prompt;
- do not include markdown fences;
- when asked for Turtle/RDF, return Turtle syntax only;
- avoid introductions, explanations, examples, templates, and notes;
- when building RDF, include `rdfs:label` for every subject and object resource;
- prefer entity-to-entity triples and a small stable predicate vocabulary so generated graphs can be evaluated with label-based SPARQL.

This prompt is used for both entity extraction and RDF construction.

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
      "entity_type": "Entity|Class|Object|Concept|Place|Person|Organization",
      "confidence": 0.0
    }
  ]
}
```

The service parses this JSON and tolerates responses that contain JSON embedded in extra text, although the prompt asks for strict JSON only. Invalid or unparsable extraction output becomes an empty extraction result.

After the LLM extraction, the service adds heuristic mentions from non-stopword tokens in the text, deduplicates mentions by surface form, and keeps at most 10 mentions.

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
- do not use undeclared prefixes;
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

The RDF returned by Ollama is lightly cleaned and parsed before it is returned:

- fenced code blocks are unwrapped;
- text before the first `@prefix` is removed;
- trailing notes or explanations are removed when they start with common note markers.
- minor syntax repairs are tried, including normalized quotes, appending a final dot, and dropping an incomplete trailing statement/block;
- the candidate RDF is parsed with `rdflib.Graph.parse(format="turtle")`;
- when parsing fails and attempts remain, the model is asked to return corrected Turtle using the parser error and previous invalid RDF.

If every attempt fails, the API returns an RDF parse error instead of invalid Turtle.

## Editing guidelines

When editing these prompts:

- keep `${TEXT}` in the entity extraction prompt;
- keep `${PAYLOAD}` in the RDF build prompt;
- keep the required RDF prefixes declared if the prompt or examples use them;
- avoid introducing undeclared prefixes such as `ex:`;
- keep the entity extraction output as strict JSON;
- keep RDF output instructions concise and strict;
- keep labels mandatory for all subject/object resources;
- keep entity-to-entity relationships preferred for facts that downstream SPARQL should be able to test;
- keep `kg:is` as the default classification/type predicate;
- update both prompt-based and hybrid prompt docs together when changing the shared predicate vocabulary;
- do not ask for markdown fences, explanations, or examples in the final output.
