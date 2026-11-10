import json

import pytest
from rdflib import Graph, Literal

from hybrid_pipelines.domain.structured_rdf import (
    RDF_PREFIXES,
    model_response_to_graph,
    model_response_to_turtle,
    serialize_graph,
)


def test_rdf_envelope_roundtrip_preserves_literals_and_required_prefixes():
    turtle = RDF_PREFIXES + r'wd:Q312 rdfs:label "Apple \"Inc.\""@en ; kg:is kg:company .'
    response = json.dumps({"rdf": turtle})
    graph = model_response_to_graph(response)
    serialized = serialize_graph(graph)
    assert serialized.startswith(RDF_PREFIXES)
    assert Literal('Apple "Inc."', lang="en") in graph.objects()
    assert set(Graph().parse(data=model_response_to_turtle(response), format="turtle")) == set(graph)


@pytest.mark.parametrize(
    "response",
    [
        "",
        "not json",
        "{}",
        "[]",
        '{"rdf": null}',
        '{"rdf": 3}',
        '{"rdf": ""}',
        '{"rdf": "   "}',
        '{"triples": []}',
        '{"rdf": "", "extra": 1}',
    ],
)
def test_rejects_invalid_or_empty_rdf_envelopes(response):
    with pytest.raises(ValueError):
        model_response_to_graph(response)


def test_rejects_bare_turtle():
    with pytest.raises(ValueError, match="Invalid RDF JSON"):
        model_response_to_graph(RDF_PREFIXES + 'wd:Q312 rdfs:label "Apple"@en .')


def test_rejects_prefixes_without_triples():
    with pytest.raises(ValueError, match="no triples"):
        model_response_to_graph(json.dumps({"rdf": RDF_PREFIXES}))


@pytest.mark.parametrize("prefix", ["rdfs", "wd", "kg"])
def test_rejects_missing_or_wrong_required_prefix(prefix):
    turtle = RDF_PREFIXES + '<urn:subject> <urn:predicate> "value" .'
    missing = "\n".join(line for line in turtle.splitlines() if not line.startswith(f"@prefix {prefix}:"))
    with pytest.raises(ValueError, match=f"@prefix {prefix}:"):
        model_response_to_graph(json.dumps({"rdf": missing}))
    wrong = turtle.replace(f"@prefix {prefix}: <", f"@prefix {prefix}: <https://wrong.example/")
    with pytest.raises(ValueError, match=f"@prefix {prefix}:"):
        model_response_to_graph(json.dumps({"rdf": wrong}))


def test_serialization_keeps_unused_required_prefixes():
    graph = model_response_to_graph(json.dumps({"rdf": RDF_PREFIXES + 'kg:thing rdfs:label "Thing"@en .'}))
    assert serialize_graph(graph).startswith(RDF_PREFIXES)
