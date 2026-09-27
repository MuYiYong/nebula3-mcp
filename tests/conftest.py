from __future__ import annotations

import pytest
from pydantic import SecretStr

from nebula3_mcp.config import Settings


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        addresses="db-a:9669,db-b:9669",
        username="reader",
        password=SecretStr("runtime-secret"),
        connect_timeout_ms=4_000,
        request_timeout_ms=8_000,
    )
