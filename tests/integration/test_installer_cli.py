from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys
from dataclasses import dataclass
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
from types import ModuleType
from typing import cast

import pytest

from tests.fixtures.build_installer_fixture import (
    build_fixture_plugin_archive,
    build_fixture_wheel,
    serve_directory,
    write_checksum_manifest,
)

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class FakeCodexHarness:
    command: tuple[str, ...]
    state_path: Path
    log_path: Path
    config_path: Path

    @classmethod
    def empty(cls, tmp_path: Path) -> FakeCodexHarness:
        return cls._at(tmp_path)

    @classmethod
    def with_managed_registration(
        cls,
        tmp_path: Path,
        *,
        launcher: Path,
        env: dict[str, str],
    ) -> FakeCodexHarness:
        return cls._at(tmp_path, launcher=launcher, env=env)

    @classmethod
    def with_conflicting_registration(cls, tmp_path: Path) -> FakeCodexHarness:
        return cls._at(tmp_path, launcher=tmp_path / "some-other-launcher.py", env={})

    @classmethod
    def _at(
        cls,
        tmp_path: Path,
        *,
        launcher: Path | None = None,
        env: dict[str, str] | None = None,
    ) -> FakeCodexHarness:
        state_path = tmp_path / "fake-codex-state.json"
        log_path = tmp_path / "fake-codex-commands.jsonl"
        config_path = tmp_path / "codex-home" / "config.toml"
        if launcher is not None:
            state_path.write_text(
                json.dumps(
                    {
                        "name": "nebula3",
                        "enabled": True,
                        "disabled_reason": None,
                        "transport": {
                            "type": "stdio",
                            "command": str(Path(sys.executable).resolve()),
                            "args": [str(launcher.resolve())],
                            "env": env,
                            "env_vars": [],
                            "cwd": None,
                        },
                        "enabled_tools": None,
                        "disabled_tools": None,
                        "startup_timeout_sec": None,
                        "tool_timeout_sec": None,
                    }
                ),
                encoding="utf-8",
            )
        command = (
            sys.executable,
            str(ROOT / "tests/fixtures/fake_codex.py"),
            "--state",
            str(state_path),
            "--log",
            str(log_path),
            "--config-file",
            str(config_path),
        )
        return cls(
            command=command,
            state_path=state_path,
            log_path=log_path,
            config_path=config_path,
        )

    @property
    def commands(self) -> list[list[str]]:
        if not self.log_path.exists():
            return []
        return [json.loads(line) for line in self.log_path.read_text().splitlines()]

    @property
    def registration_env(self) -> dict[str, str]:
        if self.config_path.exists() and "# fake-codex-nebula\n" in self.config_path.read_text(
            encoding="utf-8"
        ):
            environment: dict[str, str] = {}
            section = self.config_path.read_text(encoding="utf-8").split(
                "# fake-codex-nebula\n", 1
            )[1]
            for line in section.splitlines():
                if line.startswith("NEBULA_"):
                    key, value = line.split(" = ", 1)
                    environment[key] = json.loads(value)
            return environment
        payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        return cast(dict[str, str], payload["transport"]["env"])

    @property
    def plugin_state_path(self) -> Path:
        return self.state_path.with_name("fake-codex-plugin.json")


def create_managed_install(installer: ModuleType, root: Path) -> object:
    paths = installer.managed_paths(root)
    installer.initialize_managed_root(paths)
    python = installer.python_in_venv(paths.versions / "0.1.0/venv")
    python.parent.mkdir(parents=True)
    python.touch()
    installer.render_launcher(paths)
    installer.atomic_write_state(
        paths, installer.InstallState(schema_version=1, version="0.1.0", python=str(python))
    )
    return paths


