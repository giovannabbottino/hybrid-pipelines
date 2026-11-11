from __future__ import annotations

import json
from typing import Any

from rdflib import Graph, Namespace
from rdflib.namespace import RDFS

WD = Namespace("http://www.wikidata.org/entity/")
KG = Namespace("https://example.org/wikidata-description/")
REQUIRED_PREFIXES = {"rdfs": str(RDFS), "wd": str(WD), "kg": str(KG)}
RDF_PREFIXES = "".join(f"@prefix {prefix}: <{iri}> .\n" for prefix, iri in REQUIRED_PREFIXES.items())
RDF_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"rdf": {"type": "string", "minLength": 1}},
    "required": ["rdf"],
    "additionalProperties": False,
}


def model_response_to_graph(response_text: str) -> Graph:
    """Validate the JSON envelope, Turtle syntax, required prefixes and graph content."""
    try:
        payload = json.loads(response_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid RDF JSON: {exc.msg}.") from exc
    if not isinstance(payload, dict) or set(payload) != {"rdf"}:
        raise ValueError("The LLM response must be a JSON object containing only 'rdf'.")
    rdf = payload["rdf"]
    if not isinstance(rdf, str) or not rdf.strip():
        raise ValueError("Field 'rdf' must be a non-empty Turtle string.")
    graph = Graph(bind_namespaces="none").parse(data=rdf, format="turtle")
    namespaces = dict(graph.namespaces())
    for prefix, iri in REQUIRED_PREFIXES.items():
        if str(namespaces.get(prefix, "")) != iri:
            raise ValueError(f"RDF must declare @prefix {prefix}: <{iri}> .")
    validate_graph(graph)
    return graph


def validate_graph(graph: Graph) -> None:
    if len(graph) == 0:
        raise ValueError("RDF response contains no triples.")


def model_response_to_turtle(response_text: str) -> str:
    return serialize_graph(model_response_to_graph(response_text))


def serialize_graph(graph: Graph) -> str:
    """Keep required prefix declarations even when RDFLib omits unused namespaces."""
    for prefix, iri in REQUIRED_PREFIXES.items():
        graph.bind(prefix, Namespace(iri), replace=True)
    serialized = graph.serialize(format="turtle")
    header, _, body = serialized.partition("\n\n")
    extra_prefixes = "\n".join(
        line
        for line in header.splitlines()
        if not any(line.startswith(f"@prefix {prefix}:") for prefix in REQUIRED_PREFIXES)
    )
    return RDF_PREFIXES + extra_prefixes + "\n" + body
