"""Single-pass parsing of bounded NebulaGraph 3.8 query results."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from nebula3_mcp.database import SUCCEEDED, ResultLike
from nebula3_mcp.models import (
    GraphEdge,
    GraphNode,
    GraphPath,
    ParsedGraph,
    ParsedResult,
    QueryStatus,
    ResultLimits,
    TableResult,
    TruncationInfo,
)
from nebula3_mcp.serialization import JsonValue
from nebula3_mcp.values import EDGE, PATH, VERTEX


class _GraphCollector:
    def __init__(self, *, space: str | None, limits: ResultLimits) -> None:
        self.space = space
        self.limits = limits
        self.nodes: dict[str, GraphNode] = {}
        self.edges: dict[str, GraphEdge] = {}
        self.paths: list[GraphPath] = []
        self.reasons: list[str] = []

    def add_reason(self, reason: str) -> None:
        if reason not in self.reasons:
            self.reasons.append(reason)

    def node_key(self, vid: JsonValue) -> str:
        return node_key(self.space, vid)

    def add_node(self, value: Mapping[str, JsonValue], *, placeholder: bool = False) -> str | None:
        vid = value.get("vid")
        if vid is None:
            return None
        key = self.node_key(vid)
        properties = _mapping_value(value.get("properties"))
        tags = _string_list(value.get("tags"))
        node = GraphNode(
            key=key,
            space=self.space,
            vid=vid,
            tags=tags,
            properties=properties,
            placeholder=placeholder,
        )
        previous = self.nodes.get(key)
        if previous is not None:
            # Keep the most informative copy: GO/GET SUBGRAPH may return bare vertices first.
            if not placeholder and (
                previous.placeholder
                or len(properties) > len(previous.properties)
                or len(tags) > len(previous.tags)
            ):
                self.nodes[key] = node
            return key
        if len(self.nodes) >= self.limits.nodes:
            self.add_reason("max_nodes")
            return None
        self.nodes[key] = node
        return key

    def ensure_placeholder(self, vid: JsonValue) -> str | None:
        key = self.node_key(vid)
        if key in self.nodes:
            return key
        return self.add_node({"vid": vid}, placeholder=True)

    def add_edge(self, value: Mapping[str, JsonValue]) -> str | None:
        src = value.get("src")
        dst = value.get("dst")
        if src is None or dst is None:
            return None
        src_key = self.ensure_placeholder(src)
        dst_key = self.ensure_placeholder(dst)
        if src_key is None or dst_key is None:
            self.add_reason("max_edges")
            return None
        rank = value.get("rank", 0)
        edge_type_value = value.get("edge_type")
        edge_type = edge_type_value if isinstance(edge_type_value, str) else None
        key = edge_key(self.space, src, edge_type, rank, dst)
        if key in self.edges:
            return key
        if len(self.edges) >= self.limits.edges:
            self.add_reason("max_edges")
            return None
        self.edges[key] = GraphEdge(
            key=key,
            space=self.space,
            src=src,
            dst=dst,
            rank=rank,
            edge_type=edge_type,
            properties=_mapping_value(value.get("properties")),
        )
        return key

    def walk(self, value: JsonValue) -> None:
        if isinstance(value, list):
            for item in value:
                self.walk(item)
            return
        if not isinstance(value, dict):
            return
        kind = value.get("$type")
        if kind == PATH:
            self.add_path(value)
            return
        if kind == EDGE:
            self.add_edge(value)
            return
        if kind == VERTEX:
            self.add_node(value)
            return
        for item in value.values():
            self.walk(item)

    def add_path(self, value: Mapping[str, JsonValue]) -> None:
        node_keys: list[str] = []
        nodes = value.get("nodes")
        if isinstance(nodes, list):
            for node in nodes:
                if isinstance(node, dict) and node.get("$type") == VERTEX:
                    key = self.add_node(node)
                    if key is not None:
                        node_keys.append(key)

        edge_keys: list[str] = []
        edges = value.get("edges")
        if isinstance(edges, list):
            for edge in edges:
                if isinstance(edge, dict) and edge.get("$type") == EDGE:
                    key = self.add_edge(edge)
                    if key is not None:
                        edge_keys.append(key)

        sdk_length = value.get("length")
        self.paths.append(
            GraphPath(
                sdk_length=sdk_length if isinstance(sdk_length, int) else None,
                hop_count=len(edge_keys),
                node_keys=node_keys,
                edge_keys=edge_keys,
            )
        )


def parse_result(result: ResultLike, *, space: str | None, limits: ResultLimits) -> ParsedResult:
    """Consume a database result exactly once and build table and graph views."""

    collector = _GraphCollector(space=space, limits=limits)
    rows: list[dict[str, Any]] = []
    used_bytes = 0

    for row in result.iter_rows():
        if len(rows) >= limits.rows:
            collector.add_reason("max_rows")
            break
        row_bytes = len(
            json.dumps(
                row,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        )
        if used_bytes + row_bytes > limits.bytes:
            collector.add_reason("max_bytes")
            break
        rows.append(row)
        used_bytes += row_bytes
        collector.walk(row)

    if result.row_count > len(rows) and len(rows) >= limits.rows:
        collector.add_reason("max_rows")

    truncation = TruncationInfo(
        truncated=bool(collector.reasons),
        reasons=tuple(collector.reasons),
    )
    return ParsedResult(
        status=status_of(result),
        table=TableResult(
            columns=list(result.column_names),
            rows=rows,
            returned_row_count=len(rows),
            result_row_count=result.row_count,
            truncated=truncation.truncated,
        ),
        graph=ParsedGraph(
            space=space,
            nodes=list(collector.nodes.values()),
            edges=list(collector.edges.values()),
            paths=collector.paths,
        ),
        truncation=truncation,
    )


def status_of(result: ResultLike) -> QueryStatus:
    return QueryStatus(
        ok=result.error_code == SUCCEEDED,
        code=str(result.error_code),
        message=result.error_message,
        latency_us=result.latency_us,
        space=result.space_name or None,
        comment=result.comment or None,
    )


def node_key(space: str | None, vid: JsonValue) -> str:
    return f"{space or '_'}:{_stable_component(vid)}"


def edge_key(
    space: str | None, src: JsonValue, edge_type: str | None, rank: JsonValue, dst: JsonValue
) -> str:
    return ":".join(
        (
            space or "_",
            _stable_component(src),
            edge_type or "",
            _stable_component(rank),
            _stable_component(dst),
        )
    )


def _stable_component(value: JsonValue) -> str:
    if isinstance(value, str):
        return value
    if value is None or isinstance(value, (bool, int, float)):
        return str(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _mapping_value(value: JsonValue | None) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _string_list(value: JsonValue | None) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]