def create_legacy_install(installer: ModuleType, root: Path) -> object:
    paths = installer.managed_paths(root)
    venv = paths.versions / "0.1.0/venv"
    subprocess.run(
        [sys.executable, "-m", "venv", str(venv)],
        check=True,
        capture_output=True,
        text=True,
    )
    python = installer.python_in_venv(venv)
    paths.launcher.write_text(installer.LAUNCHER_CONTENT, encoding="utf-8")
    paths.state.write_text(
        json.dumps(
            {"schema_version": 1, "version": "0.1.0", "python": str(python)},
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    return paths


def create_directory_name_surrogate(link: Path, target: Path) -> None:
    if os.name == "nt":
        completed = subprocess.run(
            ["cmd", "/d", "/c", "mklink", "/J", str(link), str(target)],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            pytest.fail(
                "required Windows junction creation failed: "
                f"returncode={completed.returncode}; "
                f"stdout={completed.stdout!r}; stderr={completed.stderr!r}"
            )
        return
    try:
        link.symlink_to(target, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"directory symlink creation is unavailable: {exc}")


def load_installer_template() -> ModuleType:
    path = ROOT / "installer/install.py.in"
    loader = SourceFileLoader("nebula3_mcp_installer_integration", str(path))
    spec = spec_from_loader(loader.name, loader)
    assert spec is not None
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    loader.exec_module(module)
    return module


def test_install_from_controlled_endpoint_switches_state_only_after_smoke(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installer = load_installer_template()
    wheel = build_fixture_wheel(tmp_path / "assets", version="0.1.0")
    write_checksum_manifest(tmp_path / "assets", [wheel])
    monkeypatch.setenv("PIP_NO_INDEX", "1")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home-must-remain-absent"))
    with serve_directory(tmp_path / "assets") as base_url:
        state = installer.install_release(
            installer.managed_paths(tmp_path / "data"),
            version="0.1.0",
            asset_base_url=base_url,
            system_python=Path(sys.executable),
        )

    assert state.version == "0.1.0"
    assert Path(state.python).is_file()
    assert json.loads((tmp_path / "data/state.json").read_text())["version"] == "0.1.0"
    assert not (tmp_path / "codex-home-must-remain-absent").exists()


def test_first_registration_uses_get_then_add(tmp_path: Path) -> None:
    installer = load_installer_template()
    paths = installer.managed_paths(tmp_path / "data")
    fake_codex = FakeCodexHarness.empty(tmp_path)

    result = installer.ensure_registration(
        paths=paths,
        system_python=Path(sys.executable).resolve(),
        codex_command=fake_codex.command,
        replace=False,
        assume_yes=True,
    )

    assert result == "registration created"
    assert fake_codex.commands[0] == ["mcp", "get", "nebula3", "--json"]
    add_command = fake_codex.commands[1]
    assert add_command[:3] == ["mcp", "add", "nebula3"]
    assert add_command[-3:] == ["--", str(Path(sys.executable).resolve()), str(paths.launcher)]
    assert add_command[3:-3] == [
        item
        for key, value in installer.NEW_REGISTRATION_ENVIRONMENT.items()
        for item in ("--env", f"{key}={value}")
    ]


def test_fresh_plugin_registration_adds_marketplace_before_plugin(tmp_path: Path) -> None:
    installer = load_installer_template()
    paths = installer.managed_paths(tmp_path / "data")
    marketplace = paths.root / "marketplace"
    (marketplace / ".agents" / "plugins").mkdir(parents=True)
    (marketplace / ".agents" / "plugins" / "marketplace.json").write_text(
        json.dumps({"name": "nebula3-mcp-local", "plugins": []}),
        encoding="utf-8",
    )
    fake_codex = FakeCodexHarness.empty(tmp_path)

    result = installer.ensure_plugin_registration(
        paths=paths,
        system_python=Path(sys.executable).resolve(),
        codex_command=fake_codex.command,
        migrate=False,
    )

    assert result == "plugin registration created"
    assert fake_codex.commands == [
        ["mcp", "get", "nebula3", "--json"],
        ["plugin", "marketplace", "add", str(marketplace), "--json"],
        ["plugin", "add", "nebula3-mcp@nebula3-mcp-local", "--json"],
    ]
    assert not any(command[:2] == ["mcp", "add"] for command in fake_codex.commands)


def test_existing_managed_mcp_is_preserved_without_explicit_plugin_migration(
    tmp_path: Path,
) -> None:
    installer = load_installer_template()
    paths = installer.managed_paths(tmp_path / "data")
    fake_codex = FakeCodexHarness.with_managed_registration(
        tmp_path,
        launcher=paths.launcher,
        env={"NEBULA_PASSWORD": "desktop-only"},
    )

    result = installer.ensure_plugin_registration(
        paths=paths,
        system_python=Path(sys.executable).resolve(),
        codex_command=fake_codex.command,
        migrate=False,
    )

    assert "--migrate-to-plugin" in result
    assert fake_codex.commands == [["mcp", "get", "nebula3", "--json"]]
    assert fake_codex.registration_env == {"NEBULA_PASSWORD": "desktop-only"}


def test_explicit_plugin_migration_removes_only_managed_mcp_then_adds_plugin(
    tmp_path: Path,
) -> None:
    installer = load_installer_template()
    paths = installer.managed_paths(tmp_path / "data")
    marketplace = paths.root / "marketplace"
    (marketplace / ".agents" / "plugins").mkdir(parents=True)
    (marketplace / ".agents" / "plugins" / "marketplace.json").write_text(
        json.dumps({"name": "nebula3-mcp-local", "plugins": []}),
        encoding="utf-8",
    )
    fake_codex = FakeCodexHarness.with_managed_registration(
        tmp_path,
        launcher=paths.launcher,
        env={},
    )

    result = installer.ensure_plugin_registration(
        paths=paths,
        system_python=Path(sys.executable).resolve(),
        codex_command=fake_codex.command,
        migrate=True,
    )

    assert result == "plugin registration created"
    assert fake_codex.commands == [
        ["mcp", "get", "nebula3", "--json"],
        ["mcp", "remove", "nebula3"],
        ["plugin", "marketplace", "add", str(marketplace), "--json"],
        ["plugin", "add", "nebula3-mcp@nebula3-mcp-local", "--json"],
    ]


def test_plugin_checksum_failure_preserves_active_runtime_and_plugin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installer = load_installer_template()
    paths = create_managed_install(installer, tmp_path / "data")
    original_state = paths.state.read_bytes()
    original_plugin = paths.root / "marketplace" / "plugins" / "nebula3-mcp"
    original_plugin.mkdir(parents=True)
    (original_plugin / "README.md").write_text("original", encoding="utf-8")
    assets = tmp_path / "assets"
    wheel = build_fixture_wheel(assets, version="0.2.0")
    plugin = build_fixture_plugin_archive(assets, version="0.2.0")
    manifest = write_checksum_manifest(assets, [wheel, plugin])
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            hashlib.sha256(plugin.read_bytes()).hexdigest(),
            "0" * 64,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PIP_NO_INDEX", "1")

    with (
        serve_directory(assets) as base_url,
        pytest.raises(installer.InstallerError, match="checksum"),
    ):
        installer.install_plugin_release(
            paths,
            version="0.2.0",
            asset_base_url=base_url,
            system_python=Path(sys.executable),
        )

    assert paths.state.read_bytes() == original_state
    assert (original_plugin / "README.md").read_text(encoding="utf-8") == "original"
    assert not (paths.versions / "0.2.0").exists()


def test_plugin_install_renders_managed_config_and_marketplace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installer = load_installer_template()
    paths = installer.managed_paths(tmp_path / "data")
    assets = tmp_path / "assets"
    wheel = build_fixture_wheel(assets, version="0.2.0")
    plugin = build_fixture_plugin_archive(assets, version="0.2.0")
    write_checksum_manifest(assets, [wheel, plugin])
    monkeypatch.setenv("PIP_NO_INDEX", "1")

    with serve_directory(assets) as base_url:
        state = installer.install_plugin_release(
            paths,
            version="0.2.0",
            asset_base_url=base_url,
            system_python=Path(sys.executable).resolve(),
        )

    assert state.version == "0.2.0"
    assert json.loads(paths.plugin_root.joinpath(".mcp.json").read_text(encoding="utf-8")) == {
        "mcpServers": {
            "nebula3": {
                "command": str(Path(sys.executable).resolve()),
                "args": [str(paths.launcher)],
            }
        }
    }
    marketplace = json.loads(paths.marketplace_manifest.read_text(encoding="utf-8"))
    assert marketplace["name"] == "nebula3-mcp-local"
    assert marketplace["plugins"][0]["source"]["path"] == "./plugins/nebula3-mcp"
    serialized = "\n".join(
        path.read_text(errors="ignore")
        for path in paths.marketplace_root.rglob("*")
        if path.is_file()
    )
    assert "NEBULA_PASSWORD" not in serialized
    if os.name != "nt":
        assert all(
            path.stat().st_mode & 0o777 == (0o700 if path.is_dir() else 0o600)
            for path in [paths.marketplace_root, *paths.marketplace_root.rglob("*")]
        )


def test_plugin_activation_failure_rolls_back_fresh_runtime_and_plugin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installer = load_installer_template()
    paths = installer.managed_paths(tmp_path / "data")
    assets = tmp_path / "assets"
    wheel = build_fixture_wheel(assets, version="0.2.0")
    plugin = build_fixture_plugin_archive(assets, version="0.2.0")
    write_checksum_manifest(assets, [wheel, plugin])
    monkeypatch.setenv("PIP_NO_INDEX", "1")
    secure_tree = installer._secure_plugin_tree

    def fail_after_activation(root: Path) -> None:
        if root == paths.marketplace_root:
            raise installer.InstallerError("plugin", "injected activation failure", "retry")
        secure_tree(root)

    monkeypatch.setattr(installer, "_secure_plugin_tree", fail_after_activation)

    with (
        serve_directory(assets) as base_url,
        pytest.raises(installer.InstallerError, match="injected activation failure"),
    ):
        installer.install_plugin_release(
            paths,
            version="0.2.0",
            asset_base_url=base_url,
            system_python=Path(sys.executable).resolve(),
        )

    assert not (paths.versions / "0.2.0").exists()
    assert not paths.state.exists()
    assert not paths.launcher.exists()
    assert not paths.marketplace_root.exists()


def test_default_install_route_registers_plugin_not_standalone_mcp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installer = load_installer_template()
    paths = installer.managed_paths(tmp_path / "data")
    fake_codex = FakeCodexHarness.empty(tmp_path)

    def install_plugin(*args: object, **kwargs: object) -> object:
        managed = create_managed_install(installer, paths.root)
        installer._write_managed_marketplace(paths.marketplace_root)
        return managed

    monkeypatch.setattr(installer, "default_paths", lambda: paths)
    monkeypatch.setattr(installer, "find_codex", lambda: fake_codex.command)
    monkeypatch.setattr(installer, "install_plugin_release", install_plugin)

    installer.install_and_register(
        argparse.Namespace(
            yes=True,
            replace_registration=False,
            mode="plugin",
            migrate_to_plugin=False,
        )
    )

    assert [command[:2] for command in fake_codex.commands] == [
        ["mcp", "get"],
        ["plugin", "marketplace"],
        ["plugin", "add"],
    ]
    assert not any(command[:2] == ["mcp", "add"] for command in fake_codex.commands)


def test_explicit_mcp_mode_keeps_standalone_registration_route(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installer = load_installer_template()
    paths = installer.managed_paths(tmp_path / "data")
    fake_codex = FakeCodexHarness.empty(tmp_path)
    monkeypatch.setattr(installer, "default_paths", lambda: paths)
    monkeypatch.setattr(installer, "find_codex", lambda: fake_codex.command)
    monkeypatch.setattr(
        installer,
        "install_release",
        lambda *args, **kwargs: create_managed_install(installer, paths.root),
    )

    installer.install_and_register(
        argparse.Namespace(
            yes=True,
            replace_registration=False,
            mode="mcp",
            migrate_to_plugin=False,
        )
    )

    assert fake_codex.commands[-1][:3] == ["mcp", "add", "nebula3"]
    assert not any(command[:2] == ["plugin", "add"] for command in fake_codex.commands)


def test_new_native_registration_prepopulates_connection_fields(tmp_path: Path) -> None:
    installer = load_installer_template()
    paths = installer.managed_paths(tmp_path / "data")
    fake_codex = FakeCodexHarness.empty(tmp_path)

    installer.ensure_registration(
        paths=paths,
        system_python=Path(sys.executable).resolve(),
        codex_command=fake_codex.command,
        replace=False,
        assume_yes=True,
    )

    assert fake_codex.registration_env == {
        "NEBULA_ADDRESSES": "",
        "NEBULA_USERNAME": "",
        "NEBULA_PASSWORD": "",
        "NEBULA_CONNECT_TIMEOUT_MS": "30000",
        "NEBULA_ALLOW_MUTATIONS": "false",
        "NEBULA_ENVIRONMENT": "default",
    }


def test_mcp_mode_adds_native_registration_when_get_resolves_plugin_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installer = load_installer_template()
    paths = installer.managed_paths(tmp_path / "data")
    paths.plugin_root.mkdir(parents=True)
    (paths.plugin_root / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"nebula3": {
            "command": str(Path(sys.executable).resolve()),
            "args": [str(paths.launcher)],
        }}}),
        encoding="utf-8",
    )
    fake_codex = FakeCodexHarness.with_managed_registration(
        tmp_path, launcher=paths.launcher, env={}
    )
    monkeypatch.setenv("CODEX_HOME", str(fake_codex.config_path.parent))

    result = installer.ensure_registration(
        paths=paths,
        system_python=Path(sys.executable).resolve(),
        codex_command=fake_codex.command,
        replace=False,
        assume_yes=True,
    )

    assert result == "registration created"
    assert fake_codex.commands[-1][:3] == ["mcp", "add", "nebula3"]
    assert "[mcp_servers.nebula3]" in fake_codex.config_path.read_text(encoding="utf-8")


def test_mcp_mode_adds_native_registration_after_python_path_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installer = load_installer_template()
    paths = installer.managed_paths(tmp_path / "data")
    paths.plugin_root.mkdir(parents=True)
    old_python = str(Path(sys.executable).resolve())
    (paths.plugin_root / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"nebula3": {
            "command": old_python, "args": [str(paths.launcher)]
        }}}),
        encoding="utf-8",
    )
    fake_codex = FakeCodexHarness.with_managed_registration(
        tmp_path, launcher=paths.launcher, env={}
    )
    monkeypatch.setenv("CODEX_HOME", str(fake_codex.config_path.parent))
    new_python = tmp_path / "new-python"
    new_python.touch()

    result = installer.ensure_registration(
        paths=paths,
        system_python=new_python,
        codex_command=fake_codex.command,
        replace=False,
        assume_yes=True,
    )

    assert result == "registration created"
    assert fake_codex.commands[-1][:3] == ["mcp", "add", "nebula3"]
    assert fake_codex.commands[-1][-3:] == ["--", str(new_python), str(paths.launcher)]


