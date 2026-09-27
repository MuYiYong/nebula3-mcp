from __future__ import annotations

from nebula3_mcp.analysis import analyze_rows


def test_numeric_and_categorical_stats_are_deterministic() -> None:
    analysis = analyze_rows(
        [
            {"sector": "A", "score": 1.0},
            {"sector": "A", "score": 3.0},
            {"sector": "B", "score": None},
        ],
        truncated=True,
    )

    assert analysis.scope == "returned_rows"
    assert analysis.complete_result is False
    assert analysis.row_count == 3
    assert analysis.columns["score"].median == 2.0
    assert analysis.columns["score"].null_count == 1
    assert analysis.columns["sector"].top_values[0].value == "A"
    assert analysis.columns["sector"].top_values[0].count == 2


def test_boolean_values_are_not_treated_as_numbers() -> None:
    analysis = analyze_rows([{"flag": True}, {"flag": False}], truncated=False)

    assert analysis.complete_result is True
    assert analysis.columns["flag"].kind == "categorical"
    assert analysis.columns["flag"].mean is None


def test_top_values_break_ties_by_display_value() -> None:
    analysis = analyze_rows(
        [{"sector": "B"}, {"sector": "A"}, {"sector": "C"}],
        truncated=False,
    )

    assert [item.value for item in analysis.columns["sector"].top_values] == ["A", "B", "C"]
