"""Environment configuration for nebula3-mcp."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Literal, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
)


def _parse_bool(value: str, variable: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{variable} must be a boolean")


def _redact_username(username: str) -> str:
    if len(username) <= 2:
        return "*" * len(username)
    return f"{username[0]}***{username[-1]}"


LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
IssueReason = Literal["missing", "invalid"]

# NebulaGraph 3.x space names: plain identifiers, or any backtick-free UTF-8 name.
SPACE_NAME = re.compile(r"[^`\x00-\x1f]{1,128}")

_FIELD_ENVIRONMENT_NAMES = {
    "addresses": "NEBULA_ADDRESSES",
    "username": "NEBULA_USERNAME",
    "password": "NEBULA_PASSWORD",
    "default_space": "NEBULA_DEFAULT_SPACE",
    "connect_timeout_ms": "NEBULA_CONNECT_TIMEOUT_MS",
    "request_timeout_ms": "NEBULA_REQUEST_TIMEOUT_MS",
    "tls_enabled": "NEBULA_TLS_ENABLED",
    "tls_ca_file": "NEBULA_TLS_CA_FILE",
    "allow_mutations": "NEBULA_ALLOW_MUTATIONS",
    "max_rows": "NEBULA_MAX_ROWS",
    "max_nodes": "NEBULA_MAX_NODES",
    "max_edges": "NEBULA_MAX_EDGES",
    "max_bytes": "NEBULA_MAX_BYTES",
    "log_level": "NEBULA_LOG_LEVEL",
}


class ConfigurationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    variable: str
    reason: IssueReason


class ConfigurationProblem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    issues: tuple[ConfigurationIssue, ...]

    @property
    def variable_names(self) -> tuple[str, ...]:
        return tuple(sorted({issue.variable for issue in self.issues}))


class Settings(BaseModel):
    """Validated settings loaded once when the MCP server starts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    addresses: str
    username: str
    password: SecretStr
    default_space: str | None = None
    connect_timeout_ms: int = Field(5_000, ge=100, le=300_000)
    request_timeout_ms: int = Field(60_000, ge=100, le=3_600_000)
    tls_enabled: bool = False
    tls_ca_file: str | None = None
    allow_mutations: bool = False
    max_rows: int = Field(100, ge=1, le=10_000)
    max_nodes: int = Field(500, ge=1, le=20_000)
    max_edges: int = Field(1_000, ge=1, le=50_000)
    max_bytes: int = Field(1_048_576, ge=1_024, le=50_000_000)
    log_level: LogLevel = "INFO"

    @field_validator("addresses")
    @classmethod
    def validate_addresses(cls, value: str) -> str:
        addresses: list[str] = []
        for raw_address in value.split(","):
            address = raw_address.strip()
            host, separator, raw_port = address.rpartition(":")
            if not separator or not host or not raw_port.isdigit():
                raise ValueError("NEBULA_ADDRESSES must contain host:port values")
            port = int(raw_port)
            if not 1 <= port <= 65_535:
                raise ValueError("NEBULA_ADDRESSES port must be between 1 and 65535")
            addresses.append(f"{host}:{port}")
        if not addresses:
            raise ValueError("NEBULA_ADDRESSES is required")
        return ",".join(addresses)

    @field_validator("default_space")
    @classmethod
    def validate_default_space(cls, value: str | None) -> str | None:
        if value is not None and SPACE_NAME.fullmatch(value) is None:
            raise ValueError("NEBULA_DEFAULT_SPACE is not a valid space name")
        return value

    def address_list(self) -> list[tuple[str, int]]:
        """Return (host, port) pairs in the form expected by nebula3-python."""
        pairs: list[tuple[str, int]] = []
        for address in self.addresses.split(","):
            host, _, port = address.rpartition(":")
            pairs.append((host.strip("[]"), int(port)))
        return pairs

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        source = os.environ if env is None else env

        def required(name: str) -> str:
            value = source.get(name, "").strip()
            if not value:
                raise ValueError(f"{name} is required")
            return value

        def integer(name: str, default: int) -> int:
            raw_value = source.get(name, str(default))
            try:
                return int(raw_value)
            except ValueError as exc:
                raise ValueError(f"{name} must be an integer") from exc

        return cls(
            addresses=required("NEBULA_ADDRESSES"),
            username=required("NEBULA_USERNAME"),
            password=SecretStr(required("NEBULA_PASSWORD")),
            default_space=source.get("NEBULA_DEFAULT_SPACE", "").strip() or None,
            connect_timeout_ms=integer("NEBULA_CONNECT_TIMEOUT_MS", 5_000),
            request_timeout_ms=integer("NEBULA_REQUEST_TIMEOUT_MS", 60_000),
            tls_enabled=_parse_bool(
                source.get("NEBULA_TLS_ENABLED", "false"), "NEBULA_TLS_ENABLED"
            ),
            tls_ca_file=source.get("NEBULA_TLS_CA_FILE", "").strip() or None,
            allow_mutations=_parse_bool(
                source.get("NEBULA_ALLOW_MUTATIONS", "false"),
                "NEBULA_ALLOW_MUTATIONS",
            ),
            max_rows=integer("NEBULA_MAX_ROWS", 100),
            max_nodes=integer("NEBULA_MAX_NODES", 500),
            max_edges=integer("NEBULA_MAX_EDGES", 1_000),
            max_bytes=integer("NEBULA_MAX_BYTES", 1_048_576),
            log_level=cast(LogLevel, source.get("NEBULA_LOG_LEVEL", "INFO").upper()),
        )

    def public_view(self) -> dict[str, object]:
        return {
            "address_count": len(self.addresses.split(",")),
            "username": _redact_username(self.username),
            "default_space": self.default_space,
            "tls_enabled": self.tls_enabled,
            "tls_verified": self.tls_enabled and self.tls_ca_file is not None,
            "allow_mutations": self.allow_mutations,
        }


