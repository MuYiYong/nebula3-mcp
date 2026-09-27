from __future__ import annotations

from typing import Any

import pytest
from nebula3.common.ttypes import ErrorCode

from nebula3_mcp.config import Settings
from nebula3_mcp.database import DatabaseGateway, SdkResult
from nebula3_mcp.errors import NebulaMCPError


class FakeResultSet:
    """Mimics the public surface of ``nebula3.data.ResultSet.ResultSet``."""

    def __init__(self, code: int = 0, message: str = "", space: str = "") -> None:
        self.code, self.message, self.space = code, message, space
        self.rows: list[list[Any]] = []
        self.columns: list[str] = []

    def error_code(self) -> int:
        return self.code

    def error_msg(self) -> str:
        return self.message

    def latency(self) -> int:
        return 5

    def space_name(self) -> str:
        return self.space

    def keys(self) -> list[str]:
        return self.columns

    def row_size(self) -> int:
        return len(self.rows)

    def row_values(self, index: int) -> list[Any]:
        return self.rows[index]

    def plan_desc(self) -> None:
        return None


class StringValue:
    def __init__(self, value: str) -> None:
        self.value = value

    def _get_type_name(self) -> str:
        return "string"

    def as_string(self) -> str:
        return self.value


class FakeSession:
    def __init__(self, *, error: Exception | None = None, code: int = 0) -> None:
        self.error = error
        self.code = code
        self.statements: list[str] = []
        self.released = 0

    def execute(self, statement: str) -> FakeResultSet:
        self.statements.append(statement)
        if self.error is not None:
            raise self.error
        if statement == "SHOW HOSTS GRAPH":
            result = FakeResultSet()
            result.columns = ["Host", "Version"]
            result.rows = [[StringValue("graphd"), StringValue("3.8.0")]]
            return result
        return FakeResultSet(self.code, space="demo" if statement.startswith("USE") else "")

    def release(self) -> None:
        self.released += 1


class FakePool:
    def __init__(self, session: FakeSession, *, auth_error: Exception | None = None) -> None:
        self.session = session
        self.auth_error = auth_error
        self.sessions = 0
        self.closed = False

    def get_session(self, user_name: str, password: str) -> FakeSession:
        if self.auth_error is not None:
            raise self.auth_error
        assert (user_name, password) == ("reader", "runtime-secret")
        self.sessions += 1
        return self.session

    def close(self) -> None:
        self.closed = True


class PoolFactory:
    def __init__(self, pool: FakePool | Exception) -> None:
        self.pool = pool
        self.calls: list[tuple[Any, ...]] = []
        self.timeout_at_init: int | None = None

    def __call__(self, addresses: Any, config: Any, ssl_config: Any) -> FakePool:
        self.calls.append((addresses, config, ssl_config))
        self.timeout_at_init = config.timeout
        if isinstance(self.pool, Exception):
            raise self.pool
        return self.pool


@pytest.mark.anyio
async def test_execute_reuses_one_session_until_close(settings: Settings) -> None:
    session = FakeSession()
    pool = FakePool(session)
    gateway = DatabaseGateway(settings, pool_factory=PoolFactory(pool))
    gateway.open()

    first = await gateway.execute("RETURN 1")
    await gateway.execute("RETURN 2")
    version = await gateway.version()

    assert first.error_code == 0
    assert version == "3.8.0"
    assert session.statements == ["RETURN 1", "RETURN 2", "RETURN 1 AS health", "SHOW HOSTS GRAPH"]
    assert pool.sessions == 1
    gateway.close()
    assert session.released == 1
    assert pool.closed is True


def test_open_maps_settings_to_nebula3_pool_config(settings: Settings) -> None:
    factory = PoolFactory(FakePool(FakeSession()))
    gateway = DatabaseGateway(settings, pool_factory=factory)

    gateway.open()

    addresses, config, ssl_config = factory.calls[0]
    assert addresses == [("db-a", 9669), ("db-b", 9669)]
    assert factory.timeout_at_init == 4_000
    assert config.timeout == 8_000
    assert config.min_connection_pool_size == 0
    assert ssl_config is None
    gateway.close()


def test_tls_with_ca_file_requires_certificate_verification(settings: Settings) -> None:
    import ssl

    factory = PoolFactory(FakePool(FakeSession()))
    tls = settings.model_copy(update={"tls_enabled": True, "tls_ca_file": "/tmp/ca.pem"})
    DatabaseGateway(tls, pool_factory=factory).open()

    ssl_config = factory.calls[0][2]
    assert ssl_config.cert_reqs == ssl.CERT_REQUIRED
    assert ssl_config.ca_certs == "/tmp/ca.pem"