def test_mcp_mode_preserves_native_registration_alongside_plugin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installer = load_installer_template()
    paths = installer.managed_paths(tmp_path / "data")
    paths.plugin_root.mkdir(parents=True)
    (paths.plugin_root / ".mcp.json").write_text("{}", encoding="utf-8")
    fake_codex = FakeCodexHarness.with_managed_registration(
        tmp_path, launcher=paths.launcher, env={"NEBULA_PASSWORD": "preserve-me"}
    )
    fake_codex.config_path.parent.mkdir(parents=True)
    fake_codex.config_path.write_text("[mcp_servers.nebula3]\n", encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(fake_codex.config_path.parent))

    result = installer.ensure_registration(
        paths=paths,
        system_python=Path(sys.executable).resolve(),
        codex_command=fake_codex.command,
        replace=False,
        assume_yes=True,
    )

    assert result == "registration preserved"
    assert not any(command[:2] == ["mcp", "add"] for command in fake_codex.commands)


def test_configure_codex_writes_defaults_without_putting_password_in_argv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installer = load_installer_template()
    paths = create_managed_install(installer, tmp_path / "data")
    fake_codex = FakeCodexHarness.empty(tmp_path)
    fake_codex.config_path.parent.mkdir(parents=True)
    fake_codex.config_path.write_text("model = \"test-model\"\n", encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(fake_codex.config_path.parent))
    password = 'secret "quoted" \\ value\nnext-line'

    result = installer.configure_codex(
        paths=paths,
        codex_command=fake_codex.command,
        addresses="db.example.invalid:9669",
        username="readonly-user",
        password=password,
    )

    assert result == "nebula3 MCP configuration updated; restart Codex to apply it"
    assert fake_codex.registration_env == {
        "NEBULA_ADDRESSES": "db.example.invalid:9669",
        "NEBULA_USERNAME": "readonly-user",
        "NEBULA_PASSWORD": password,
        "NEBULA_CONNECT_TIMEOUT_MS": "30000",
        "NEBULA_ALLOW_MUTATIONS": "false",
    }
    assert "model = \"test-model\"" in fake_codex.config_path.read_text(encoding="utf-8")
    assert all(password not in argument for command in fake_codex.commands for argument in command)


def test_configure_codex_rolls_back_original_config_when_secret_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installer = load_installer_template()
    paths = create_managed_install(installer, tmp_path / "data")
    fake_codex = FakeCodexHarness.empty(tmp_path)
    fake_codex.config_path.parent.mkdir(parents=True)
    original = b'model = "keep-me"\n'
    fake_codex.config_path.write_bytes(original)
    monkeypatch.setenv("CODEX_HOME", str(fake_codex.config_path.parent))
    monkeypatch.setattr(
        installer,
        "replace_codex_secret_placeholder",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("injected")),
    )

    with pytest.raises(installer.InstallerError, match="configuration"):
        installer.configure_codex(
            paths=paths,
            codex_command=fake_codex.command,
            addresses="db.example.invalid:9669",
            username="readonly-user",
            password="must-not-leak",
        )

    assert fake_codex.config_path.read_bytes() == original


def test_configuration_status_is_redacted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    installer = load_installer_template()
    paths = create_managed_install(installer, tmp_path / "data")
    fake_codex = FakeCodexHarness.empty(tmp_path)
    fake_codex.config_path.parent.mkdir(parents=True)
    monkeypatch.setenv("CODEX_HOME", str(fake_codex.config_path.parent))
    installer.configure_codex(
        paths=paths,
        codex_command=fake_codex.command,
        addresses="private-host.invalid:9669",
        username="private-user",
        password="private-password",
    )

    installer.print_configuration_status(fake_codex.command)

    output = capsys.readouterr().out
    assert "NEBULA_ADDRESSES: set" in output
    assert "NEBULA_USERNAME: set" in output
    assert "NEBULA_PASSWORD: set" in output
    assert "NEBULA_CONNECT_TIMEOUT_MS: set" in output
    assert "NEBULA_ALLOW_MUTATIONS: set" in output
    assert "private-host" not in output
    assert "private-user" not in output
    assert "private-password" not in output


def test_clear_configuration_removes_only_standalone_overlay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installer = load_installer_template()
    paths = create_managed_install(installer, tmp_path / "data")
    fake_codex = FakeCodexHarness.empty(tmp_path)
    fake_codex.config_path.parent.mkdir(parents=True)
    fake_codex.config_path.write_text("model = \"keep-me\"\n", encoding="utf-8")
    fake_codex.plugin_state_path.write_text('{"installed":true}', encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(fake_codex.config_path.parent))
    installer.configure_codex(
        paths=paths,
        codex_command=fake_codex.command,
        addresses="db.example.invalid:9669",
        username="readonly-user",
        password="clear-me",
    )

    result = installer.clear_codex_configuration(
        paths=paths,
        codex_command=fake_codex.command,
    )

    assert result == "nebula3 MCP connection configuration cleared"
    assert "mcp_servers.nebula3" not in fake_codex.config_path.read_text(encoding="utf-8")
    assert "model = \"keep-me\"" in fake_codex.config_path.read_text(encoding="utf-8")
    assert fake_codex.plugin_state_path.is_file()


