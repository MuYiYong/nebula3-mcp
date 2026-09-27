"""PROFILE extraction from real nebula3 Thrift PlanDescription structures."""

from __future__ import annotations

import json

from nebula3.graph.ttypes import (
    Pair,
    PlanDescription,
    PlanNodeBranchInfo,
    PlanNodeDescription,
    ProfilingStats,
)

from nebula3_mcp.profiling import extract_profile
from tests.fakes import FakeResult


def node(
    node_id: int,
    name: str,
    dependencies: list[int] | None,
    *,
    rows: int | None = None,
    branch: PlanNodeBranchInfo | None = None,
    runs: int = 1,
) -> PlanNodeDescription:
    profiles = (
        [ProfilingStats(rows=rows, exec_duration_in_us=1_500, total_duration_in_us=2_000,
                        other_stats={b"resp[0]": b'{"exec": "871(us)"}'})] * runs
        if rows is not None else None
    )
    return PlanNodeDescription(
        name=name.encode(),
        id=node_id,
        output_var=json.dumps({"colNames": ["v"], "name": f"__{name}_{node_id}"}).encode(),
        description=[Pair(key=b"inputVar", value=b"__Start_1")],
        profiles=profiles,
        branch_info=branch,
        dependencies=dependencies,
    )


def plan(*nodes: PlanNodeDescription) -> PlanDescription:
    return PlanDescription(
        plan_node_descs=list(nodes),
        node_index_map={item.id: index for index, item in enumerate(nodes)},
        format=b"row",
        optimize_time_in_us=321,
    )


def test_profile_is_flattened_root_first_with_depth_and_metrics() -> None:
    result = FakeResult(plan_desc=plan(
        node(3, "Project", [2], rows=2),
        node(2, "Limit", [1], rows=2),
        node(1, "Start", None, rows=0),
    ), latency_us=900)

    output = extract_profile(result, max_bytes=100_000)

    assert output.latency_us == 900
    assert output.format == "row"
    assert output.optimize_time_us == 321
    assert [(item["name"], item["depth"]) for item in output.operators] == [
        ("Project", 0), ("Limit", 1), ("Start", 2),
    ]
    first = output.operators[0]
    assert first["rows"] == 2
    assert first["exec_time_ms"] == 1.5
    assert first["total_time_ms"] == 2.0
    assert first["columns"] == ["v"]
    assert first["details"] == "inputVar: __Start_1"
    assert first["other_stats"] == {"resp[0]": '{"exec": "871(us)"}'}


def test_loop_bodies_are_nested_under_their_condition_node() -> None:
    body = node(5, "GetNeighbors", [4], rows=3, runs=3,
                branch=PlanNodeBranchInfo(is_do_branch=True, condition_node_id=6))
    output = extract_profile(FakeResult(plan_desc=plan(
        node(7, "Project", [6], rows=1),
        node(6, "Loop", [1], rows=1),
        body,
        node(4, "Start", None, rows=0),
        node(1, "Start", None, rows=0),
    )), max_bytes=100_000)

    names = [(item["name"], item["depth"]) for item in output.operators]
    assert names[0] == ("Project", 0)
    assert ("GetNeighbors", 2) in names
    looped = next(item for item in output.operators if item["name"] == "GetNeighbors")
    assert looped["executions"] == 3
    assert looped["rows"] == 9
    assert looped["branch"] == {"is_do_branch": True, "condition_node_id": 6}
    assert len(output.operators) == 5


def test_explain_nodes_without_profiles_and_missing_plan() -> None:
    explained = extract_profile(FakeResult(plan_desc=plan(node(1, "Start", None))), max_bytes=10_000)

    assert explained.operators[0]["rows"] is None
    assert explained.operators[0]["executions"] == 0
    assert extract_profile(FakeResult(), max_bytes=10_000).operators == []


def test_profile_is_bounded_by_bytes() -> None:
    nodes = [node(index, "Project", [index - 1] if index else None, rows=1) for index in range(50)]

    output = extract_profile(FakeResult(plan_desc=plan(*reversed(nodes))), max_bytes=2_000)

    assert output.truncated is True
    assert 0 < len(output.operators) < 50
