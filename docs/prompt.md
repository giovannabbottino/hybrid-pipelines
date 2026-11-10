# Prompts

The system uses `prompt/system/agent.txt` and three task prompts: `entity-extraction.txt`, `candidate-disambiguation.txt`, and `rdf-build.txt`. Configurable prompt names are listed in [.env.example](../.env.example).

Extraction receives `${TEXT}` and returns `{"entities":[...]}`. Disambiguation receives `${PAYLOAD}` and returns `{"selections":[{"mention_index":0,"selected_id":"Q312"}]}`. Only QIDs from the supplied candidates are accepted.

RDF generation receives `${PAYLOAD}` containing the text, selected entities, and relationships. Its response must be JSON containing only the `rdf` key, whose value is a Turtle string. Use `\n` for line breaks and `\"` for double quotes inside the JSON string. Do not include Markdown or explanations.

The Turtle string must begin with these declarations:

```turtle
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix wd: <http://www.wikidata.org/entity/> .
@prefix kg: <https://example.org/wikidata-description/> .
```

Preserve supplied QIDs, use readable labels, and materialize the supplied relationships. Use `kg:is` for classification, `kg:part_of` and `kg:has_part` for composition, and `kg:snake_case` predicates for other relationships. The parser validates the JSON, prefixes, and Turtle syntax; empty or invalid responses are retried up to three total attempts.
