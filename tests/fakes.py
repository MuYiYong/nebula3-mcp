"""Deterministic NebulaGraph 3.x result fakes shared by unit and protocol tests."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from nebula3_mcp.serialization import JsonValue

MISSING_SPACE = (-1009, "SemanticError: Space was not chosen.")
SPACE_NOT_FOUND = (-1005, "SpaceNotFound: SpaceName `old`")


class FakeResult:
    """Implements ``nebula3_mcp.database.ResultLike`` with already-converted rows."""

    def __init__(
        self,
        rows: list[dict[str, Any]] | None = None,
        *,
        error: tuple[int, str] | None = None,
        space: str = "",
        columns: list[str] | None = None,
        plan_desc: Any = None,
        latency_us: int = 11,
        comment: str = "",
    ) -> None:
        self.rows = list(rows or [])
        self.error_code, self.error_message = error if error is not None else (0, "")
        self.space_name = space
        self.comment = comment
        self.column_names = columns if columns is not None else (list(self.rows[0]) if self.rows else [])
        self.row_count = len(self.rows)
        self.plan_desc = plan_desc
        self.latency_us = latency_us

    def iter_rows(self) -> Iterator[dict[str, JsonValue]]:
        for row in self.rows:
            yield dict(row)


class FakeGateway:
    """Replays queued results; ``USE`` succeeds (reporting the space) unless queued otherwise."""

    def __init__(self, results: list[FakeResult] | None = None, *, auto_use: bool = True) -> None:
        self.results = list(results or [])
        self.statements: list[str] = []
        self.auto_use = auto_use

    async def execute(self, statement: str) -> FakeResult:
        self.statements.append(statement)
        if self.auto_use and statement.startswith("USE `") and (
            not self.results or self.results[0].error_code == 0
        ):
            return FakeResult(space=statement[5:-1])
        return self.results.pop(0) if self.results else FakeResult()

    async def version(self) -> str:
        return "3.8.0"


def vertex(vid: JsonValue, tag: str | None = None, **properties: JsonValue) -> dict[str, JsonValue]:
    return {
        "$type": "vertex",
        "vid": vid,
        "tags": [tag] if tag else [],
        "properties": {f"{tag}.{key}": value for key, value in properties.items()} if tag else {},
    }


def edge(
    src: JsonValue, dst: JsonValue, edge_type: str, rank: int = 0, **properties: JsonValue
) -> dict[str, JsonValue]:
    return {
        "$type": "edge",
        "src": src,
        "dst": dst,
        "edge_type": edge_type,
        "rank": rank,
        "properties": dict(properties),
    }


def path(nodes: list[dict[str, JsonValue]], edges: list[dict[str, JsonValue]]) -> dict[str, JsonValue]:
    return {"$type": "path", "nodes": list(nodes), "edges": list(edges), "length": len(edges)}
