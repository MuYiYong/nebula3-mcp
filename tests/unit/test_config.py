from __future__ import annotations

import json

import pytest

from nebula3_mcp.config import Settings, load_environments, load_settings
from nebula3_mcp.errors import NebulaMCPError

BASE_ENV = {
    "NEBULA_ADDRESSES": "db-a:9669,db-b:9669",
    "NEBULA_USERNAME": "reader",
    "NEBULA_PASSWORD": "secret-value",
}


def test_settings_parse_defaults_and_redact_secrets() -> None:
    settings = Settings.from_env(BASE_ENV)

    assert settings.addresses == "db-a:9669,db-b:9669"
    assert settings.address_list() == [("db-a", 9669), ("db-b", 9669)]
    assert settings.password.get_secret_value() == "secret-value"
    assert settings.allow_mutations is False
    assert settings.max_rows == 100
    assert "secret-value" not in repr(settings)
    assert settings.public_view() == {
        "address_count": 2,
        "username": "r***r",
        "default_space": None,
        "tls_enabled": False,
        "tls_verified": False,
        "allow_mutations": False,
    }


def test_settings_parse_boolean_numeric_space_and_tls_overrides() -> None:
    settings = Settings.from_env(
        {
            **BASE_ENV,
            "NEBULA_ALLOW_MUTATIONS": "yes",
            "NEBULA_TLS_ENABLED": "1",
            "NEBULA_TLS_CA_FILE": "/etc/nebula/ca.pem",
            "NEBULA_MAX_ROWS": "25",
            "NEBULA_DEFAULT_SPACE": "basketballplayer",
            "NEBULA_REQUEST_TIMEOUT_MS": "9000",
        }
    )

    assert settings.allow_mutations is True
    assert settings.tls_enabled is True
    assert settings.public_view()["tls_verified"] is True
    assert settings.max_rows == 25
    assert settings.default_space == "basketballplayer"
    assert settings.request_timeout_ms == 9_000


def test_ipv6_brackets_are_removed_for_the_sdk() -> None:
    settings = Settings.from_env({**BASE_ENV, "NEBULA_ADDRESSES": "[::1]:9669"})

    assert settings.address_list() == [("::1", 9669)]


def test_settings_reject_missing_password() -> None:
    with pytest.raises(ValueError, match="NEBULA_PASSWORD"):
        Settings.from_env({k: v for k, v in BASE_ENV.items() if k != "NEBULA_PASSWORD"})


@pytest.mark.parametrize("addresses", ["missing-port", "host:0", "host:65536", ":9669"])
def test_settings_reject_invalid_addresses(addresses: str) -> None:
    with pytest.raises(ValueError, match="NEBULA_ADDRESSES"):
        Settings.from_env({**BASE_ENV, "NEBULA_ADDRESSES": addresses})


def test_load_settings_reports_all_missing_required_names_without_values() -> None:
    settings, problem = load_settings({"NEBULA_USERNAME": "visible-user"})

    assert settings is None
    assert problem is not None
    assert problem.variable_names == ("NEBULA_ADDRESSES", "NEBULA_PASSWORD")
    assert "visible-user" not in problem.model_dump_json()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("NEBULA_DEFAULT_SPACE", "bad`space"),
        ("NEBULA_REQUEST_TIMEOUT_MS", "10"),
        ("NEBULA_MAX_ROWS", "many"),
        ("NEBULA_TLS_ENABLED", "maybe"),
    ],
)
def test_load_settings_reports_invalid_variable_names(name: str, value: str) -> None:
    settings, problem = load_settings({**BASE_ENV, name: value})

    assert settings is None
    assert problem is not None
    assert problem.variable_names == (name,)


def test_flat_connection_can_name_its_native_server_environment() -> None:
    assert load_environments({**BASE_ENV, "NEBULA_ENVIRONMENT": "staging"}) == ({}, "staging")
    assert load_environments(BASE_ENV) == ({}, None)


@pytest.mark.parametrize("name", ["", "two words", "x" * 65])
def test_flat_environment_name_is_validated(name: str) -> None:
    with pytest.raises(NebulaMCPError):
        load_environments({**BASE_ENV, "NEBULA_ENVIRONMENT": name})


def test_named_environments_are_independent() -> None:
    environments, active = load_environments({
        "NEBULA_ENVIRONMENTS": json.dumps({
            "dev": {**BASE_ENV, "NEBULA_ALLOW_MUTATIONS": "true"},
            "prod": {**BASE_ENV, "NEBULA_DEFAULT_SPACE": "prod_space"},
        }),
        "NEBULA_ENVIRONMENT": "prod",
    })

    assert active == "prod"
    assert environments["dev"].allow_mutations is True
    assert environments["prod"].allow_mutations is False
    assert environments["prod"].default_space == "prod_space"
