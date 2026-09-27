"""Packaged MCP Apps resource loading."""

from __future__ import annotations

from importlib.resources import files

UI_RESOURCE_URI = "ui://nebula3/query-result.html"


def read_query_result_html() -> str:
    """Return the packaged single-file query result application."""

    resource = files("nebula3_mcp").joinpath("ui/query-result.html")
    try:
        html = resource.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError) as exc:
        raise RuntimeError("Packaged query-result UI resource is unavailable") from exc
    if not html.strip():
        raise RuntimeError("Packaged query-result UI resource is empty")
    return html
