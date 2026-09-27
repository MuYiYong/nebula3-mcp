"""Bounded extraction of a NebulaGraph 3.x execution plan (PROFILE / EXPLAIN)."""

from __future__ import annotations

import json
from typing import Any

from nebula3_mcp.models import ProfileOutput

_MAX_OPERATORS = 1_000
_MAX_STAT_CHARS = 2_000


def extract_profile(result: Any, *, max_bytes: int) -> ProfileOutput:
    """Flatten ``PlanDescription`` into depth-annotated operator rows, root first."""

    plan = getattr(result, "plan_desc", None)
    output = ProfileOutput(latency_us=getattr(result, "latency_us", None))
    descriptions = list(getattr(plan, "plan_node_descs", None) or [])
    if plan is None or not descriptions:
        return output
    output = output.model_copy(update={
        "format": _text(getattr(plan, "format", None)) or None,
        "optimize_time_us": getattr(plan, "optimize_time_in_us", None),
    })

    by_id = {node.id: node for node in descriptions}
    children: dict[int, list[int]] = {node.id: [] for node in descriptions}
    referenced: set[int] = set()
    for node in descriptions:
        for dependency in node.dependencies or []:
            if dependency in by_id:
                children[node.id].append(dependency)
                referenced.add(dependency)
        branch = getattr(node, "branch_info", None)
        condition = getattr(branch, "condition_node_id", None)
        if isinstance(condition, int) and condition in by_id and condition != node.id:
            # Loop/Select bodies hang off their condition node rather than dependencies.
            children[condition].append(node.id)
            referenced.add(node.id)

    order: list[tuple[Any, int]] = []
    visited: set[int] = set()
    roots = [node.id for node in descriptions if node.id not in referenced]
    for root in [*roots, *(node.id for node in descriptions)]:
        stack = [(root, 0)]
        while stack:
            node_id, depth = stack.pop()
            if node_id in visited:
                continue
            visited.add(node_id)
            order.append((by_id[node_id], depth))
            stack.extend((child, depth + 1) for child in reversed(children[node_id]))

    used = 0
    operators: list[dict[str, Any]] = []
    for node, depth in order:
        row = _operator(node, depth)
        used += len(json.dumps(row, ensure_ascii=False, default=str).encode("utf-8"))
        if used > max_bytes or len(operators) >= _MAX_OPERATORS:
            return output.model_copy(update={"operators": operators, "truncated": True})
        operators.append(row)
    return output.model_copy(update={"operators": operators})


def _operator(node: Any, depth: int) -> dict[str, Any]:
    profiles = list(getattr(node, "profiles", None) or [])
    details = "; ".join(
        f"{_text(pair.key)}: {_text(pair.value)}" for pair in (node.description or [])
    )
    output_var = _text(getattr(node, "output_var", None))
    columns: list[str] | None = None
    try:
        parsed = json.loads(output_var) if output_var else None
        if isinstance(parsed, dict) and isinstance(parsed.get("colNames"), list):
            columns = [str(item) for item in parsed["colNames"]]
    except ValueError:
        columns = None
    other_stats: dict[str, str] = {}
    for stats in profiles:
        for key, value in (getattr(stats, "other_stats", None) or {}).items():
            other_stats[_text(key)] = _text(value)[:_MAX_STAT_CHARS]
    branch = getattr(node, "branch_info", None)
    row: dict[str, Any] = {
        "id": node.id,
        "name": _text(node.name),
        "depth": depth,
        "dependencies": list(node.dependencies or []),
        "columns": columns,
        "details": details[:_MAX_STAT_CHARS],
        "executions": len(profiles),
        "rows": sum(int(stats.rows) for stats in profiles) if profiles else None,
        "exec_time_ms": _ms(sum(int(stats.exec_duration_in_us) for stats in profiles))
        if profiles else None,
        "total_time_ms": _ms(sum(int(stats.total_duration_in_us) for stats in profiles))
        if profiles else None,
        "other_stats": other_stats or None,
        "branch": (
            {"is_do_branch": bool(branch.is_do_branch), "condition_node_id": branch.condition_node_id}
            if branch is not None else None
        ),
    }
    return row


def _ms(microseconds: int) -> float:
    return round(microseconds / 1_000, 3)


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)
