import json

import pytest
from rdflib import Graph, Literal, URIRef

from hybrid_pipelines.domain.structured_rdf import model_response_to_turtle


def test_structured_response_is_serialized_to_a_turtle_string():
    response = json.dumps(
        {
            "triples": [
                {
                    "subject": "wd:Q312",
                    "predicate": "rdfs:label",
                    "object": "Apple Inc.",
                    "object_type": "literal",
                    "language": "en",
                },
                {
                    "subject": "wd:Q312",
                    "predicate": "kg:headquartered in",
                    "object": "wd:Q189471",
                    "object_type": "resource",
                },
            ]
        }
    )

    turtle = model_response_to_turtle(response)
    graph = Graph().parse(data=turtle, format="turtle")

    assert isinstance(turtle, str)
    assert (
        URIRef("http://www.wikidata.org/entity/Q312"),
        URIRef("https://example.org/wikidata-description/headquartered_in"),
        URIRef("http://www.wikidata.org/entity/Q189471"),
    ) in graph
    assert Literal("Apple Inc.", lang="en") in graph.objects()


def test_structured_response_rejects_missing_required_fields():
    with pytest.raises(ValueError, match="missing fields"):
        model_response_to_turtle(
            json.dumps(
                {
                    "triples": [
                        {
                            "subject": "kg:apple",
                            "predicate": "rdfs:label",
                            "object_type": "literal",
                        }
                    ]
                }
            )
        )
