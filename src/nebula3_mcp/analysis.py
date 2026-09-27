"""Deterministic analysis of bounded returned rows."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from statistics import fmean, median
from typing import TypeGuard

from nebula3_mcp.models import Analysis, ColumnAnalysis, TopValue
from nebula3_mcp.serialization import JsonValue

_ISO_DATE_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}(?:$|T| )")
Number = int | float


def analyze_rows(
    rows: Sequence[Mapping[str, JsonValue]],
    truncated: bool,
    *,
    top_k: int = 10,
) -> Analysis:
    """Calculate reproducible facts without inferring beyond returned rows."""

    column_names = _column_names(rows)
    columns = {
        name: _analyze_column([row.get(name) for row in rows], top_k=top_k)
        for name in column_names
    }
    return Analysis(
        complete_result=not truncated,
        row_count=len(rows),
        columns=columns,
    )


def _column_names(rows: Sequence[Mapping[str, JsonValue]]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for name in row:
            if name not in seen:
                names.append(name)
                seen.add(name)
    return names


def _analyze_column(values: list[JsonValue | None], *, top_k: int) -> ColumnAnalysis:
    non_null = [value for value in values if value is not None]
    null_count = len(values) - len(non_null)
    if not non_null:
        return ColumnAnalysis(kind="empty", count=0, null_count=null_count)

    if all(_is_number(value) for value in non_null):
        numeric = [value for value in non_null if _is_number(value)]
        return ColumnAnalysis(
            kind="numeric",
            count=len(numeric),
            null_count=null_count,
            minimum=min(numeric),
            maximum=max(numeric),
            mean=fmean(numeric),
            median=float(median(numeric)),
        )

    if all(isinstance(value, str) and _is_temporal(value) for value in non_null):
        return ColumnAnalysis(kind="temporal", count=len(non_null), null_count=null_count)

    if all(isinstance(value, (str, bool)) for value in non_null):
        return ColumnAnalysis(
            kind="categorical",
            count=len(non_null),
            null_count=null_count,
            top_values=_top_values(non_null, top_k=top_k),
        )

    return ColumnAnalysis(kind="unsupported", count=len(non_null), null_count=null_count)


def _is_number(value: JsonValue) -> TypeGuard[Number]:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _is_temporal(value: str) -> bool:
    if not _ISO_DATE_PREFIX.match(value):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _top_values(values: Sequence[JsonValue], *, top_k: int) -> list[TopValue]:
    counts: dict[str, tuple[JsonValue, int]] = {}
    for value in values:
        display = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        previous = counts.get(display)
        counts[display] = (value, 1 if previous is None else previous[1] + 1)
    ordered = sorted(counts.items(), key=lambda item: (-item[1][1], item[0]))
    return [TopValue(value=value, count=count) for _, (value, count) in ordered[:top_k]]
