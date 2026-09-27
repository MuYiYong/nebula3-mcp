"""Conversion of real nebula3-python Thrift values, independent of a database."""

from __future__ import annotations

import json

from nebula3.common.ttypes import (
    Coordinate,
    Date,
    DateTime,
    Duration,
    Edge,
    Geography,
    LineString,
    NList,
    NMap,
    NSet,
    NullType,
    Path,
    Point,
    Polygon,
    Step,
    Tag,
    Time,
    Value,
    Vertex,
)
from nebula3.data.DataObject import ValueWrapper

from nebula3_mcp.values import convert_value


def s(text: str) -> Value:
    return Value(sVal=text.encode())


def wrap(value: Value) -> ValueWrapper:
    return ValueWrapper(value)


def player(vid: str, name: str, age: int) -> Vertex:
    return Vertex(
        vid=s(vid),
        tags=[Tag(name=b"player", props={b"name": s(name), b"age": Value(iVal=age)})],
    )


def test_scalars_and_containers_keep_json_types() -> None:
    assert convert_value(wrap(Value(nVal=NullType.__NULL__))) is None
    assert convert_value(wrap(Value(bVal=True))) is True
    assert convert_value(wrap(Value(iVal=9_223_372_036_854_775_807))) == 9_223_372_036_854_775_807
    assert convert_value(wrap(Value(fVal=1.5))) == 1.5
    assert convert_value(wrap(Value(fVal=float("nan")))) == {"$type": "float", "value": "NaN"}
    assert convert_value(wrap(s("中文"))) == "中文"
    assert convert_value(wrap(Value(lVal=NList(values=[Value(iVal=1), s("x")])))) == [1, "x"]
    assert convert_value(wrap(Value(mVal=NMap(kvs={b"k": Value(iVal=2)})))) == {"k": 2}
    assert convert_value(wrap(Value(uVal=NSet(values={Value(iVal=2), Value(iVal=1)})))) == {
        "$type": "set", "values": [1, 2],
    }


def test_temporal_duration_and_geography_are_typed() -> None:
    assert convert_value(wrap(Value(dVal=Date(2020, 1, 2)))) == {"$type": "date", "value": "2020-01-02"}
    assert convert_value(wrap(Value(tVal=Time(10, 0, 1, 5)))) == {
        "$type": "time", "value": "10:00:01.000005",
    }
    assert convert_value(wrap(Value(dtVal=DateTime(2020, 1, 2, 3, 4, 5, 6)))) == {
        "$type": "datetime", "value": "2020-01-02T03:04:05.000006",
    }
    assert convert_value(wrap(Value(duVal=Duration(seconds=86_400, microseconds=0, months=1)))) == {
        "$type": "duration",
        "value": "P1MT86400.000000000S",
        "components": {"months": 1, "seconds": 86_400, "microseconds": 0},
    }
    point = Geography(ptVal=Point(coord=Coordinate(1.0, 2.5)))
    line = Geography(lsVal=LineString(coordList=[Coordinate(0.0, 1.0), Coordinate(2.0, 3.0)]))
    polygon = Geography(pgVal=Polygon(coordListList=[[
        Coordinate(0.0, 0.0), Coordinate(1.0, 0.0), Coordinate(1.0, 1.0), Coordinate(0.0, 0.0),
    ]]))
    assert convert_value(wrap(Value(ggVal=point)))["value"] == "POINT(1 2.5)"
    assert convert_value(wrap(Value(ggVal=line)))["value"] == "LINESTRING(0 1, 2 3)"
    assert convert_value(wrap(Value(ggVal=polygon)))["value"] == "POLYGON((0 0, 1 0, 1 1, 0 0))"


def test_vertex_flattens_tag_properties() -> None:
    multi = Vertex(
        vid=Value(iVal=7),
        tags=[
            Tag(name=b"player", props={b"name": s("Tim")}),
            Tag(name=b"coach", props={}),
        ],
    )

    assert convert_value(wrap(Value(vVal=multi))) == {
        "$type": "vertex",
        "vid": 7,
        "tags": ["player", "coach"],
        "properties": {"player.name": "Tim"},
    }


def test_reverse_edge_is_normalized_to_stored_direction() -> None:
    forward = Edge(src=s("a"), dst=s("b"), type=5, name=b"follow", ranking=2,
                   props={b"degree": Value(iVal=90)})
    reverse = Edge(src=s("b"), dst=s("a"), type=-5, name=b"follow", ranking=2,
                   props={b"degree": Value(iVal=90)})

    expected = {
        "$type": "edge", "src": "a", "dst": "b", "edge_type": "follow", "rank": 2,
        "properties": {"degree": 90},
    }
    assert convert_value(wrap(Value(eVal=forward))) == expected
    assert convert_value(wrap(Value(eVal=reverse))) == expected


def test_path_keeps_vertices_edges_and_direction() -> None:
    value = Path(
        src=player("p1", "Tim", 42),
        steps=[
            Step(dst=player("p2", "Tony", 36), type=3, name=b"follow", ranking=0, props={}),
            Step(dst=player("p3", "Manu", 41), type=-3, name=b"follow", ranking=0, props={}),
        ],
    )

    converted = convert_value(wrap(Value(pVal=value)))

    assert converted["$type"] == "path"
    assert converted["length"] == 2
    assert [node["vid"] for node in converted["nodes"]] == ["p1", "p2", "p3"]
    assert [(e["src"], e["dst"]) for e in converted["edges"]] == [("p1", "p2"), ("p3", "p2")]
    assert converted["nodes"][0]["properties"] == {"player.age": 42, "player.name": "Tim"}
    json.dumps(converted, allow_nan=False)