def test_default_space_is_selected_when_opening(settings: Settings) -> None:
    session = FakeSession()
    gateway = DatabaseGateway(
        settings.model_copy(update={"default_space": "demo"}),
        pool_factory=PoolFactory(FakePool(session)),
    )

    gateway.open()

    assert session.statements == ["USE `demo`"]
    gateway.close()


def test_unusable_default_space_closes_the_pool(settings: Settings) -> None:
    session = FakeSession(code=ErrorCode.E_EXECUTION_ERROR)
    pool = FakePool(session)
    gateway = DatabaseGateway(
        settings.model_copy(update={"default_space": "missing"}),
        pool_factory=PoolFactory(pool),
    )

    with pytest.raises(NebulaMCPError) as caught:
        gateway.open()

    assert caught.value.code == "INVALID_DEFAULT_SPACE"
    assert pool.closed is True


def test_connection_and_authentication_failures_are_redacted(settings: Settings) -> None:
    unreachable = DatabaseGateway(
        settings, pool_factory=PoolFactory(RuntimeError("runtime-secret host down"))
    )
    with pytest.raises(NebulaMCPError) as caught:
        unreachable.open()
    assert caught.value.category == "connection_error"
    assert "runtime-secret" not in str(caught.value)

    class AuthFailedException(Exception):
        pass

    pool = FakePool(FakeSession(), auth_error=AuthFailedException("runtime-secret"))
    rejected = DatabaseGateway(settings, pool_factory=PoolFactory(pool))
    with pytest.raises(NebulaMCPError) as caught:
        rejected.open()
    assert caught.value.category == "authentication_error"
    assert pool.closed is True


@pytest.mark.anyio
async def test_execute_keeps_session_after_sdk_error(settings: Settings) -> None:
    session = FakeSession(error=RuntimeError("runtime-secret must not escape"))
    gateway = DatabaseGateway(settings, pool_factory=PoolFactory(FakePool(session)))
    gateway.open()

    with pytest.raises(NebulaMCPError) as caught:
        await gateway.execute("RETURN 1")

    assert caught.value.category == "database_error"
    assert str(caught.value) == "Database query failed"
    assert "runtime-secret" not in str(caught.value)
    assert session.released == 0
    gateway.close()
    assert session.released == 1


@pytest.mark.anyio
async def test_io_timeout_is_retryable_connection_error(settings: Settings) -> None:
    class IOErrorException(Exception):
        type = 3

    gateway = DatabaseGateway(
        settings, pool_factory=PoolFactory(FakePool(FakeSession(error=IOErrorException())))
    )
    gateway.open()

    with pytest.raises(NebulaMCPError) as caught:
        await gateway.execute("RETURN 1")

    assert caught.value.category == "connection_error"
    assert caught.value.retryable is True
    assert "timed out" in caught.value.message


@pytest.mark.anyio
async def test_expired_session_is_not_silently_replaced(settings: Settings) -> None:
    pool = FakePool(FakeSession(code=ErrorCode.E_SESSION_INVALID))
    gateway = DatabaseGateway(settings, pool_factory=PoolFactory(pool))
    gateway.open()

    with pytest.raises(NebulaMCPError) as caught:
        await gateway.version()

    assert caught.value.category == "connection_error"
    assert caught.value.database_code == "-1002"
    assert pool.sessions == 1
    gateway.close()


def test_close_is_idempotent(settings: Settings) -> None:
    pool = FakePool(FakeSession())
    gateway = DatabaseGateway(settings, pool_factory=PoolFactory(pool))
    gateway.open()

    gateway.close()
    gateway.close()

    assert pool.closed is True


@pytest.mark.anyio
async def test_execute_before_open_is_actionable(settings: Settings) -> None:
    gateway = DatabaseGateway(settings, pool_factory=PoolFactory(FakePool(FakeSession())))

    with pytest.raises(NebulaMCPError) as caught:
        await gateway.execute("RETURN 1")

    assert caught.value.category == "configuration_error"
    assert caught.value.suggestion == "Open the database gateway before executing tools"


def test_sdk_result_adapter_converts_rows_lazily() -> None:
    result_set = FakeResultSet(space="demo")
    result_set.columns = ["name"]
    result_set.rows = [[StringValue("a")], [StringValue("b")]]

    adapted = SdkResult(result_set)
    rows = adapted.iter_rows()

    assert (adapted.error_code, adapted.space_name, adapted.row_count) == (0, "demo", 2)
    assert next(rows) == {"name": "a"}
    assert next(rows) == {"name": "b"}
