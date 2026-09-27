"""Pooled NebulaGraph 3.8 database access through nebula3-python."""

from __future__ import annotations

import ssl
from collections.abc import Callable, Iterator, Sequence
from threading import RLock
from typing import Any, Protocol, cast

import anyio
from nebula3.Config import Config, SSL_config  # type: ignore[import-untyped]
from nebula3.gclient.net import ConnectionPool  # type: ignore[import-untyped]

from nebula3_mcp.config import Settings
from nebula3_mcp.errors import NebulaMCPError
from nebula3_mcp.serialization import JsonValue
from nebula3_mcp.values import convert_value

SUCCEEDED = 0
E_SESSION_INVALID = -1002
E_SESSION_TIMEOUT = -1003
_IO_TIMEOUT = 3  # nebula3.Exception.IOErrorException.E_TIMEOUT


class ResultLike(Protocol):
    """The normalized result surface consumed by the parser and service."""

    error_code: int
    error_message: str
    latency_us: int
    space_name: str
    comment: str
    column_names: list[str]
    row_count: int
    plan_desc: Any

    def iter_rows(self) -> Iterator[dict[str, JsonValue]]: ...


class SessionLike(Protocol):
    def execute(self, stmt: str) -> Any: ...

    def release(self) -> None: ...


class PoolLike(Protocol):
    def get_session(self, user_name: str, password: str) -> SessionLike: ...

    def close(self) -> None: ...


PoolFactory = Callable[[list[tuple[str, int]], Config, SSL_config | None], PoolLike]


class SdkResult:
    """Adapter from ``nebula3.data.ResultSet.ResultSet`` to :class:`ResultLike`."""

    def __init__(self, result_set: Any) -> None:
        self._result_set = result_set
        self.error_code = int(result_set.error_code())
        self.error_message = result_set.error_msg()
        self.latency_us = int(result_set.latency() or 0)
        self.space_name = result_set.space_name()
        comment = getattr(getattr(result_set, "_resp", None), "comment", None)
        self.comment = comment.decode("utf-8", errors="replace") if comment else ""
        self.column_names = list(result_set.keys())
        self.row_count = int(result_set.row_size())
        self.plan_desc = result_set.plan_desc()

    def iter_rows(self) -> Iterator[dict[str, JsonValue]]:
        # Convert lazily so row/byte limits stop work before the whole result is decoded.
        for index in range(self.row_count):
            values = self._result_set.row_values(index)
            yield {
                name: convert_value(value)
                for name, value in zip(self.column_names, values, strict=False)
            }


def _default_pool_factory(
    addresses: list[tuple[str, int]], config: Config, ssl_config: SSL_config | None
) -> PoolLike:
    pool = ConnectionPool()
    pool.init(addresses, config, ssl_config)
    return cast(PoolLike, pool)


