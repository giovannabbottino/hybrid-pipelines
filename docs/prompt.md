# Prompts

[README](../README.md) · [API](analyze.md) · [Testing](how-to-test.md)

## Templates

The pipeline uses three prompts:

- [System](../prompt/system/agent.txt): task order, evidence handling, and output rules.
- [NED](../prompt/prompts/ned.txt): identifies mentions, directs MCP consultations, and selects identities. Receives the input text through `${TEXT}`.
- [RDF](../prompt/prompts/rdf-build.txt): receives the text and validated entities through `${PAYLOAD}`, preserving the earlier evidence in the conversation.

## Output contract

NED returns `{"entities":[{"surface":"Adams","start":0,"end":5,"id":"Q42","label":"Douglas Adams"}]}`. The example assumes the input text is `Adams` and Q42 occurred in a successful tool result. Unresolved mentions use a null ID.

RDF returns exactly `{"rdf":"<Turtle>"}`. Its Turtle string must declare:

```turtle
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix wd: <http://www.wikidata.org/entity/> .
@prefix kg: <https://example.org/wikidata-description/> .
```

## Validation and retries

Instructions require readable labels and supported relationships, with `kg:is`, `kg:part_of`, `kg:has_part`, or other concise predicates. The code validates syntax, prefixes, non-empty content, and preservation of resolved Wikidata IDs. It does not certify factual correctness or enforce every semantic instruction in the prompt.

Prompt paths are configurable in [.env.example](../.env.example). The former extraction and candidate-selection prompts have been replaced by the NED prompt.
