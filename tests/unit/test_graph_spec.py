from __future__ import annotations

from nebula3_mcp.models import ResultLimits
from nebula3_mcp.result_parser import parse_result
from nebula3_mcp.specs import build_cytoscape_graph
from tests.fakes import FakeResult, edge, path, vertex

LIMITS = ResultLimits(rows=100, nodes=100, edges=100, bytes=1_000_000)


def test_cytoscape_elements_expose_vid_tags_and_edge_identity() -> None:
    parsed = parse_result(
        FakeResult([{"p": path(
            [vertex("p1", "player", name="Tim"), vertex("t1", "team", name="Spurs")],
            [edge("p1", "t1", "serve", rank=1997, start_year=1997)],
        )}]),
        space="demo",
        limits=LIMITS,
    )

    spec = build_cytoscape_graph(parsed)

    assert spec.format == "cytoscape-elements-v1"
    assert spec.space == "demo"
    node = spec.elements.nodes[0].data
    assert node == {
        "id": "demo:p1",
        "vid": "p1",
        "space": "demo",
        "type": "player",
        "tags": ["player"],
        "properties": {"player.name": "Tim"},
        "placeholder": False,
    }
    link = spec.elements.edges[0].data
    assert link["source"] == "demo:p1"
    assert link["target"] == "demo:t1"
    assert (link["type"], link["src"], link["dst"], link["rank"]) == ("serve", "p1", "t1", 1997)
    assert link["properties"] == {"start_year": 1997}
    assert spec.paths[0]["hop_count"] == 1


def test_large_integer_vids_and_ranks_survive_javascript() -> None:
    big = 2**63 - 1
    parsed = parse_result(
        FakeResult([{"e": edge(big, 1, "follow", rank=big)}]), space="s", limits=LIMITS
    )

    spec = build_cytoscape_graph(parsed)

    vids = {item.data["vid"] for item in spec.elements.nodes}
    assert str(big) in vids
    assert spec.elements.edges[0].data["rank"] == str(big)
    assert spec.elements.edges[0].data["source"] == f"s:{big}"
    assert spec.elements.nodes[0].data["placeholder"] is True


def test_empty_result_builds_empty_graph() -> None:
    spec = build_cytoscape_graph(parse_result(FakeResult([{"n": 1}]), space=None, limits=LIMITS))

    assert spec.elements.nodes == []
    assert spec.elements.edges == []
    assert spec.space is None