def test_plugin_uninstall_removes_plugin_then_marketplace_before_files(tmp_path: Path) -> None:
    installer = load_installer_template()
    paths = create_managed_install(installer, tmp_path / "data")
    installer._write_managed_marketplace(paths.marketplace_root)
    fake_codex = FakeCodexHarness.empty(tmp_path)

    installer.uninstall(paths, fake_codex.command)

    assert fake_codex.commands == [
        ["plugin", "remove", "nebula3-mcp@nebula3-mcp-local", "--json"],
        ["plugin", "marketplace", "remove", "nebula3-mcp-local"],
    ]
    assert not paths.root.exists()


def test_plugin_uninstall_failure_retains_managed_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installer = load_installer_template()
    paths = create_managed_install(installer, tmp_path / "data")
    installer._write_managed_marketplace(paths.marketplace_root)
    fake_codex = FakeCodexHarness.empty(tmp_path)
    monkeypatch.setenv("FAKE_CODEX_PLUGIN_REMOVE_FAIL", "1")

    with pytest.raises(installer.InstallerError, match="remove"):
        installer.uninstall(paths, fake_codex.command)

    assert paths.root.exists()
    assert paths.ownership_marker.is_file()


def test_marketplace_uninstall_failure_retains_managed_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installer = load_installer_template()
    paths = create_managed_install(installer, tmp_path / "data")
    installer._write_managed_marketplace(paths.marketplace_root)
    fake_codex = FakeCodexHarness.empty(tmp_path)
    monkeypatch.setenv("FAKE_CODEX_MARKETPLACE_REMOVE_FAIL", "1")

    with pytest.raises(installer.InstallerError, match="marketplace"):
        installer.uninstall(paths, fake_codex.command)

    assert fake_codex.commands == [
        ["plugin", "remove", "nebula3-mcp@nebula3-mcp-local", "--json"],
        ["plugin", "marketplace", "remove", "nebula3-mcp-local"],
    ]
    assert paths.root.exists()
    assert paths.ownership_marker.is_file()


def test_plugin_migration_never_removes_conflicting_registration(tmp_path: Path) -> None:
    installer = load_installer_template()
    paths = installer.managed_paths(tmp_path / "data")
    fake_codex = FakeCodexHarness.with_conflicting_registration(tmp_path)

    with pytest.raises(installer.InstallerError, match="another command"):
        installer.ensure_plugin_registration(
            paths=paths,
            system_python=Path(sys.executable).resolve(),
            codex_command=fake_codex.command,
            migrate=True,
        )

    assert fake_codex.commands == [["mcp", "get", "nebula3", "--json"]]


def test_upgrade_does_not_remove_add_or_read_environment(tmp_path: Path) -> None:
    installer = load_installer_template()
    paths = installer.managed_paths(tmp_path / "data")
    fake_codex = FakeCodexHarness.with_managed_registration(
        tmp_path,
        launcher=paths.launcher,
        env={"NEBULA_PASSWORD": "keep-this-value"},
    )

    result = installer.ensure_registration(
        paths=paths,
        system_python=Path(sys.executable).resolve(),
        codex_command=fake_codex.command,
        replace=False,
        assume_yes=True,
    )

    assert result == "registration preserved"
    assert fake_codex.commands == [["mcp", "get", "nebula3", "--json"]]
    assert fake_codex.registration_env == {"NEBULA_PASSWORD": "keep-this-value"}


def test_conflict_is_not_replaced_without_explicit_flag(tmp_path: Path) -> None:
    installer = load_installer_template()
    paths = installer.managed_paths(tmp_path / "data")
    fake_codex = FakeCodexHarness.with_conflicting_registration(tmp_path)

    with pytest.raises(installer.InstallerError) as raised:
        installer.ensure_registration(
            paths=paths,
            system_python=Path(sys.executable),
            codex_command=fake_codex.command,
            replace=False,
            assume_yes=True,
        )

    assert raised.value.stage == "codex-registration"
    assert "--replace-registration" in raised.value.next_step
    assert fake_codex.commands == [["mcp", "get", "nebula3", "--json"]]


