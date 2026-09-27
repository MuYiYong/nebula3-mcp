from __future__ import annotations

from nebula3_mcp.errors import NebulaMCPError


def test_error_payload_is_actionable_and_safe() -> None:
    error = NebulaMCPError(
        category="connection_error",
        message="Database connection failed",
        retryable=True,
        suggestion="Check host and port",
    )

    assert error.as_payload().model_dump() == {
        "category": "connection_error",
        "message": "Database connection failed",
        "retryable": True,
        "suggestion": "Check host and port",
        "database_code": None,
        "code": None,
        "variables": (),
    }


def test_error_string_uses_safe_message_only() -> None:
    error = NebulaMCPError(category="authentication_error", message="Authentication failed")

    assert str(error) == "Authentication failed"


def test_error_payload_includes_stable_machine_code() -> None:
    payload = NebulaMCPError(
        category="configuration_error",
        code="CONFIGURATION_REQUIRED",
        message="Database configuration is required",
        variables=("NEBULA_PASSWORD",),
    ).as_payload()

    assert payload.code == "CONFIGURATION_REQUIRED"
    assert payload.variables == ("NEBULA_PASSWORD",)
