"""Safe error types returned by nebula3-mcp tools."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

ErrorCategory = Literal[
    "configuration_error",
    "connection_error",
    "authentication_error",
    "validation_error",
    "policy_denied",
    "database_error",
    "result_limit",
    "serialization_error",
    "render_error",
]


class ErrorPayload(BaseModel):
    """Structured, non-sensitive error details."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    category: ErrorCategory
    message: str
    retryable: bool = False
    suggestion: str | None = None
    database_code: str | None = None
    code: str | None = None
    variables: tuple[str, ...] = ()


class NebulaMCPError(Exception):
    """An actionable error safe to expose to an MCP client."""

    def __init__(
        self,
        category: ErrorCategory,
        message: str,
        *,
        retryable: bool = False,
        suggestion: str | None = None,
        database_code: str | None = None,
        code: str | None = None,
        variables: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.category = category
        self.message = message
        self.retryable = retryable
        self.suggestion = suggestion
        self.database_code = database_code
        self.code = code
        self.variables = variables

    def as_payload(self) -> ErrorPayload:
        return ErrorPayload(
            category=self.category,
            message=self.message,
            retryable=self.retryable,
            suggestion=self.suggestion,
            database_code=self.database_code,
            code=self.code,
            variables=self.variables,
        )