def test_explicit_replacement_warns_then_removes_and_adds(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    installer = load_installer_template()
    paths = installer.managed_paths(tmp_path / "data")
    fake_codex = FakeCodexHarness.with_conflicting_registration(tmp_path)

    installer.ensure_registration(
        paths=paths,
        system_python=Path(sys.executable),
        codex_command=fake_codex.command,
        replace=True,
        assume_yes=True,
    )

    assert "environment variables" in capsys.readouterr().out
    assert fake_codex.commands[:2] == [
        ["mcp", "get", "nebula3", "--json"],
        ["mcp", "remove", "nebula3"],
    ]
    add_command = fake_codex.commands[2]
    assert add_command[:3] == ["mcp", "add", "nebula3"]
    assert add_command[-3:] == ["--", sys.executable, str(paths.launcher)]
    assert add_command[3:-3] == [
        item
        for key, value in installer.NEW_REGISTRATION_ENVIRONMENT.items()
        for item in ("--env", f"{key}={value}")
    ]


def test_missing_codex_leaves_manual_registration_command(tmp_path: Path) -> None:
    installer = load_installer_template()
    paths = installer.managed_paths(tmp_path / "data")

    result = installer.ensure_registration(
        paths=paths,
        system_python=Path(sys.executable),
        codex_command=None,
        replace=False,
        assume_yes=True,
    )

    assert result == installer.render_manual_registration_command(
        Path(sys.executable), paths.launcher
    )


def test_upgrade_does_not_prompt_for_first_install_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installer = load_installer_template()
    paths = create_managed_install(installer, tmp_path / "data")
    state = installer.read_state(paths)
    fake_codex = FakeCodexHarness.with_managed_registration(
        tmp_path, launcher=paths.launcher, env={}
    )
    monkeypatch.setattr(installer, "default_paths", lambda: paths)
    monkeypatch.setattr(installer, "find_codex", lambda: fake_codex.command)
    monkeypatch.setattr(installer, "install_release", lambda *args, **kwargs: state)
    monkeypatch.setattr(
        installer,
        "input",
        lambda _: pytest.fail("upgrade must not ask for first-install confirmation"),
        raising=False,
    )

    installer.install_and_register(
        argparse.Namespace(yes=False, replace_registration=False)
    )

    assert fake_codex.commands == [["mcp", "get", "nebula3", "--json"]]


def test_first_install_without_codex_completes_and_prints_manual_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    installer = load_installer_template()
    paths = installer.managed_paths(tmp_path / "data")

    def install_into_managed_root(*args: object, **kwargs: object) -> object:
        return create_managed_install(installer, paths.root)

    monkeypatch.setattr(installer, "default_paths", lambda: paths)
    monkeypatch.setattr(installer, "find_codex", lambda: None)
    monkeypatch.setattr(installer, "install_release", install_into_managed_root)
    monkeypatch.setattr(installer, "input", lambda _: "yes", raising=False)

    installer.install_and_register(
        argparse.Namespace(yes=False, replace_registration=False)
    )

    output = capsys.readouterr().out
    assert f"Target version: {installer.TARGET_VERSION}" in output
    assert f"Data root: {paths.root}" in output
    assert "MCP server: nebula" in output
    assert installer.render_manual_registration_command(
        Path(sys.executable).resolve(), paths.launcher
    ) in output
    assert paths.root.is_dir()


def test_windows_manual_command_still_prints_missing_codex_explanation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    installer = load_installer_template()
    paths = installer.managed_paths(tmp_path / "data")
    monkeypatch.setattr(installer, "default_paths", lambda: paths)
    monkeypatch.setattr(installer, "find_codex", lambda: None)
    monkeypatch.setattr(installer.sys, "platform", "win32")
    monkeypatch.setattr(
        installer,
        "install_release",
        lambda *args, **kwargs: create_managed_install(installer, paths.root),
    )

    installer.install_and_register(
        argparse.Namespace(yes=True, replace_registration=False)
    )

    output = capsys.readouterr().out
    assert "Codex CLI was not found" in output
    assert "& 'codex' 'mcp' 'add' 'nebula3' '--env'" in output


def test_codex_child_process_does_not_receive_nebula_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installer = load_installer_template()
    paths = installer.managed_paths(tmp_path / "data")
    fake_codex = FakeCodexHarness.with_conflicting_registration(tmp_path)
    monkeypatch.setenv("NEBULA_PASSWORD", "must-not-reach-fake-codex")
    monkeypatch.setenv("NEBULA_FAKE_CODEX_REMOVE_FAIL", "1")

    installer.ensure_registration(
        paths=paths,
        system_python=Path(sys.executable),
        codex_command=fake_codex.command,
        replace=True,
        assume_yes=True,
    )

    assert fake_codex.commands[-2] == ["mcp", "remove", "nebula3"]
    add_command = fake_codex.commands[-1]
    assert add_command[:3] == ["mcp", "add", "nebula3"]
    assert add_command[-3:] == ["--", sys.executable, str(paths.launcher)]
    assert "NEBULA_PASSWORD=" in add_command
    assert "NEBULA_PASSWORD=must-not-reach-fake-codex" not in add_command


def test_uninstall_removes_registration_before_managed_root(tmp_path: Path) -> None:
    installer = load_installer_template()
    paths = create_managed_install(installer, tmp_path / "data")
    fake_codex = FakeCodexHarness.with_managed_registration(
        tmp_path, launcher=paths.launcher, env={"NEBULA_HOSTS": "localhost:9669"}
    )

    installer.uninstall(paths, fake_codex.command)

    assert fake_codex.commands == [
        ["mcp", "get", "nebula3", "--json"],
        ["mcp", "remove", "nebula3"],
    ]
    assert not paths.root.exists()


def test_uninstall_retains_files_if_registration_remove_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installer = load_installer_template()
    paths = create_managed_install(installer, tmp_path / "data")
    fake_codex = FakeCodexHarness.with_managed_registration(
        tmp_path, launcher=paths.launcher, env={}
    )
    monkeypatch.setenv("FAKE_CODEX_REMOVE_FAIL", "1")

    with pytest.raises(installer.InstallerError, match="remove"):
        installer.uninstall(paths, fake_codex.command)

    assert paths.root.exists()
    assert fake_codex.state_path.exists()


@pytest.mark.parametrize("registration", ["missing-cli", "conflict"])
def test_uninstall_retains_files_without_safe_registration_removal(
    tmp_path: Path, registration: str
) -> None:
    installer = load_installer_template()
    paths = create_managed_install(installer, tmp_path / "data")
    command = None
    if registration == "conflict":
        command = FakeCodexHarness.with_conflicting_registration(tmp_path).command

    with pytest.raises(installer.InstallerError):
        installer.uninstall(paths, command)

    assert paths.root.exists()


def test_uninstall_is_idempotent_when_root_is_absent(tmp_path: Path) -> None:
    installer = load_installer_template()
    paths = installer.managed_paths(tmp_path / "absent")

    installer.uninstall(paths, None)
    installer.uninstall(paths, None)

    assert not paths.root.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink safety regression")
def test_uninstall_rejects_broken_symlink_instead_of_treating_it_as_absent(
    tmp_path: Path,
) -> None:
    paths_root = tmp_path / "managed-root"
    paths_root.symlink_to(tmp_path / "missing-target", target_is_directory=True)
    installer = load_installer_template()
    paths = installer.ManagedPaths(
        root=paths_root,
        versions=paths_root / "versions",
        staging=paths_root / "staging",
        launcher=paths_root / "launcher.py",
        state=paths_root / "state.json",
        logs=paths_root / "logs",
    )

    with pytest.raises(installer.InstallerError, match="managed installation"):
        installer.uninstall(paths, None)

    assert paths.root.is_symlink()


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink safety regression")
@pytest.mark.parametrize("broken", [False, True])
@pytest.mark.parametrize("operation", ["install", "uninstall"])
def test_production_paths_reject_root_leaf_symlink_without_touching_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    broken: bool,
    operation: str,
) -> None:
    installer = load_installer_template()
    data_home = tmp_path / "data-home"
    data_home.mkdir()
    target = tmp_path / ("missing-target" if broken else "external-target")
    if not broken:
        target.mkdir()
        (target / "keep.txt").write_bytes(b"unrelated content\n")
    logical_root = data_home / "nebula3-mcp"
    try:
        logical_root.symlink_to(target, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    resolved = installer.resolve_data_root(
        platform_name="linux",
        env={"XDG_DATA_HOME": str(data_home)},
        home=tmp_path,
    )
    monkeypatch.setattr(installer, "resolve_data_root", lambda: resolved)
    paths = installer.default_paths()

    assert paths.root == logical_root
    with pytest.raises(installer.InstallerError, match="symbolic link"):
        if operation == "install":
            installer.install_release(
                paths,
                version="0.1.0",
                asset_base_url="https://releases.example/0.1.0",
                system_python=Path(sys.executable),
            )
        else:
            installer.uninstall(paths, None)

    assert logical_root.is_symlink()
    if not broken:
        assert target.is_dir()
        assert (target / "keep.txt").read_bytes() == b"unrelated content\n"


def test_install_refuses_nonempty_root_without_ownership_marker(tmp_path: Path) -> None:
    installer = load_installer_template()
    paths = installer.managed_paths(tmp_path / "data")
    paths.root.mkdir()
    unrelated = paths.root / "keep.txt"
    unrelated.write_bytes(b"not managed by nebula3-mcp\n")

    with pytest.raises(installer.InstallerError, match="ownership"):
        installer.install_release(
            paths,
            version="0.1.0",
            asset_base_url="https://releases.example/0.1.0",
            system_python=Path(sys.executable),
        )

    assert unrelated.read_bytes() == b"not managed by nebula3-mcp\n"


@pytest.mark.skipif(os.name == "nt", reason="POSIX metadata regression")
def test_foreign_markerless_root_is_rejected_without_metadata_or_byte_changes(
    tmp_path: Path,
) -> None:
    installer = load_installer_template()
    paths = installer.managed_paths(tmp_path / "data")
    paths.root.mkdir(mode=0o777)
    paths.root.chmod(0o777)
    foreign = paths.root / "keep.txt"
    foreign.write_bytes(b"foreign bytes must remain exact\n")
    foreign.chmod(0o666)
    timestamp_ns = 1_700_000_000_123_456_789
    os.utime(paths.root, ns=(timestamp_ns, timestamp_ns))
    os.utime(foreign, ns=(timestamp_ns, timestamp_ns))
    before_root = paths.root.lstat()
    before_file = foreign.lstat()
    before_bytes = foreign.read_bytes()

    with pytest.raises(installer.InstallerError, match="ownership"):
        installer.install_release(
            paths,
            version="0.1.0",
            asset_base_url="https://releases.example/0.1.0",
            system_python=Path(sys.executable),
        )

    after_root = paths.root.lstat()
    after_file = foreign.lstat()
    assert foreign.read_bytes() == before_bytes
    assert (
        stat.S_IMODE(after_root.st_mode),
        after_root.st_uid,
        after_root.st_mtime_ns,
    ) == (
        stat.S_IMODE(before_root.st_mode),
        before_root.st_uid,
        before_root.st_mtime_ns,
    )
    assert (
        stat.S_IMODE(after_file.st_mode),
        after_file.st_uid,
        after_file.st_mtime_ns,
    ) == (
        stat.S_IMODE(before_file.st_mode),
        before_file.st_uid,
        before_file.st_mtime_ns,
    )


def test_markerless_legacy_install_migrates_then_upgrades_in_place(tmp_path: Path) -> None:
    installer = load_installer_template()
    paths = create_legacy_install(installer, tmp_path / "data")
    venv = paths.versions / "0.1.0/venv"
    assert (venv / "pyvenv.cfg").is_file()
    if os.name != "nt":
        assert installer.python_in_venv(venv).is_symlink()

    state = installer.install_release(
        paths,
        version="0.1.0",
        asset_base_url="https://releases.example/0.1.0",
        system_python=Path(sys.executable),
    )

    assert state.version == "0.1.0"
    assert paths.ownership_marker.read_text(encoding="utf-8") == (
        installer.OWNERSHIP_MARKER_CONTENT
    )


def test_markerless_legacy_install_migrates_then_uninstalls(tmp_path: Path) -> None:
    installer = load_installer_template()
    paths = create_legacy_install(installer, tmp_path / "data")
    venv = paths.versions / "0.1.0/venv"
    assert (venv / "pyvenv.cfg").is_file()
    if os.name != "nt":
        assert installer.python_in_venv(venv).is_symlink()
    fake_codex = FakeCodexHarness.with_managed_registration(
        tmp_path, launcher=paths.launcher, env={}
    )

    installer.uninstall(paths, fake_codex.command)

    assert not paths.root.exists()
    assert fake_codex.commands == [
        ["mcp", "get", "nebula3", "--json"],
        ["mcp", "remove", "nebula3"],
    ]


@pytest.mark.parametrize("foreign_identity", ["extra-root-entry", "outside-python"])
def test_markerless_legacy_migration_rejects_incomplete_identity_without_writes(
    tmp_path: Path,
    foreign_identity: str,
) -> None:
    installer = load_installer_template()
    paths = create_legacy_install(installer, tmp_path / "data")
    if foreign_identity == "extra-root-entry":
        (paths.root / "foreign.txt").write_bytes(b"foreign root entry\n")
    else:
        payload = json.loads(paths.state.read_text(encoding="utf-8"))
        payload["python"] = str((tmp_path / "outside" / "python").resolve())
        paths.state.write_text(json.dumps(payload), encoding="utf-8")
    before = {
        entry.relative_to(paths.root): entry.read_bytes()
        for entry in paths.root.rglob("*")
        if entry.is_file()
    }

    with pytest.raises(installer.InstallerError):
        installer.initialize_managed_root(paths)

    after = {
        entry.relative_to(paths.root): entry.read_bytes()
        for entry in paths.root.rglob("*")
        if entry.is_file()
    }
    assert after == before
    assert not paths.ownership_marker.exists()


@pytest.mark.parametrize("ancestor", ["version", "venv", "executable-parent"])
def test_legacy_migration_rejects_inactive_version_ancestor_name_surrogate(
    tmp_path: Path,
    ancestor: str,
) -> None:
    installer = load_installer_template()
    paths = create_legacy_install(installer, tmp_path / "data")
    inactive = paths.versions / "0.0.9"
    outside = tmp_path / f"outside-{ancestor}"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_bytes(b"outside ancestor target must remain exact\n")
    if ancestor == "version":
        outside_python = installer.python_in_venv(outside / "venv")
        outside_python.parent.mkdir(parents=True)
        link = inactive
    elif ancestor == "venv":
        outside_python = installer.python_in_venv(outside)
        outside_python.parent.mkdir(parents=True)
        inactive.mkdir()
        link = inactive / "venv"
    else:
        outside_python = outside / installer.python_in_venv(
            inactive / "venv"
        ).name
        (inactive / "venv").mkdir(parents=True)
        link = installer.python_in_venv(inactive / "venv").parent
    outside_python.touch()
    sentinel_before = sentinel.read_bytes()
    target_before = outside.lstat()
    create_directory_name_surrogate(link, outside)

    with pytest.raises(installer.InstallerError, match="trusted managed identity"):
        installer.install_release(
            paths,
            version="0.1.0",
            asset_base_url="https://releases.example/0.1.0",
            system_python=Path(sys.executable),
        )

    target_after = outside.lstat()
    assert sentinel.read_bytes() == sentinel_before
    assert (target_after.st_mode, target_after.st_mtime_ns) == (
        target_before.st_mode,
        target_before.st_mtime_ns,
    )
    assert not paths.ownership_marker.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX broken symlink regression")
def test_legacy_migration_rejects_inactive_broken_python_leaf(tmp_path: Path) -> None:
    installer = load_installer_template()
    paths = create_legacy_install(installer, tmp_path / "data")
    inactive_venv = paths.versions / "0.0.9/venv"
    broken_python = installer.python_in_venv(inactive_venv)
    broken_python.parent.mkdir(parents=True)
    missing_target = tmp_path / "missing-system-python"
    broken_python.symlink_to(missing_target)

    with pytest.raises(installer.InstallerError, match="invalid version entry"):
        installer.install_release(
            paths,
            version="0.1.0",
            asset_base_url="https://releases.example/0.1.0",
            system_python=Path(sys.executable),
        )

    assert not paths.ownership_marker.exists()
    assert not missing_target.exists()


@pytest.mark.parametrize("invalid_leaf", ["missing", "directory"])
def test_legacy_migration_rejects_inactive_non_file_python_leaf(
    tmp_path: Path,
    invalid_leaf: str,
) -> None:
    installer = load_installer_template()
    paths = create_legacy_install(installer, tmp_path / "data")
    inactive_python = installer.python_in_venv(paths.versions / "0.0.9/venv")
    inactive_python.parent.mkdir(parents=True)
    if invalid_leaf == "directory":
        inactive_python.mkdir()

    with pytest.raises(installer.InstallerError, match="invalid version entry"):
        installer.install_release(
            paths,
            version="0.1.0",
            asset_base_url="https://releases.example/0.1.0",
            system_python=Path(sys.executable),
        )

    assert not paths.ownership_marker.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows junction safety regression")
@pytest.mark.parametrize("operation", ["install", "uninstall"])
def test_windows_production_paths_reject_root_junction_without_touching_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    installer = load_installer_template()
    data_home = tmp_path / "Local App Data"
    data_home.mkdir()
    target = tmp_path / "junction target"
    target.mkdir()
    keep = target / "keep.txt"
    keep.write_bytes(b"junction target must remain exact\n")
    logical_root = data_home / "nebula3-mcp"
    create_directory_name_surrogate(logical_root, target)
    resolved = installer.resolve_data_root(
        platform_name="win32",
        env={"LOCALAPPDATA": str(data_home)},
        home=tmp_path,
    )
    monkeypatch.setattr(installer, "resolve_data_root", lambda: resolved)
    paths = installer.default_paths()

    with pytest.raises(installer.InstallerError, match="reparse"):
        if operation == "install":
            installer.install_release(
                paths,
                version="0.1.0",
                asset_base_url="https://releases.example/0.1.0",
                system_python=Path(sys.executable),
            )
        else:
            installer.uninstall(paths, None)

    assert keep.read_bytes() == b"junction target must remain exact\n"
    assert not (target / installer.OWNERSHIP_MARKER).exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink safety regression")
def test_uninstall_rejects_state_symlink_outside_managed_root(tmp_path: Path) -> None:
    installer = load_installer_template()
    paths = installer.managed_paths(tmp_path / "data")
    installer.initialize_managed_root(paths)
    python = paths.versions / "0.1.0/venv/bin/python"
    python.parent.mkdir(parents=True)
    python.touch()
    installer.render_launcher(paths)
    outside_state = tmp_path / "outside-state.json"
    outside_state.write_text(
        json.dumps(
            {"schema_version": 1, "version": "0.1.0", "python": str(python)}
        ),
        encoding="utf-8",
    )
    paths.state.symlink_to(outside_state)
    fake_codex = FakeCodexHarness.empty(tmp_path)

    with pytest.raises(installer.InstallerError, match="managed installation"):
        installer.uninstall(paths, fake_codex.command)

    assert paths.root.is_dir()
    assert outside_state.is_file()
    assert fake_codex.commands == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink safety regression")
def test_uninstall_unlinks_python_leaf_symlink_without_touching_external_target(
    tmp_path: Path,
) -> None:
    installer = load_installer_template()
    paths = installer.managed_paths(tmp_path / "data")
    installer.initialize_managed_root(paths)
    external_python = tmp_path / "external-python"
    external_python.write_bytes(b"external executable marker\n")
    external_python.chmod(0o751)
    expected_content = external_python.read_bytes()
    expected_mode = stat.S_IMODE(external_python.stat().st_mode)
    python = paths.versions / "0.1.0/venv/bin/python"
    python.parent.mkdir(parents=True)
    python.symlink_to(external_python)
    installer.render_launcher(paths)
    installer.atomic_write_state(
        paths,
        installer.InstallState(1, "0.1.0", str(python)),
    )
    fake_codex = FakeCodexHarness.with_managed_registration(
        tmp_path, launcher=paths.launcher, env={}
    )

    installer.uninstall(paths, fake_codex.command)

    assert not paths.root.exists()
    assert external_python.read_bytes() == expected_content
    assert stat.S_IMODE(external_python.stat().st_mode) == expected_mode


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink safety regression")
def test_uninstall_rejects_parent_symlink_escape_and_retains_files(tmp_path: Path) -> None:
    installer = load_installer_template()
    paths = installer.managed_paths(tmp_path / "data")
    installer.initialize_managed_root(paths)
    outside_venv = tmp_path / "outside-venv"
    outside_python = outside_venv / "bin/python"
    outside_python.parent.mkdir(parents=True)
    outside_python.write_bytes(b"outside parent marker\n")
    version = paths.versions / "0.1.0"
    version.mkdir(parents=True)
    (version / "venv").symlink_to(outside_venv, target_is_directory=True)
    installer.render_launcher(paths)
    paths.state.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "version": "0.1.0",
                "python": str(version / "venv/bin/python"),
            }
        ),
        encoding="utf-8",
    )
    fake_codex = FakeCodexHarness.empty(tmp_path)

    with pytest.raises(installer.InstallerError, match="state"):
        installer.uninstall(paths, fake_codex.command)

    assert paths.root.is_dir()
    assert outside_python.read_bytes() == b"outside parent marker\n"
    assert fake_codex.commands == []


