from __future__ import annotations

import json
import re
import unicodedata
from typing import Any
from urllib.parse import urlsplit

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import OWL, RDF, RDFS, XSD

WD = Namespace("http://www.wikidata.org/entity/")
KG = Namespace("https://example.org/wikidata-description/")

RDF_TRIPLES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "triples": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string", "minLength": 1},
                    "predicate": {"type": "string", "minLength": 1},
                    "object": {"type": "string", "minLength": 1},
                    "object_type": {"type": "string", "enum": ["resource", "literal"]},
                    "language": {"type": "string", "minLength": 1},
                    "datatype": {"type": "string", "minLength": 1},
                },
                "required": ["subject", "predicate", "object", "object_type"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["triples"],
    "additionalProperties": False,
}

_PREFIXES = {
    "wd": str(WD),
    "kg": str(KG),
    "rdf": str(RDF),
    "rdfs": str(RDFS),
    "xsd": str(XSD),
    "owl": str(OWL),
}
_LANGUAGE_TAG = re.compile(r"^[A-Za-z]{1,8}(?:-[A-Za-z0-9]{1,8})*$")
_SAFE_STANDARD_LOCAL = re.compile(r"^[A-Za-z_][A-Za-z0-9._-]*$")
_WIKIDATA_ID = re.compile(r"^Q[1-9][0-9]*$")
_TRIPLE_KEYS = {"subject", "predicate", "object", "object_type", "language", "datatype"}


def model_response_to_turtle(response_text: str) -> str:
    """Build and serialize RDF from structured triples.

    Valid legacy Turtle is accepted so custom clients can migrate without an API break. The
    bundled Ollama clients always request ``RDF_TRIPLES_SCHEMA`` and therefore use the structured
    path in production.
    """

    text = response_text.strip()
    if not text:
        raise ValueError("Empty structured RDF response.")
    if not _looks_like_json(text):
        graph = Graph().parse(data=_strip_fence(text), format="turtle")
        if len(graph) == 0:
            raise ValueError("RDF response contains no triples.")
        return _serialize(graph)

    payload = _parse_json_object(text)
    if set(payload) != {"triples"}:
        raise ValueError("Structured RDF response must contain only the 'triples' field.")
    triples = payload["triples"]
    if not isinstance(triples, list) or not triples:
        raise ValueError("Structured RDF response must contain at least one triple.")

    graph = _new_graph()
    for index, item in enumerate(triples):
        if not isinstance(item, dict):
            raise ValueError(f"Triple {index} must be an object.")
        unknown = set(item) - _TRIPLE_KEYS
        if unknown:
            raise ValueError(f"Triple {index} has unsupported fields: {sorted(unknown)}.")
        missing = {"subject", "predicate", "object", "object_type"} - set(item)
        if missing:
            raise ValueError(f"Triple {index} is missing fields: {sorted(missing)}.")

        subject = _resource(item["subject"], role=f"triple {index} subject")
        predicate = _resource(item["predicate"], role=f"triple {index} predicate")
        object_type = item["object_type"]
        if object_type == "resource":
            if item.get("language") or item.get("datatype"):
                raise ValueError(f"Triple {index} resource object cannot have language or datatype.")
            object_node = _resource(item["object"], role=f"triple {index} object")
        elif object_type == "literal":
            object_node = _literal(item, index)
        else:
            raise ValueError(f"Triple {index} object_type must be 'resource' or 'literal'.")
        graph.add((subject, predicate, object_node))

    return _serialize(graph)


def _literal(item: dict[str, Any], index: int) -> Literal:
    value = item["object"]
    if not isinstance(value, str):
        raise ValueError(f"Triple {index} literal object must be a string.")
    language = item.get("language")
    datatype = item.get("datatype")
    if language and datatype:
        raise ValueError(f"Triple {index} literal cannot have both language and datatype.")
    if language:
        if not isinstance(language, str) or not _LANGUAGE_TAG.fullmatch(language):
            raise ValueError(f"Triple {index} has an invalid language tag.")
        return Literal(value, lang=language)
    if datatype:
        return Literal(value, datatype=_resource(datatype, role=f"triple {index} datatype"))
    return Literal(value)


def _resource(value: Any, *, role: str) -> URIRef:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"The {role} must be a non-empty string.")
    identifier = value.strip()
    if identifier == "a":
        return RDF.type
    if _WIKIDATA_ID.fullmatch(identifier):
        return WD[identifier]
    if identifier.startswith(("http://", "https://", "urn:")):
        if not _valid_absolute_iri(identifier):
            raise ValueError(f"The {role} contains an invalid absolute IRI.")
        return URIRef(identifier)
    if ":" not in identifier:
        return KG[_safe_local_name(identifier, role=role)]

    prefix, local = identifier.split(":", 1)
    namespace = _PREFIXES.get(prefix.lower())
    if namespace is None:
        raise ValueError(f"The {role} uses unsupported prefix {prefix!r}.")
    if prefix.lower() == "wd":
        if not _WIKIDATA_ID.fullmatch(local):
            raise ValueError(f"The {role} contains an invalid Wikidata ID.")
        return URIRef(namespace + local)
    if prefix.lower() == "kg":
        local = _safe_local_name(local, role=role)
    elif not _SAFE_STANDARD_LOCAL.fullmatch(local):
        raise ValueError(f"The {role} contains an invalid prefixed name.")
    return URIRef(namespace + local)


def _safe_local_name(value: str, *, role: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    local = re.sub(r"[^A-Za-z0-9._-]+", "_", ascii_value).strip("._-").lower()
    if not local:
        raise ValueError(f"The {role} cannot be converted to a safe kg: name.")
    if local[0].isdigit():
        local = f"n_{local}"
    return local


def _valid_absolute_iri(value: str) -> bool:
    if any(character.isspace() or ord(character) < 32 for character in value):
        return False
    parsed = urlsplit(value)
    if parsed.scheme in {"http", "https"}:
        return bool(parsed.netloc)
    return parsed.scheme == "urn" and bool(parsed.path)


def _looks_like_json(text: str) -> bool:
    stripped = _strip_fence(text).lstrip()
    return stripped.startswith("{")


def _parse_json_object(text: str) -> dict[str, Any]:
    cleaned = _strip_fence(text)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid structured RDF JSON: {exc.msg}.") from exc
    if not isinstance(payload, dict):
        raise ValueError("Structured RDF response must be a JSON object.")
    return payload


def _strip_fence(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    return cleaned


def _new_graph() -> Graph:
    graph = Graph()
    graph.bind("wd", WD)
    graph.bind("kg", KG)
    graph.bind("rdf", RDF)
    graph.bind("rdfs", RDFS)
    graph.bind("xsd", XSD)
    graph.bind("owl", OWL)
    return graph


def _serialize(graph: Graph) -> str:
    serialized = graph.serialize(format="turtle")
    return serialized.decode("utf-8") if isinstance(serialized, bytes) else serialized
