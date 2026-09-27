"""Deterministic stdio MCP process used only by protocol tests."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, ClassVar

from pydantic import SecretStr

from nebula3_mcp.config import Settings
from nebula3_mcp.server import create_server
from nebula3_mcp.service import NebulaService


class FakeResult:
    error_code = 0
    error_message = ""
    latency_us = 1
    space_name = ""
    comment = ""
    column_names: ClassVar[list[str]] = []
    row_count = 0
    plan_desc: Any = None

    def iter_rows(self) -> Iterator[dict[str, Any]]:
        return iter(())


class FakeGateway:
    async def version(self) -> str:
        return "3.8.0"

    async def execute(self, statement: str) -> FakeResult:
        del statement
        return FakeResult()


def main() -> None:
    settings = Settings(
        addresses="fixture.invalid:9669",
        username="fixture",
        password=SecretStr("fixture-only"),
    )
    service = NebulaService(settings, FakeGateway())
    create_server(service=service).run("stdio")


if __name__ == "__main__":
    main()
