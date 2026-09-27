from __future__ import annotations

import json

from nebula3_mcp.models import ResultLimits
from nebula3_mcp.result_parser import parse_result
from tests.fakes import MISSING_SPACE, FakeResult, edge, path, vertex

LIMITS = ResultLimits(rows=100, nodes=100, edges=100, bytes=1_000_000)
NODE_A = vertex("player100", "player", name="Tim Duncan", age=42)
NODE_B = vertex("player101", "player", name="Tony Parker", age=36)
FOLLOW = edge("player100", "player101", "follow", degree=95)


def test_vertices_edges_and_paths_are_collected_once_with_stable_keys() -> None:
    result = FakeResult(
        [
            {"v": NODE_A, "e": FOLLOW, "n": NODE_B},
            {"p": path([NODE_A, NODE_B], [FOLLOW])},
        ],
        columns=["v", "e", "n", "p"],
        space="basketballplayer",
    )

    parsed = parse_result(result, space="basketballplayer", limits=LIMITS)

    assert parsed.status.ok is True
    assert parsed.status.space == "basketballplayer"
    assert [node.key for node in parsed.graph.nodes] == [
        "basketballplayer:player100", "basketballplayer:player101",
    ]
    assert parsed.graph.nodes[0].properties == {"player.name": "Tim Duncan", "player.age": 42}
    assert [item.key for item in parsed.graph.edges] == [
        "basketballplayer:player100:follow:0:player101",
    ]
    assert parsed.graph.paths[0].hop_count == 1
    assert parsed.graph.paths[0].sdk_length == 1
    assert parsed.table.columns == ["v", "e", "n", "p"]


def test_edge_endpoints_become_placeholders_until_a_full_vertex_arrives() -> None:
    result = FakeResult([{"e": FOLLOW}, {"v": NODE_A}])

    parsed = parse_result(result, space="s", limits=LIMITS)

    nodes = {node.vid: node for node in parsed.graph.nodes}
    assert nodes["player100"].placeholder is False
    assert nodes["player100"].tags == ["player"]
    assert nodes["player101"].placeholder is True


def test_subgraph_lists_and_maps_are_walked_but_user_maps_are_not_entities() -> None:
    result = FakeResult([
        {"nodes": [NODE_A, NODE_B], "relationships": [FOLLOW]},
        {"m": {"vid": "not-a-vertex", "src": "x", "dst": "y"}, "nested": {"k": [NODE_A]}},
    ])

    parsed = parse_result(result, space="s", limits=LIMITS)

    assert len(parsed.graph.nodes) == 2
    assert len(parsed.graph.edges) == 1


def test_integer_vids_and_ranks_are_distinct_edges() -> None:
    result = FakeResult([
        {"e": edge(1, 2, "serve", rank=2014)},
        {"e": edge(1, 2, "serve", rank=2015)},
    ])

    parsed = parse_result(result, space="s", limits=LIMITS)

    assert [item.key for item in parsed.graph.edges] == ["s:1:serve:2014:2", "s:1:serve:2015:2"]


def test_row_node_edge_and_byte_limits_are_reported() -> None:
    rows = [{"v": vertex(f"p{index}", "player", name="x" * 50)} for index in range(5)]

    by_rows = parse_result(FakeResult(rows), space="s", limits=LIMITS.model_copy(update={"rows": 2}))
    by_nodes = parse_result(FakeResult(rows), space="s", limits=LIMITS.model_copy(update={"nodes": 3}))
    by_bytes = parse_result(FakeResult(rows), space="s", limits=LIMITS.model_copy(update={"bytes": 200}))
    by_edges = parse_result(
        FakeResult([{"e": edge("a", "b", "t", rank=i)} for i in range(3)]),
        space="s", limits=LIMITS.model_copy(update={"edges": 2}),
    )

    assert by_rows.truncation.reasons == ("max_rows",)
    assert by_rows.table.returned_row_count == 2
    assert by_rows.table.result_row_count == 5
    assert "max_nodes" in by_nodes.truncation.reasons
    assert len(by_nodes.graph.nodes) == 3
    assert by_bytes.truncation.reasons == ("max_bytes",)
    assert by_edges.truncation.reasons == ("max_edges",)


def test_error_status_is_reported_without_rows() -> None:
    parsed = parse_result(FakeResult(error=MISSING_SPACE), space=None, limits=LIMITS)

    assert parsed.status.ok is False
    assert parsed.status.code == "-1009"
    assert parsed.status.message == "SemanticError: Space was not chosen."
    assert parsed.table.rows == []


def test_rows_are_json_serializable() -> None:
    parsed = parse_result(FakeResult([{"v": NODE_A, "d": {"$type": "date", "value": "2020-01-01"}}]),
                          space="s", limits=LIMITS)

    json.dumps(parsed.model_dump(mode="json"), allow_nan=False)
