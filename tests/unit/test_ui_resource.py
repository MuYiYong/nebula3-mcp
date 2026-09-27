from __future__ import annotations

from pathlib import Path

import pytest

from nebula3_mcp import ui_resource


def test_packaged_query_result_ui_is_self_contained() -> None:
    html = ui_resource.read_query_result_html()

    assert "nebula_expand_node" not in html
    assert "default-src 'none'" in html
    assert "<script" in html


def test_missing_ui_resource_uses_stable_path_free_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ui_resource, "files", lambda _: tmp_path / "missing-package")

    with pytest.raises(RuntimeError) as raised:
        ui_resource.read_query_result_html()

    assert str(raised.value) == "Packaged query-result UI resource is unavailable"
    assert str(tmp_path) not in str(raised.value)


def test_empty_ui_resource_uses_stable_path_free_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = tmp_path / "package"
    resource = package / "ui" / "query-result.html"
    resource.parent.mkdir(parents=True)
    resource.write_text(" \n", encoding="utf-8")
    monkeypatch.setattr(ui_resource, "files", lambda _: package)

    with pytest.raises(RuntimeError) as raised:
        ui_resource.read_query_result_html()

    assert str(raised.value) == "Packaged query-result UI resource is empty"
    assert str(tmp_path) not in str(raised.value)
