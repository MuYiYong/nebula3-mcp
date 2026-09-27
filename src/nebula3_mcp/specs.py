"""Portable graph and chart artifact specifications."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from nebula3_mcp.models import (
    Analysis,
    ChartSpec,
    CytoscapeElement,
    CytoscapeElements,
    GraphSpec,
    ParsedResult,
)
from nebula3_mcp.result_parser import node_key
from nebula3_mcp.serialization import JsonValue

_VEGA_LITE_V5_SCHEMA = "https://vega.github.io/schema/vega-lite/v5.json"


def build_cytoscape_graph(parsed: ParsedResult) -> GraphSpec:
    """Build a client-renderable Cytoscape elements specification.

    NebulaGraph 3.x identifies a vertex by its VID and an edge by
    (src, edge type, rank, dst), so these identities are exposed directly.
    """

    nodes = [
        CytoscapeElement(
            data={
                "id": node.key,
                "vid": node.vid,
                "space": node.space,
                "type": node.tags[0] if node.tags else None,
                "tags": node.tags,
                "properties": node.properties,
                "placeholder": node.placeholder,
            }
        )
        for node in parsed.graph.nodes
    ]
    edges = [
        CytoscapeElement(
            data={
                "id": edge.key,
                "source": node_key(edge.space, edge.src),
                "target": node_key(edge.space, edge.dst),
                "space": edge.space,
                "type": edge.edge_type,
                "src": edge.src,
                "dst": edge.dst,
                "rank": edge.rank,
                "properties": edge.properties,
            }
        )
        for edge in parsed.graph.edges
    ]
    return GraphSpec(
        space=parsed.graph.space,
        elements=CytoscapeElements(
            nodes=[CytoscapeElement(data=_browser_safe(item.data)) for item in nodes],
            edges=[CytoscapeElement(data=_browser_safe(item.data)) for item in edges],
        ),
        paths=[path.model_dump(mode="json") for path in parsed.graph.paths],
        truncated=parsed.truncation.truncated,
    )


def _browser_safe(value: Any) -> Any:
    """Preserve exact graph integers across the JavaScript JSON boundary."""
    if isinstance(value, int) and not isinstance(value, bool) and abs(value) > 2**53 - 1:
        return str(value)
    if isinstance(value, dict):
        return {key: _browser_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_browser_safe(item) for item in value]
    return value


def build_vega_lite_specs(
    rows: Sequence[Mapping[str, JsonValue]],
    analysis: Analysis,
    max_charts: int = 3,
) -> list[ChartSpec]:
    """Choose a bounded, deterministic set of Vega-Lite v5 specifications."""

    if max_charts <= 0 or not rows:
        return []
    values = [dict(row) for row in rows]
    temporal = [name for name, column in analysis.columns.items() if column.kind == "temporal"]
    categorical = [
        name for name, column in analysis.columns.items() if column.kind == "categorical"
    ]
    numeric = [name for name, column in analysis.columns.items() if column.kind == "numeric"]
    specs: list[ChartSpec] = []

    for temporal_name in temporal:
        for numeric_name in numeric:
            _append_chart(
                specs,
                max_charts,
                description=f"{numeric_name} over {temporal_name} in returned rows",
                values=values,
                mark="line",
                encoding={
                    "x": {"field": temporal_name, "type": "temporal"},
                    "y": {"field": numeric_name, "type": "quantitative"},
                },
            )

    for category_name in categorical:
        for numeric_name in numeric:
            _append_chart(
                specs,
                max_charts,
                description=f"Mean {numeric_name} by {category_name} in returned rows",
                values=values,
                mark="bar",
                encoding={
                    "x": {"field": category_name, "type": "nominal", "sort": "-y"},
                    "y": {
                        "field": numeric_name,
                        "type": "quantitative",
                        "aggregate": "mean",
                    },
                },
            )

    for x_index, x_name in enumerate(numeric):
        for y_name in numeric[x_index + 1 :]:
            _append_chart(
                specs,
                max_charts,
                description=f"{y_name} versus {x_name} in returned rows",
                values=values,
                mark="point",
                encoding={
                    "x": {"field": x_name, "type": "quantitative"},
                    "y": {"field": y_name, "type": "quantitative"},
                },
            )

    for numeric_name in numeric:
        _append_chart(
            specs,
            max_charts,
            description=f"Distribution of {numeric_name} in returned rows",
            values=values,
            mark="bar",
            encoding={
                "x": {"field": numeric_name, "type": "quantitative", "bin": True},
                "y": {"aggregate": "count", "type": "quantitative"},
            },
        )
    return specs


def _append_chart(
    specs: list[ChartSpec],
    max_charts: int,
    *,
    description: str,
    values: list[dict[str, JsonValue]],
    mark: str,
    encoding: dict[str, Any],
) -> None:
    if len(specs) >= max_charts:
        return
    specs.append(
        ChartSpec(
            description=description,
            spec={
                "$schema": _VEGA_LITE_V5_SCHEMA,
                "description": description,
                "data": {"values": values},
                "mark": mark,
                "encoding": encoding,
            },
        )
    )
