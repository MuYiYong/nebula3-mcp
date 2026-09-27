from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from nebula3_mcp.config import Settings
from nebula3_mcp.database import DatabaseGateway
from nebula3_mcp.service import NebulaService


@pytest.fixture
async def remote_service() -> AsyncIterator[NebulaService]:
    settings = Settings.from_env()
    gateway = DatabaseGateway(settings)
    gateway.open()
    try:
        yield NebulaService(settings, gateway)
    finally:
        gateway.close()
