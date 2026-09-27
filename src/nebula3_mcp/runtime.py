"""Safe lifecycle state for the MCP server."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from nebula3_mcp.config import ConfigurationProblem
from nebula3_mcp.errors import NebulaMCPError
from nebula3_mcp.service import NebulaService

RuntimeStatus = Literal["UNCONFIGURED", "CONNECT_FAILED", "READY"]


@dataclass
class RuntimeState:
    """The initialized MCP runtime, including safe startup failures."""

    status: RuntimeStatus
    service: NebulaService | None
    error: NebulaMCPError | None

    @classmethod
    def unconfigured(cls, problem: ConfigurationProblem) -> RuntimeState:
        error = NebulaMCPError(
            category="configuration_error",
            code="CONFIGURATION_REQUIRED",
            message="NebulaGraph database configuration is required",
            suggestion=(
                "Call nebula_configure_connection with the connection settings; "
                "or use installer --configure for local no-echo password entry"
            ),
            variables=problem.variable_names,
        )
        return cls(status="UNCONFIGURED", service=None, error=error)

    @classmethod
    def connect_failed(cls, error: NebulaMCPError) -> RuntimeState:
        return cls(status="CONNECT_FAILED", service=None, error=error)

    @classmethod
    def ready(cls, service: NebulaService) -> RuntimeState:
        return cls(status="READY", service=service, error=None)

    def require_service(self) -> NebulaService:
        if self.service is not None:
            return self.service
        if self.error is not None:
            raise self.error
        raise NebulaMCPError(
            category="configuration_error",
            message="MCP runtime state is invalid",
        )
