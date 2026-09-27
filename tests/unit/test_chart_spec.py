from __future__ import annotations

from nebula3_mcp.analysis import analyze_rows
from nebula3_mcp.specs import build_vega_lite_specs

ROWS = [
    {"sector": "A", "score": 1.0},
    {"sector": "A", "score": 3.0},
    {"sector": "B", "score": 2.0},
]


def test_category_numeric_data_generates_vega_lite_bar_spec() -> None:
    specs = build_vega_lite_specs(ROWS, analyze_rows(ROWS, False))

    assert specs[0].format == "vega-lite-v5"
    assert specs[0].spec["$schema"].endswith("/vega-lite/v5.json")
    assert specs[0].spec["mark"] == "bar"
    assert specs[0].spec["encoding"]["x"]["type"] == "nominal"
    assert specs[0].spec["encoding"]["y"]["aggregate"] == "mean"
    assert specs[0].spec["data"]["values"] == ROWS


def test_temporal_numeric_data_prefers_line_chart() -> None:
    rows = [
        {"at": "2026-08-29T10:00:00+08:00", "score": 1},
        {"at": "2026-08-30T10:00:00+08:00", "score": 2},
    ]

    specs = build_vega_lite_specs(rows, analyze_rows(rows, False))

    assert specs[0].spec["mark"] == "line"
    assert specs[0].spec["encoding"]["x"]["type"] == "temporal"


def test_unsupported_rows_do_not_generate_a_chart() -> None:
    rows = [{"payload": {"nested": "value"}}]

    assert build_vega_lite_specs(rows, analyze_rows(rows, False)) == []


def test_max_charts_is_honored() -> None:
    rows = [
        {"category": "A", "x": 1, "y": 2},
        {"category": "B", "x": 2, "y": 3},
    ]

    specs = build_vega_lite_specs(rows, analyze_rows(rows, False), max_charts=1)

    assert len(specs) == 1
