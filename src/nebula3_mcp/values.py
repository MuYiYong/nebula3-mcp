"""Loss-aware conversion of nebula3-python ValueWrapper objects to JSON values.

The SDK's ``cast_primitive`` flattens dates to strings and prints geography as
Thrift reprs. This converter keeps database types explicit so graph entities
can be recognised unambiguously and temporal values are not confused with text.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from nebula3_mcp.serialization import JsonValue, to_json_value

VERTEX = "vertex"
EDGE = "edge"
PATH = "path"


def convert_value(value: Any) -> JsonValue:
    """Convert one ``nebula3.data.DataObject.ValueWrapper`` to a JSON value."""

    type_name = value._get_type_name()
    converter = _CONVERTERS.get(type_name)
    if converter is not None:
        return converter(value)
    # DataSet and future value types: keep the database repr rather than failing the query.
    return {"$type": type_name, "value": str(value)}


def _vertex(node: Any) -> dict[str, JsonValue]:
    tags = list(node.tags())
    properties: dict[str, JsonValue] = {}
    for tag in tags:
        for name, item in sorted(node.properties(tag).items()):
            properties[f"{tag}.{name}"] = convert_value(item)
    return {
        "$type": VERTEX,
        "vid": convert_value(node.get_id()),
        "tags": list(tags),
        "properties": properties,
    }


def _edge(relationship: Any) -> dict[str, JsonValue]:
    # Relationship already normalises reverse-direction edges to their stored src -> dst.
    return {
        "$type": EDGE,
        "src": convert_value(relationship.start_vertex_id()),
        "dst": convert_value(relationship.end_vertex_id()),
        "edge_type": relationship.edge_name(),
        "rank": relationship.ranking(),
        "properties": {
            name: convert_value(item)
            for name, item in sorted(relationship.properties().items())
        },
    }


def _path(path: Any) -> dict[str, JsonValue]:
    return {
        "$type": PATH,
        "nodes": [_vertex(node) for node in path.nodes()],
        "edges": [_edge(edge) for edge in path.relationships()],
        "length": path.length(),
    }


def _coordinate(coordinate: Any) -> str:
    return f"{_number(coordinate.x)} {_number(coordinate.y)}"


def _number(value: float) -> str:
    number = float(value)
    return str(int(number)) if number.is_integer() else repr(number)


def _ring(points: Any) -> str:
    return "(" + ", ".join(_coordinate(point) for point in points or []) + ")"


def _geography(value: Any) -> dict[str, JsonValue]:
    # The SDK geography wrappers expose raw Thrift structs inconsistently; read them directly.
    geography = value.as_geography().get_geography()
    kind = geography.getType()
    if kind == geography.PTVAL:
        text = f"POINT({_coordinate(geography.get_ptVal().coord)})"
    elif kind == geography.LSVAL:
        text = "LINESTRING" + _ring(geography.get_lsVal().coordList)
    elif kind == geography.PGVAL:
        rings = geography.get_pgVal().coordListList or []
        text = "POLYGON(" + ", ".join(_ring(ring) for ring in rings) + ")"
    else:
        text = str(geography)
    return {"$type": "geography", "value": text}


def _duration(value: Any) -> dict[str, JsonValue]:
    duration = value.as_duration()
    return {
        "$type": "duration",
        "value": str(duration),
        "components": {
            "months": int(duration.get_months()),
            "seconds": int(duration.get_seconds()),
            "microseconds": int(duration.get_microseconds()),
        },
    }


def _sorted_set(value: Any) -> JsonValue:
    converted = [convert_value(item) for item in value.as_set()]
    converted.sort(key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True))
    return {"$type": "set", "values": converted}


_CONVERTERS: dict[str, Callable[[Any], JsonValue]] = {
    "empty": lambda _: None,
    "null": lambda _: None,
    "bool": lambda value: bool(value.as_bool()),
    "int": lambda value: int(value.as_int()),
    "double": lambda value: to_json_value(float(value.as_double())),
    "string": lambda value: str(value.as_string()),
    "list": lambda value: [convert_value(item) for item in value.as_list()],
    "set": _sorted_set,
    "map": lambda value: {str(key): convert_value(item) for key, item in value.as_map().items()},
    "date": lambda value: {"$type": "date", "value": repr(value.as_date())},
    "time": lambda value: {"$type": "time", "value": value.as_time().get_local_time_str()},
    "datetime": lambda value: {
        "$type": "datetime", "value": value.as_datetime().get_local_datetime_str()
    },
    "vertex": lambda value: _vertex(value.as_node()),
    "edge": lambda value: _edge(value.as_relationship()),
    "path": lambda value: _path(value.as_path()),
    "geography": _geography,
    "duration": _duration,
}