def test_uninstall_absent_registration_then_second_run_succeeds(tmp_path: Path) -> None:
    installer = load_installer_template()
    paths = create_managed_install(installer, tmp_path / "data")
    fake_codex = FakeCodexHarness.empty(tmp_path)

    installer.uninstall(paths, fake_codex.command)
    installer.uninstall(paths, fake_codex.command)

    assert fake_codex.commands == [["mcp", "get", "nebula3", "--json"]]
    assert not paths.root.exists()


def test_failed_upgrade_keeps_previous_state(tmp_path: Path) -> None:
    installer = load_installer_template()
    paths = installer.managed_paths(tmp_path / "data")
    installer.initialize_managed_root(paths)
    old_python = installer.python_in_venv(paths.versions / "0.1.0/venv")
    old_python.parent.mkdir(parents=True)
    old_python.touch()
    installer.atomic_write_state(
        paths,
        installer.InstallState(schema_version=1, version="0.1.0", python=str(old_python)),
    )
    assets = tmp_path / "corrupt-assets"
    wheel = build_fixture_wheel(assets, version="0.2.0")
    (assets / "SHA256SUMS").write_text(
        f"{'0' * 64}  {wheel.name}\n",
        encoding="utf-8",
    )

    with (
        serve_directory(assets) as base_url,
        pytest.raises(installer.InstallerError, match="checksum"),
    ):
        installer.install_release(
            paths,
            version="0.2.0",
            asset_base_url=base_url,
            system_python=Path(sys.executable),
        )

    retained = installer.read_state(paths)
    assert retained is not None
    assert retained.version == "0.1.0"
    assert old_python.exists()