def load_settings(
    env: Mapping[str, str] | None = None,
) -> tuple[Settings | None, ConfigurationProblem | None]:
    source = os.environ if env is None else env
    required = ("NEBULA_ADDRESSES", "NEBULA_USERNAME", "NEBULA_PASSWORD")
    missing = tuple(name for name in required if not source.get(name, "").strip())
    if missing:
        return None, ConfigurationProblem(
            issues=tuple(ConfigurationIssue(variable=name, reason="missing") for name in missing)
        )
    try:
        return Settings.from_env(source), None
    except ValidationError as exc:
        variables: set[str] = set()
        for error in exc.errors():
            location = error["loc"]
            if location:
                variables.add(_FIELD_ENVIRONMENT_NAMES[str(location[0])])
        return None, ConfigurationProblem(
            issues=tuple(
                ConfigurationIssue(variable=name, reason="invalid") for name in sorted(variables)
            )
        )
    except ValueError as exc:
        variable = str(exc).partition(" ")[0]
        return None, ConfigurationProblem(
            issues=(ConfigurationIssue(variable=variable, reason="invalid"),)
        )


def load_environments(
    env: Mapping[str, str] | None = None,
) -> tuple[dict[str, Settings], str | None]:
    """Load independent named environments, without inheriting another account's settings."""
    import json

    from nebula3_mcp.errors import NebulaMCPError

    source = os.environ if env is None else env
    raw = source.get("NEBULA_ENVIRONMENTS")
    try:
        active = source.get("NEBULA_ENVIRONMENT")
        if active is not None and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", active):
            raise ValueError
        if raw is None:
            return {}, active
        values = json.loads(raw)
        if not isinstance(values, dict) or not values:
            raise ValueError
        environments = {}
        for name, values_by_key in values.items():
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
                raise ValueError
            if not isinstance(values_by_key, dict) or not all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in values_by_key.items()
            ):
                raise ValueError
            environments[name] = Settings.from_env(values_by_key)
        if active is None and len(environments) == 1:
            active = next(iter(environments))
        if active is not None and active not in environments:
            raise ValueError
        return environments, active
    except (ValueError, TypeError):
        raise NebulaMCPError(
            category="configuration_error", code="INVALID_ENVIRONMENTS",
            message="检查 NEBULA_ENVIRONMENTS JSON 和 NEBULA_ENVIRONMENT 名称；每套环境需独立配置。",
        ) from None