class DatabaseGateway:
    """Owns a ConnectionPool with one retained session; offloads its synchronous calls."""

    def __init__(self, settings: Settings, pool_factory: PoolFactory | None = None) -> None:
        self._settings = settings
        self._pool_factory = pool_factory or _default_pool_factory
        self._pool: PoolLike | None = None
        self._session: SessionLike | None = None
        self._session_lock = RLock()

    def open(self) -> None:
        if self._pool is not None:
            return
        config = Config()
        # Only connectivity probes run during init; no idle connection is opened with the
        # short connect timeout. The retained session then uses the request timeout.
        config.min_connection_pool_size = 0
        config.max_connection_pool_size = 2
        config.timeout = self._settings.connect_timeout_ms
        ssl_config = _ssl_config(self._settings)
        try:
            pool = self._pool_factory(self._settings.address_list(), config, ssl_config)
        except Exception as exc:  # noqa: BLE001 - redact all SDK startup failures at boundary
            raise _safe_database_error(exc, operation="connect") from None
        config.timeout = self._settings.request_timeout_ms
        try:
            session = pool.get_session(
                self._settings.username, self._settings.password.get_secret_value()
            )
        except Exception as exc:  # noqa: BLE001
            pool.close()
            raise _safe_database_error(exc, operation="connect") from None
        self._pool, self._session = pool, session
        if self._settings.default_space is not None:
            result = self._execute_sync(f"USE {quote_name(self._settings.default_space)}")
            if result.error_code != SUCCEEDED:
                self.close()
                raise NebulaMCPError(
                    category="configuration_error",
                    code="INVALID_DEFAULT_SPACE",
                    database_code=str(result.error_code),
                    message="NEBULA_DEFAULT_SPACE 无法选择，请检查图空间名称和账号权限。",
                    variables=("NEBULA_DEFAULT_SPACE",),
                )

    def close(self) -> None:
        with self._session_lock:
            pool, self._pool = self._pool, None
            session, self._session = self._session, None
            if pool is not None:
                try:
                    if session is not None:
                        session.release()
                except Exception:  # noqa: BLE001, S110 - sign-out is best effort on shutdown
                    pass
                finally:
                    pool.close()

    def _require_session(self) -> SessionLike:
        if self._pool is None or self._session is None:
            raise NebulaMCPError(
                category="configuration_error",
                message="Database gateway is not open",
                suggestion="Open the database gateway before executing tools",
            )
        return self._session

    def _execute_sync(self, statement: str) -> ResultLike:
        with self._session_lock:
            try:
                result = SdkResult(self._require_session().execute(statement))
            except NebulaMCPError:
                raise
            except Exception as exc:  # noqa: BLE001 - redact SDK failures
                raise _safe_database_error(exc, operation="query") from None
            if result.error_code in {E_SESSION_INVALID, E_SESSION_TIMEOUT}:
                raise _session_expired(result.error_code)
            return result

    async def execute(self, statement: str) -> ResultLike:
        return await anyio.to_thread.run_sync(self._execute_sync, statement)

    def _version_sync(self) -> str:
        with self._session_lock:
            # Check the retained session itself without silently opening a new one.
            health = self._execute_sync("RETURN 1 AS health")
            if health.error_code != SUCCEEDED:
                raise NebulaMCPError(
                    category="connection_error",
                    database_code=str(health.error_code),
                    message="Database session health check failed",
                    suggestion="Check database availability and explicitly reconfigure "
                    "the connection if the session has expired",
                )
            hosts = self._execute_sync("SHOW HOSTS GRAPH")
            if hosts.error_code != SUCCEEDED:
                return "unknown"
            versions = [row.get("Version") for row in hosts.iter_rows()]
            return _first_text(versions) or "unknown"

    async def version(self) -> str:
        return await anyio.to_thread.run_sync(self._version_sync)


def quote_name(name: str) -> str:
    """Quote a NebulaGraph schema object name with backticks."""
    return "`" + name.replace("`", "") + "`"


def _first_text(values: Sequence[JsonValue]) -> str | None:
    for value in values:
        if isinstance(value, str) and value:
            return value
    return None


def _ssl_config(settings: Settings) -> SSL_config | None:
    if not settings.tls_enabled:
        return None
    config = SSL_config()
    if settings.tls_ca_file is not None:
        config.cert_reqs = ssl.CERT_REQUIRED
        config.ca_certs = settings.tls_ca_file
    return config


def _session_expired(code: int) -> NebulaMCPError:
    return NebulaMCPError(
        category="connection_error",
        database_code=str(code),
        message="Database session is no longer valid",
        suggestion="Restart this MCP server or call nebula_configure_connection to open a "
        "new session; the previous session space must be selected again",
    )


def _safe_database_error(exc: Exception, *, operation: str) -> NebulaMCPError:
    exception_name = type(exc).__name__.lower()
    if "auth" in exception_name:
        return NebulaMCPError(
            category="authentication_error",
            message="Database authentication failed",
            suggestion="Check the configured database username and password",
        )
    if (
        isinstance(exc, TimeoutError)
        or "timeout" in exception_name
        or ("ioerror" in exception_name and getattr(exc, "type", None) == _IO_TIMEOUT)
    ):
        return NebulaMCPError(
            category="connection_error",
            message="Database request timed out",
            retryable=True,
            suggestion="Check network reachability and configured timeouts",
        )
    if operation == "connect":
        return NebulaMCPError(
            category="connection_error",
            message="Database connection failed",
            retryable=True,
            suggestion="Check host, port, TLS mode, and graphd availability",
        )
    if "ioerror" in exception_name or "connection" in exception_name:
        return NebulaMCPError(
            category="connection_error",
            message="Database connection was interrupted",
            retryable=True,
            suggestion="Check graphd availability; reconfigure the connection if it persists",
        )
    return NebulaMCPError(
        category="database_error",
        message="Database query failed",
        retryable=False,
        suggestion="Validate the nGQL statement and target space before retrying",
    )
