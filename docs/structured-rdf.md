# Structured RDF contract

All three pipelines use the same triple fields and RDFLib conversion rules.
Their grounding steps differ; the public `rdf` response field remains a Turtle string.

The bundled Ollama client sends `RDF_TRIPLES_SCHEMA` as its `format` parameter during
RDF generation. Ontology tool conversations run without that format until tools are disabled.

```json
{
  "triples": [
    {"subject": "kg:alice", "predicate": "kg:knows", "object": "kg:bob", "object_type": "resource"},
    {"subject": "kg:alice", "predicate": "rdfs:label", "object": "Alice", "object_type": "literal", "language": "en"}
  ]
}
```

- The top-level object contains only a non-empty `triples` array.
- Each triple requires string `subject`, `predicate`, `object`, and `object_type` fields.
- `object_type` is `resource` or `literal`. Optional `language` and `datatype` describe
  literals and cannot both be set; resource objects cannot carry literal metadata.
- Supported prefixes are `wd`, `kg`, `rdf`, `rdfs`, `xsd`, and `owl`. Bare QIDs resolve
  to Wikidata; `a` resolves to `rdf:type`. HTTP(S) and URN identifiers are also accepted.
- Bare local names and `kg:` names are normalized to lowercase ASCII with unsafe
  characters replaced by underscores. A leading digit receives an `n_` prefix.

The application constructs RDFLib terms, serializes the graph, and validates the result.
Ontology additionally rejects graphs containing only labels. Hybrid applies its existing
Wikidata-ID and label postconditions. Syntax validation does not establish factual accuracy.

`max_rdf_attempts` is clamped to 1-3 and defaults to 3. Invalid output is returned to the
same generation stage with validation feedback. Grounding evidence is reused. Exhausted
RDF attempts return HTTP 422; hybrid candidate disambiguation has its own three-attempt limit.

For custom-client compatibility, the converter also accepts valid legacy Turtle, including
an outer Markdown fence. It parses and reserializes that graph without syntax repair.
The bundled production clients request the structured JSON contract above.

Implementation: `src/*/domain/structured_rdf.py`. See [pipeline diagrams](diagrams.md).
