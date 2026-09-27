from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.anyio
async def test_stdio_subprocess_initializes_lists_and_calls_tools() -> None:
    wheel_python = os.getenv("NEBULA_WHEEL_PYTHON")
    params = StdioServerParameters(
        command=wheel_python or sys.executable,
        args=[str(ROOT / "tests/fixtures/fake_stdio_server.py")],
        cwd=ROOT,
        env={} if wheel_python else {"PYTHONPATH": str(ROOT / "src")},
    )

    async with Client(params) as client:
        tools = await client.list_tools()
        result = await client.call_tool("nebula_test_connection", {})

    assert len(tools.tools) == 11
    assert result.is_error is False
    assert result.structured_content["version"] == "3.8.0"


@pytest.mark.anyio
async def test_production_stdio_subprocess_initializes_without_configuration() -> None:
    wheel_python = os.getenv("NEBULA_WHEEL_PYTHON")
    params = StdioServerParameters(
        command=wheel_python or sys.executable,
        args=["-m", "nebula3_mcp"],
        cwd=ROOT,
        env={} if wheel_python else {"PYTHONPATH": str(ROOT / "src")},
    )

    async with Client(params) as client:
        tools = await client.list_tools()
        result = await client.call_tool("nebula_test_connection", {})

    assert len(tools.tools) == 11
    assert result.is_error is True
    assert result.structured_content["error"]["code"] == "CONFIGURATION_REQUIRED"