def test_reinstall_is_idempotent_and_upgrade_preserves_launcher_and_old_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installer = load_installer_template()
    paths = installer.managed_paths(tmp_path / "data")
    assets = tmp_path / "assets"
    first_wheel = build_fixture_wheel(assets, version="0.1.0")
    write_checksum_manifest(assets, [first_wheel])
    monkeypatch.setenv("PIP_NO_INDEX", "1")
    monkeypatch.setenv("NEBULA_PASSWORD", "fixture-secret-must-not-be-logged")

    with serve_directory(assets) as base_url:
        first = installer.install_release(
            paths,
            version="0.1.0",
            asset_base_url=base_url,
            system_python=Path(sys.executable),
        )
        sentinel = Path(first.python).parent / "idempotency-sentinel"
        sentinel.write_text("preserve", encoding="utf-8")
        launcher_before = paths.launcher.read_bytes()
        launcher_stat_before = paths.launcher.stat()

        repeated = installer.install_release(
            paths,
            version="0.1.0",
            asset_base_url=base_url,
            system_python=Path(sys.executable),
        )

        second_wheel = build_fixture_wheel(assets, version="0.2.0")
        write_checksum_manifest(assets, [first_wheel, second_wheel])
        upgraded = installer.install_release(
            paths,
            version="0.2.0",
            asset_base_url=base_url,
            system_python=Path(sys.executable),
        )

    assert repeated == first
    assert sentinel.read_text(encoding="utf-8") == "preserve"
    assert upgraded.version == "0.2.0"
    assert installer.read_state(paths) == upgraded
    assert (paths.versions / "0.1.0").is_dir()
    assert paths.launcher.read_bytes() == launcher_before
    launcher_stat_after = paths.launcher.stat()
    assert launcher_stat_after.st_ino == launcher_stat_before.st_ino
    assert launcher_stat_after.st_mtime_ns == launcher_stat_before.st_mtime_ns
    logs = "".join(path.read_text(encoding="utf-8") for path in paths.logs.iterdir())
    assert "NEBULA_PASSWORD" not in logs
    assert "fixture-secret-must-not-be-logged" not in logs


def test_launcher_failure_rolls_back_fresh_install_and_allows_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installer = load_installer_template()
    paths = installer.managed_paths(tmp_path / "data")
    wheel = build_fixture_wheel(tmp_path / "assets", version="0.1.0")
    write_checksum_manifest(tmp_path / "assets", [wheel])
    monkeypatch.setenv("PIP_NO_INDEX", "1")
    real_write_launcher = installer.write_launcher_once

    def fail_after_launcher_creation(managed: object) -> None:
        real_write_launcher(managed)
        raise installer.InstallerError("launcher", "injected failure", "retry")

    monkeypatch.setattr(installer, "write_launcher_once", fail_after_launcher_creation)
    with (
        serve_directory(tmp_path / "assets") as base_url,
        pytest.raises(installer.InstallerError, match="injected failure"),
    ):
        installer.install_release(
            paths,
            version="0.1.0",
            asset_base_url=base_url,
            system_python=Path(sys.executable),
        )

    assert not (paths.versions / "0.1.0").exists()
    assert not paths.launcher.exists()
    assert not paths.state.exists()

    monkeypatch.setattr(installer, "write_launcher_once", real_write_launcher)
    with serve_directory(tmp_path / "assets") as base_url:
        retried = installer.install_release(
            paths,
            version="0.1.0",
            asset_base_url=base_url,
            system_python=Path(sys.executable),
        )

    assert installer.read_state(paths) == retried


