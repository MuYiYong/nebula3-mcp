from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal

import pytest

from nebula3_mcp.errors import NebulaMCPError
from nebula3_mcp.serialization import to_json_value


@dataclass
class FakeExtraInfo:
    cursor: str
    affected_nodes: int
    affected_edges: int


def test_datetime_date_time_and_bytes_keep_type_information() -> None:
    assert to_json_value(datetime(2026, 8, 30, 9, 5, tzinfo=timezone.utc)) == {
        "$type": "datetime",
        "value": "2026-08-30T09:05:00+00:00",
    }
    assert to_json_value(date(2026, 8, 30)) == {
        "$type": "date",
        "value": "2026-08-30",
    }
    assert to_json_value(time(9, 5, 1)) == {
        "$type": "time",
        "value": "09:05:01",
    }
    assert to_json_value(b"abc") == {
        "$type": "bytes",
        "encoding": "base64",
        "value": "YWJj",
    }


def test_decimal_and_nonfinite_float_are_explicit() -> None:
    assert to_json_value(Decimal("1.250")) == {"$type": "decimal", "value": "1.250"}
    assert to_json_value(math.inf) == {"$type": "float", "value": "Infinity"}
    assert to_json_value(math.nan) == {"$type": "float", "value": "NaN"}


def test_nested_map_and_set_are_recursive_and_deterministic() -> None:
    actual = to_json_value({"at": date(2026, 8, 30), "tags": {"b", "a"}})

    assert actual == {
        "at": {"$type": "date", "value": "2026-08-30"},
        "tags": {"$type": "set", "values": ["a", "b"]},
    }
    json.dumps(actual, allow_nan=False)


def test_native_nested_values_remain_native_json() -> None:
    actual = to_json_value({"ok": True, "count": 3, "items": [None, "x", 2.5]})

    assert actual == {"ok": True, "count": 3, "items": [None, "x", 2.5]}
    json.dumps(actual, allow_nan=False)


def test_sdk_dataclass_metadata_is_converted_by_declared_fields() -> None:
    assert to_json_value(FakeExtraInfo(cursor="", affected_nodes=2, affected_edges=1)) == {
        "cursor": "",
        "affected_nodes": 2,
        "affected_edges": 1,
    }


def test_unsupported_value_raises_safe_error() -> None:
    with pytest.raises(NebulaMCPError) as caught:
        to_json_value(object())

    assert caught.value.category == "serialization_error"
    assert "object at" not in str(caught.value)