def test_state_write_failure_rolls_back_upgrade_and_allows_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installer = load_installer_template()
    paths = installer.managed_paths(tmp_path / "data")
    assets = tmp_path / "assets"
    first_wheel = build_fixture_wheel(assets, version="0.1.0")
    write_checksum_manifest(assets, [first_wheel])
    monkeypatch.setenv("PIP_NO_INDEX", "1")

    with serve_directory(assets) as base_url:
        first = installer.install_release(
            paths,
            version="0.1.0",
            asset_base_url=base_url,
            system_python=Path(sys.executable),
        )
        launcher_before = paths.launcher.read_bytes()
        state_before = paths.state.read_bytes()
        second_wheel = build_fixture_wheel(assets, version="0.2.0")
        write_checksum_manifest(assets, [first_wheel, second_wheel])
        real_write_state = installer.atomic_write_state

        def fail_state_write(managed: object, state: object) -> None:
            raise installer.InstallerError("state", "injected failure", "retry")

        monkeypatch.setattr(installer, "atomic_write_state", fail_state_write)
        with pytest.raises(installer.InstallerError, match="injected failure"):
            installer.install_release(
                paths,
                version="0.2.0",
                asset_base_url=base_url,
                system_python=Path(sys.executable),
            )

        assert paths.state.read_bytes() == state_before
        assert installer.read_state(paths) == first
        assert paths.launcher.read_bytes() == launcher_before
        assert not (paths.versions / "0.2.0").exists()

        monkeypatch.setattr(installer, "atomic_write_state", real_write_state)
        retried = installer.install_release(
            paths,
            version="0.2.0",
            asset_base_url=base_url,
            system_python=Path(sys.executable),
        )

    assert installer.read_state(paths) == retried


@pytest.mark.skipif(os.name == "nt", reason="POSIX chmod failure regression")
def test_state_permission_failure_rolls_back_fresh_install_and_allows_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installer = load_installer_template()
    paths = installer.managed_paths(tmp_path / "data")
    wheel = build_fixture_wheel(tmp_path / "assets", version="0.1.0")
    write_checksum_manifest(tmp_path / "assets", [wheel])
    monkeypatch.setenv("PIP_NO_INDEX", "1")
    real_chmod = installer.os.chmod

    def fail_state_permission(path: str | Path, mode: int) -> None:
        if Path(path).name.startswith(".state-"):
            raise PermissionError("injected permission failure")
        real_chmod(path, mode)

    monkeypatch.setattr(installer.os, "chmod", fail_state_permission)
    with (
        serve_directory(tmp_path / "assets") as base_url,
        pytest.raises(installer.InstallerError, match="state"),
    ):
        installer.install_release(
            paths,
            version="0.1.0",
            asset_base_url=base_url,
            system_python=Path(sys.executable),
        )

    assert not (paths.versions / "0.1.0").exists()
    assert not paths.launcher.exists()
    assert not paths.state.exists()

    monkeypatch.setattr(installer.os, "chmod", real_chmod)
    with serve_directory(tmp_path / "assets") as base_url:
        retried = installer.install_release(
            paths,
            version="0.1.0",
            asset_base_url=base_url,
            system_python=Path(sys.executable),
        )

    assert installer.read_state(paths) == retried


@pytest.mark.parametrize("payload", [b"\xff\xfe", b"{not-json"])
def test_cli_reports_invalid_state_without_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    payload: bytes,
) -> None:
    installer = load_installer_template()
    paths = create_managed_install(installer, tmp_path / "data")
    paths.state.write_bytes(payload)
    monkeypatch.setattr(installer, "default_paths", lambda: paths)
    monkeypatch.setattr(installer, "find_codex", lambda: None)

    result = installer.main(["--uninstall"])

    stderr = capsys.readouterr().err
    assert result == 1
    assert stderr.splitlines() == [
        "Stage: state",
        "Error: installation state is invalid",
        "Next step: Run the installer again to repair the installation.",
    ]
    assert "Traceback" not in stderr
    assert str(tmp_path) not in stderr


def test_cli_reports_unwritable_root_without_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    installer = load_installer_template()
    paths = installer.managed_paths(tmp_path / "data")
    real_mkdir = installer.Path.mkdir

    def deny_root(path: Path, *args: object, **kwargs: object) -> None:
        if path == paths.root:
            raise PermissionError("private path and secret must stay hidden")
        real_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(installer.Path, "mkdir", deny_root)
    monkeypatch.setattr(installer, "default_paths", lambda: paths)
    monkeypatch.setattr(installer, "TARGET_VERSION", "0.1.0")

    result = installer.main(["--yes"])

    stderr = capsys.readouterr().err
    assert result == 1
    assert stderr.startswith("Stage: install\nError: could not create")
    assert "Next step:" in stderr
    assert "Traceback" not in stderr
    assert "private path" not in stderr
    assert str(tmp_path) not in stderr


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink safety regression")
def test_cli_reports_circular_parent_symlink_without_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    installer = load_installer_template()
    first = tmp_path / "first"
    second = tmp_path / "second"
    try:
        first.symlink_to(second, target_is_directory=True)
        second.symlink_to(first, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")
    monkeypatch.setattr(
        installer,
        "resolve_data_root",
        lambda: installer.managed_paths(first / "nebula3-mcp").root,
    )

    result = installer.main(["--yes"])

    stderr = capsys.readouterr().err
    assert result == 1
    assert stderr.startswith("Stage: paths\nError:")
    assert "Next step:" in stderr
    assert "Traceback" not in stderr
    assert str(tmp_path) not in stderr


def test_non_utf8_checksum_manifest_is_an_installer_error(tmp_path: Path) -> None:
    installer = load_installer_template()
    assets = tmp_path / "assets"
    build_fixture_wheel(assets, version="0.1.0")
    (assets / "SHA256SUMS").write_bytes(b"\xff\xfe")

    with (
        serve_directory(assets) as base_url,
        pytest.raises(installer.InstallerError) as raised,
    ):
        installer.install_release(
            installer.managed_paths(tmp_path / "data"),
            version="0.1.0",
            asset_base_url=base_url,
            system_python=Path(sys.executable),
        )

    assert raised.value.stage == "checksum"


def test_install_local_assets_without_release_download(tmp_path, monkeypatch):
    installer = load_installer_template()
    assets = tmp_path / "assets"
    wheel = build_fixture_wheel(assets, version="0.3.0")
    plugin = build_fixture_plugin_archive(assets, version="0.3.0")
    write_checksum_manifest(assets, [wheel, plugin])
    monkeypatch.setenv("PIP_NO_INDEX", "1")
    def reject_download(*args, **kwargs):
        raise AssertionError("Local asset installation must not download release assets")
    monkeypatch.setattr(installer, "download_file", reject_download)
    paths = installer.managed_paths(tmp_path / "data")
    state = installer.install_plugin_release(
        paths, version="0.3.0", asset_base_url="https://example.invalid/releases",
        system_python=Path(sys.executable), local_assets=assets,
    )
    assert state.version == "0.3.0"
    assert paths.plugin_root.is_dir()
    assert installer.build_parser().parse_args(["--assets", str(assets)]).assets == assets


def test_local_asset_checksum_failure_does_not_activate_runtime(tmp_path):
    installer = load_installer_template()
    assets = tmp_path / "assets"
    wheel = build_fixture_wheel(assets, version="0.3.0")
    write_checksum_manifest(assets, [wheel])
    wheel.write_bytes(b"modified after checksum")
    paths = installer.managed_paths(tmp_path / "data")
    with pytest.raises(installer.InstallerError, match="checksum"):
        installer.install_release(
            paths, version="0.3.0", asset_base_url="https://example.invalid/releases",
            system_python=Path(sys.executable), local_assets=assets,
        )
    assert not paths.state.exists()
    assert not (paths.versions / "0.3.0").exists()
