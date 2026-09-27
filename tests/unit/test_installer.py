from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
from email.message import Message
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
from types import ModuleType
from typing import Any
from zipfile import ZipFile, ZipInfo

import pytest

ROOT = Path(__file__).resolve().parents[2]

MANAGED_REGISTRATION = {
    "name": "nebula3",
    "enabled": True,
    "disabled_reason": None,
    "transport": {
        "type": "stdio",
        "command": str(Path(sys.executable).resolve()),
        "args": [str(Path("different-launcher.py").resolve())],
        "env": {"NEBULA_PASSWORD": "local-only"},
        "env_vars": [],
        "cwd": None,
    },
    "enabled_tools": None,
    "disabled_tools": None,
    "startup_timeout_sec": None,
    "tool_timeout_sec": None,
}


class FakeResponse:
    def __init__(
        self,
        *,
        url: str,
        status: int = 200,
        content_length: str | None = None,
        body: bytes = b"",
    ) -> None:
        self._url = url
        self._status = status
        self._body = io.BytesIO(body)
        self.headers = Message()
        if content_length is not None:
            self.headers["Content-Length"] = content_length

    def __enter__(self) -> Any:
        return self

    def __exit__(self, *_: object) -> None:
        self._body.close()

    def getcode(self) -> int:
        return self._status

    def geturl(self) -> str:
        return self._url

    def read(self, size: int = -1) -> bytes:
        return self._body.read(size)


class FakeOpener:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response

    def open(self, _: str, *, timeout: int) -> FakeResponse:
        assert timeout == 30
        return self.response


def install_fake_response(
    monkeypatch: pytest.MonkeyPatch, installer: ModuleType, response: FakeResponse
) -> None:
    monkeypatch.setattr(
        installer.urllib.request,
        "build_opener",
        lambda *_: FakeOpener(response),
    )


def load_installer_template() -> ModuleType:
    path = ROOT / "installer/install.py.in"
    loader = SourceFileLoader("nebula3_mcp_installer_template", str(path))
    spec = spec_from_loader(loader.name, loader)
    assert spec is not None
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    loader.exec_module(module)
    return module


def test_platform_data_roots_are_user_scoped(tmp_path: Path) -> None:
    installer = load_installer_template()

    assert installer.resolve_data_root(
        platform_name="darwin", env={}, home=tmp_path
    ) == tmp_path / "Library/Application Support/nebula3-mcp"
    assert installer.resolve_data_root(
        platform_name="linux", env={}, home=tmp_path
    ) == tmp_path / ".local/share/nebula3-mcp"
    assert installer.resolve_data_root(
        platform_name="win32",
        env={"LOCALAPPDATA": str(tmp_path / "Local")},
        home=tmp_path,
    ) == tmp_path / "Local/nebula3-mcp"


def test_windows_data_root_requires_absolute_localappdata(tmp_path: Path) -> None:
    installer = load_installer_template()

    with pytest.raises(installer.InstallerError, match="LOCALAPPDATA"):
        installer.resolve_data_root(
            platform_name="win32",
            env={"LOCALAPPDATA": "relative/local-app-data"},
            home=tmp_path,
        )


def test_registration_is_managed_by_launcher_not_environment(tmp_path: Path) -> None:
    installer = load_installer_template()
    launcher = tmp_path / "launcher.py"
    system_python = Path(sys.executable).resolve()
    payload = copy.deepcopy(MANAGED_REGISTRATION)
    payload["transport"]["command"] = str(system_python)
    payload["transport"]["args"] = [str(launcher)]

    assert installer.classify_registration(payload, launcher, system_python) == "managed"


@pytest.mark.parametrize("command", [None, "python3", "wrong-absolute"])
def test_registration_conflicts_on_missing_nonabsolute_or_wrong_command(
    tmp_path: Path,
    command: str | None,
) -> None:
    installer = load_installer_template()
    launcher = tmp_path / "launcher.py"
    expected_python = Path(sys.executable).resolve()
    payload = copy.deepcopy(MANAGED_REGISTRATION)
    if command == "wrong-absolute":
        command = str((tmp_path / "wrong python").resolve())
    payload["transport"]["command"] = command
    payload["transport"]["args"] = [str(launcher)]

    assert (
        installer.classify_registration(payload, launcher, expected_python)
        == "conflict"
    )


def test_registration_conflicts_when_expected_python_disappears(tmp_path: Path) -> None:
    installer = load_installer_template()
    launcher = tmp_path / "launcher.py"
    expected_python = tmp_path / "system python"
    expected_python.write_bytes(b"python marker")
    payload = copy.deepcopy(MANAGED_REGISTRATION)
    payload["transport"]["command"] = str(expected_python)
    payload["transport"]["args"] = [str(launcher)]
    expected_python.unlink()

    assert (
        installer.classify_registration(payload, launcher, expected_python)
        == "conflict"
    )


def test_posix_manual_registration_command_round_trips_argv(tmp_path: Path) -> None:
    installer = load_installer_template()
    system_python = tmp_path / "Python's Folder/python 3"
    launcher = tmp_path / "Application Support/launcher's copy.py"

    command = installer.render_manual_registration_command(
        system_python,
        launcher,
        platform_name="darwin",
    )

    assert shlex.split(command) == [
        "codex",
        "mcp",
        "add",
        "nebula3",
        *[
            item
            for key, value in installer.NEW_REGISTRATION_ENVIRONMENT.items()
            for item in ("--env", f"{key}={value}")
        ],
        "--",
        str(system_python),
        str(launcher),
    ]


def test_windows_manual_registration_command_is_executable_powershell(
    tmp_path: Path,
) -> None:
    installer = load_installer_template()
    system_python = tmp_path / "Python's Home" / "python.exe"
    launcher = tmp_path / "App Data" / "launcher's copy.py"

    command = installer.render_manual_registration_command(
        system_python,
        launcher,
        platform_name="win32",
    )

    quote = lambda value: "'" + value.replace("'", "''") + "'"
    assert command == "& " + " ".join(
        quote(value)
        for value in (
            "codex",
            "mcp",
            "add",
            "nebula3",
            *[
                item
                for key, value in installer.NEW_REGISTRATION_ENVIRONMENT.items()
                for item in ("--env", f"{key}={value}")
            ],
            "--",
            str(system_python),
            str(launcher),
        )
    )


@pytest.mark.skipif(
    os.name != "nt" or shutil.which("pwsh") is None,
    reason="actual PowerShell argv evidence requires Windows with pwsh",
)
def test_windows_manual_registration_command_round_trips_seven_argv_with_pwsh(
    tmp_path: Path,
) -> None:
    installer = load_installer_template()
    system_python = tmp_path / "Python's Home" / "python.exe"
    launcher = tmp_path / "App Data" / "launcher's copy.py"
    command = installer.render_manual_registration_command(
        system_python,
        launcher,
        platform_name="win32",
    )
    script = (
        "function codex { ConvertTo-Json -Compress -InputObject "
        "(@('codex') + @($args)) }; "
        + command
    )

    completed = subprocess.run(
        ["pwsh", "-NoProfile", "-NonInteractive", "-Command", script],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout) == [
        "codex",
        "mcp",
        "add",
        "nebula3",
        *[
            item
            for key, value in installer.NEW_REGISTRATION_ENVIRONMENT.items()
            for item in ("--env", f"{key}={value}")
        ],
        "--",
        str(system_python),
        str(launcher),
    ]


def test_windows_reparse_attribute_is_rejected_without_pathlib_is_junction() -> None:
    installer = load_installer_template()

    assert installer._is_windows_reparse_point(0x400, platform_name="nt")


@pytest.mark.parametrize(
    "transport",
    [
        {"type": "stdio", "args": ["/another/launcher.py"]},
        {"type": "stdio", "args": ["/launcher.py", "--extra"]},
        {"type": "http", "url": "https://example.invalid/mcp"},
        "not-a-transport",
    ],
)
def test_registration_conflict_requires_exact_stdio_launcher(
    tmp_path: Path, transport: object
) -> None:
    installer = load_installer_template()

    assert installer.classify_registration(
        {"name": "nebula3", "transport": transport},
        tmp_path / "launcher.py",
        Path(sys.executable).resolve(),
    ) == "conflict"


def test_parser_exposes_only_supported_installer_options() -> None:
    installer = load_installer_template()

    assert set(installer.build_parser()._option_string_actions) == {
        "-h",
        "--help",
        "--version",
        "--uninstall",
        "--yes",
        "--replace-registration",
        "--mode",
        "--migrate-to-plugin",
        "--configure",
        "--config-status",
        "--clear-config",
        "--assets",
    }
    assert installer.build_parser().parse_args([]).mode == "plugin"
    assert installer.build_parser().parse_args(["--mode", "mcp"]).mode == "mcp"


def test_extract_and_render_managed_plugin_use_exact_launcher_paths(tmp_path: Path) -> None:
    installer = load_installer_template()
    archive = tmp_path / "plugin.zip"
    with ZipFile(archive, "w") as output:
        output.writestr(
            ".codex-plugin/plugin.json",
            json.dumps(
                {
                    "name": "nebula3-mcp",
                    "version": "0.2.0",
                    "mcpServers": "./.mcp.json",
                }
            ),
        )
        output.writestr(
            ".mcp.json",
            json.dumps(
                {"mcpServers": {"nebula3": {"command": "nebula3-mcp", "args": []}}}
            ),
        )
        output.writestr("README.md", "# Plugin\n")

    plugin = installer.extract_plugin_archive(archive, tmp_path / "staging")
    system_python = Path(sys.executable).resolve()
    launcher = (tmp_path / "managed" / "launcher.py").resolve()
    installer.render_managed_mcp_config(plugin, system_python, launcher)

    assert json.loads((plugin / ".mcp.json").read_text(encoding="utf-8")) == {
        "mcpServers": {
            "nebula3": {
                "command": str(system_python),
                "args": [str(launcher)],
            }
        }
    }
    serialized = "\n".join(
        path.read_text(errors="ignore") for path in plugin.rglob("*") if path.is_file()
    )
    assert "NEBULA_PASSWORD" not in serialized


@pytest.mark.parametrize(
    ("member", "symlink"),
    [
        ("../escape", False),
        ("/absolute", False),
        ("unexpected.txt", False),
        ("README.md", True),
    ],
)
def test_extract_plugin_rejects_unsafe_members_before_writing(
    tmp_path: Path,
    member: str,
    symlink: bool,
) -> None:
    installer = load_installer_template()
    archive = tmp_path / "unsafe.zip"
    with ZipFile(archive, "w") as output:
        info = ZipInfo(member)
        if symlink:
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
        output.writestr(info, "payload")

    staging = tmp_path / "staging"
    with pytest.raises(installer.InstallerError, match="plugin archive"):
        installer.extract_plugin_archive(archive, staging)

    assert not staging.exists() or list(staging.rglob("*")) == []


def test_report_error_emits_only_actionable_fields(capsys: pytest.CaptureFixture[str]) -> None:
    installer = load_installer_template()

    result = installer.report_error(
        installer.InstallerError("download", "could not download", "try again")
    )

    assert result == 1
    assert capsys.readouterr().err == (
        "Stage: download\nError: could not download\nNext step: try again\n"
    )


def test_cli_catches_unexpected_exception_without_sensitive_details(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    installer = load_installer_template()
    monkeypatch.setattr(
        installer,
        "install_and_register",
        lambda _: (_ for _ in ()).throw(
            RuntimeError("secret-token at /private/sensitive/location")
        ),
    )

    result = installer.main(["--yes"])

    stderr = capsys.readouterr().err
    assert result == 1
    assert stderr == (
        "Stage: unexpected\n"
        "Error: the installer could not complete safely\n"
        "Next step: Check the managed data directory and retry.\n"
    )
    assert "Traceback" not in stderr
    assert "secret-token" not in stderr
    assert "/private/sensitive/location" not in stderr


@pytest.mark.parametrize(
    ("manifest", "expected"),
    [
        ("a" * 64 + "  ../wheel.whl\n", "checksum manifest"),
        ("A" * 64 + "  wheel.whl\n", "checksum manifest"),
        ("a" * 64 + "  wheel.whl\n" + "b" * 64 + "  wheel.whl\n", "checksum manifest"),
    ],
)
def test_checksum_parser_rejects_unsafe_or_duplicate_entries(
    manifest: str, expected: str
) -> None:
    installer = load_installer_template()

    with pytest.raises(installer.InstallerError, match=expected):
        installer.parse_checksums(manifest)


def test_checksum_parser_and_verifier_accept_matching_file(tmp_path: Path) -> None:
    installer = load_installer_template()
    asset = tmp_path / "nebula3_mcp-0.1.0-py3-none-any.whl"
    asset.write_bytes(b"release asset")
    digest = hashlib.sha256(asset.read_bytes()).hexdigest()

    checksums = installer.parse_checksums(f"{digest}  {asset.name}\n")

    assert checksums == {asset.name: digest}
    installer.verify_sha256(asset, digest)
    with pytest.raises(installer.InstallerError, match="checksum"):
        installer.verify_sha256(asset, "0" * 64)


def test_download_rejects_plain_http_without_explicit_test_allowance(tmp_path: Path) -> None:
    installer = load_installer_template()

    with pytest.raises(installer.InstallerError, match="HTTPS"):
        installer.download_file("http://127.0.0.1:8765/wheel.whl", tmp_path / "asset")


def test_download_directory_permission_error_is_an_installer_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installer = load_installer_template()
    destination = tmp_path / "blocked" / "asset"
    real_mkdir = installer.Path.mkdir

    def deny_destination(path: Path, *args: object, **kwargs: object) -> None:
        if path == destination.parent:
            raise PermissionError("private path must not escape")
        real_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(installer.Path, "mkdir", deny_destination)

    with pytest.raises(installer.InstallerError) as raised:
        installer.download_file("https://releases.example/asset", destination)

    assert raised.value.stage == "download"
    assert "private path" not in raised.value.message


def test_download_allows_only_exact_loopback_http_for_controlled_tests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installer = load_installer_template()
    response = FakeResponse(
        url="http://127.0.0.1:8765/wheel.whl",
        content_length="5",
        body=b"wheel",
    )
    install_fake_response(monkeypatch, installer, response)

    installer.download_file(
        "http://127.0.0.1:8765/wheel.whl", tmp_path / "asset", allow_http=True
    )

    assert (tmp_path / "asset").read_bytes() == b"wheel"
    with pytest.raises(installer.InstallerError, match="HTTPS"):
        installer.download_file("http://127.0.0.2/wheel.whl", tmp_path / "unsafe", allow_http=True)


def test_redirect_handler_rejects_downgrade_in_any_hop() -> None:
    installer = load_installer_template()
    handler = installer.ValidatingRedirectHandler(allow_http=False)
    request = installer.urllib.request.Request("https://releases.example/asset")

    redirected = handler.redirect_request(
        request,
        None,
        302,
        "Found",
        {},
        "https://mirror.example/intermediate",
    )

    assert redirected is not None
    with pytest.raises(installer.InstallerError, match="HTTPS"):
        handler.redirect_request(
            redirected,
            None,
            302,
            "Found",
            {},
            "http://downloads.example/asset",
        )


@pytest.mark.parametrize(
    ("status", "content_length", "body", "message"),
    [
        (404, "9", b"not found", "unsuccessful"),
        (200, None, b"asset", "Content-Length"),
        (200, "unknown", b"asset", "invalid Content-Length"),
        (200, "3", b"asset", "did not match"),
        (200, str(512 * 1024 * 1024 + 1), b"", "maximum allowed size"),
    ],
)
def test_download_rejects_invalid_response_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    content_length: str | None,
    body: bytes,
    message: str,
) -> None:
    installer = load_installer_template()
    response = FakeResponse(
        url="https://releases.example/asset",
        status=status,
        content_length=content_length,
        body=body,
    )
    install_fake_response(monkeypatch, installer, response)

    with pytest.raises(installer.InstallerError, match=message):
        installer.download_file("https://releases.example/asset", tmp_path / "asset")

    assert not (tmp_path / "asset").exists()


def test_state_contains_only_version_and_python(tmp_path: Path) -> None:
    installer = load_installer_template()
    paths = installer.managed_paths(tmp_path / "nebula3-mcp")
    python = installer.python_in_venv(paths.versions / "0.1.0/venv")
    python.parent.mkdir(parents=True)
    python.touch()
    state = installer.InstallState(
        schema_version=1,
        version="0.1.0",
        python=str(python),
    )

    installer.atomic_write_state(paths, state)

    assert installer.read_state(paths) == state
    assert set(json.loads(paths.state.read_text())) == {
        "schema_version",
        "version",
        "python",
    }
    if os.name != "nt":
        assert paths.state.stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(os.name == "nt", reason="POSIX ownership and modes only")
def test_managed_root_and_files_are_private_with_permissive_umask(
    tmp_path: Path,
) -> None:
    installer = load_installer_template()
    paths = installer.managed_paths(tmp_path / "nebula3-mcp")
    paths.root.mkdir(mode=0o777)
    paths.root.chmod(0o777)
    previous_umask = os.umask(0)
    try:
        installer.initialize_managed_root(paths)
        paths.logs.mkdir(mode=0o777)
        paths.logs.chmod(0o777)
        log_path = installer.new_log_path(paths)
        installer.render_launcher(paths)
        python = paths.versions / "0.1.0/venv/bin/python"
        python.parent.mkdir(parents=True)
        python.touch()
        installer.atomic_write_state(
            paths,
            installer.InstallState(1, "0.1.0", str(python)),
        )
    finally:
        os.umask(previous_umask)

    for directory in (paths.root, paths.logs):
        metadata = directory.stat()
        assert stat.S_IMODE(metadata.st_mode) == 0o700
        assert metadata.st_uid == os.getuid()
    for managed_file in (
        paths.ownership_marker,
        paths.state,
        paths.launcher,
        log_path,
    ):
        metadata = managed_file.stat()
        assert stat.S_IMODE(metadata.st_mode) == 0o600
        assert metadata.st_uid == os.getuid()


def test_state_rejects_python_outside_managed_root(tmp_path: Path) -> None:
    installer = load_installer_template()
    paths = installer.managed_paths(tmp_path / "nebula3-mcp")
    paths.root.mkdir()
    outside_python = (tmp_path / "outside" / "python").resolve()
    paths.state.write_text(
        json.dumps(
            {"schema_version": 1, "version": "0.1.0", "python": str(outside_python)}
        ),
        encoding="utf-8",
    )

    with pytest.raises(installer.InstallerError, match="state"):
        installer.read_state(paths)


def test_state_write_rejects_destination_outside_managed_root(tmp_path: Path) -> None:
    installer = load_installer_template()
    root = tmp_path / "nebula3-mcp"
    outside = tmp_path / "outside-state.json"
    paths = installer.ManagedPaths(
        root=root,
        versions=root / "versions",
        staging=root / "staging",
        launcher=root / "launcher.py",
        state=outside,
        logs=root / "logs",
    )
    python = installer.python_in_venv(root / "versions/0.1.0/venv")
    state = installer.InstallState(1, "0.1.0", str(python))

    with pytest.raises(installer.InstallerError) as raised:
        installer.atomic_write_state(paths, state)

    assert raised.value.stage == "state"
    assert not outside.exists()


def test_rendered_launcher_validates_state_before_exec(tmp_path: Path) -> None:
    installer = load_installer_template()
    paths = installer.managed_paths(tmp_path / "nebula3-mcp")

    installer.render_launcher(paths)

    assert paths.launcher.read_text(encoding="utf-8") == """from __future__ import annotations

import json
import os
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parent
    state = json.loads((root / \"state.json\").read_text(encoding=\"utf-8\"))
    python = Path(state[\"python\"])
    managed_python = python.parent.resolve() / python.name
    if not managed_python.is_relative_to(root / \"versions\") or not python.is_file():
        raise SystemExit(\"nebula3-mcp installation state is invalid\")
    os.execv(str(python), [str(python), \"-m\", \"nebula3_mcp\"])


if __name__ == \"__main__\":
    main()
"""


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink safety regression")
def test_launcher_render_replaces_symlink_without_touching_its_target(tmp_path: Path) -> None:
    installer = load_installer_template()
    paths = installer.managed_paths(tmp_path / "nebula3-mcp")
    paths.root.mkdir()
    outside = tmp_path / "outside-launcher.py"
    outside.write_text("do not overwrite", encoding="utf-8")
    paths.launcher.symlink_to(outside)

    installer.render_launcher(paths)

    assert outside.read_text(encoding="utf-8") == "do not overwrite"
    assert not paths.launcher.is_symlink()


def test_release_asset_url_uses_versioned_github_https_path() -> None:
    installer = load_installer_template()

    assert (
        installer.release_asset_url("vesoft-inc/nebula3-mcp", "0.2.0")
        == "https://github.com/vesoft-inc/nebula3-mcp/releases/download/v0.2.0"
    )


def test_run_checked_logs_output_and_hides_failure_details(tmp_path: Path) -> None:
    installer = load_installer_template()
    log_path = tmp_path / "install.log"

    output = installer.run_checked(
        "success",
        [sys.executable, "-c", "print('safe output')"],
        log_path=log_path,
    )

    assert output == "safe output\n"
    assert "safe output" in log_path.read_text(encoding="utf-8")

    with pytest.raises(installer.InstallerError) as raised:
        installer.run_checked(
            "smoke",
            [sys.executable, "-c", "import sys; print('sensitive'); sys.exit(7)"],
            log_path=log_path,
        )

    assert raised.value.stage == "smoke"
    assert "sensitive" not in raised.value.message
    assert "sensitive" in log_path.read_text(encoding="utf-8")


def test_run_checked_removes_nebula_secrets_but_preserves_required_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installer = load_installer_template()
    monkeypatch.setenv("NEBULA_PASSWORD", "database-secret")
    monkeypatch.setenv("NEBULA_SESSION_TOKEN", "session-secret")
    monkeypatch.setenv("PIP_NO_INDEX", "preserved-pip-setting")

    output = installer.run_checked(
        "environment",
        [
            sys.executable,
            "-c",
            (
                "import json, os; print(json.dumps({"
                "'password': os.getenv('NEBULA_PASSWORD'), "
                "'token': os.getenv('NEBULA_SESSION_TOKEN'), "
                "'pip': os.getenv('PIP_NO_INDEX'), "
                "'path': bool(os.getenv('PATH'))}))"
            ),
        ],
        log_path=tmp_path / "install.log",
    )

    assert json.loads(output) == {
        "password": None,
        "token": None,
        "pip": "preserved-pip-setting",
        "path": True,
    }


def test_install_refuses_unmanaged_existing_version_directory(tmp_path: Path) -> None:
    installer = load_installer_template()
    paths = installer.managed_paths(tmp_path / "data")
    existing = paths.versions / "0.2.0"
    existing.mkdir(parents=True)

    with pytest.raises(installer.InstallerError) as raised:
        installer.install_release(
            paths,
            version="0.2.0",
            asset_base_url="https://releases.example/0.2.0",
            system_python=Path(sys.executable),
        )

    assert raised.value.stage == "install"
    assert "ownership marker" in raised.value.message
    assert existing.is_dir()


@pytest.mark.parametrize(
    ("platform_name", "relative_python"),
    [("nt", "Scripts/python.exe"), ("posix", "bin/python")],
)
def test_python_in_venv_matches_platform_layout(
    tmp_path: Path, platform_name: str, relative_python: str
) -> None:
    installer = load_installer_template()

    assert installer.python_in_venv(
        tmp_path / "venv", platform_name=platform_name
    ) == tmp_path / "venv" / relative_python


def test_installed_python_requires_exact_lexical_venv_path(tmp_path: Path) -> None:
    installer = load_installer_template()
    paths = installer.managed_paths(tmp_path / "data")
    python = installer.python_in_venv(paths.versions / "0.1.0/venv")
    python.parent.mkdir(parents=True)
    python.touch()
    non_exact = python.parent / ".." / "bin" / "python"

    with pytest.raises(installer.InstallerError, match="selected version"):
        installer.validate_installed_python(
            paths,
            installer.InstallState(1, "0.1.0", str(non_exact)),
        )


def test_write_launcher_once_rejects_existing_file_outside_managed_root(
    tmp_path: Path,
) -> None:
    installer = load_installer_template()
    root = tmp_path / "data"
    outside = tmp_path / "outside-launcher.py"
    outside.write_text("do not use", encoding="utf-8")
    paths = installer.ManagedPaths(
        root=root,
        versions=root / "versions",
        staging=root / "staging",
        launcher=outside,
        state=root / "state.json",
        logs=root / "logs",
    )

    with pytest.raises(installer.InstallerError) as raised:
        installer.write_launcher_once(paths)

    assert raised.value.stage == "launcher"
    assert outside.read_text(encoding="utf-8") == "do not use"


def test_write_launcher_once_rejects_foreign_regular_file(tmp_path: Path) -> None:
    installer = load_installer_template()
    paths = installer.managed_paths(tmp_path / "data")
    paths.root.mkdir()
    paths.launcher.write_text("print('foreign launcher')\n", encoding="utf-8")

    with pytest.raises(installer.InstallerError, match="not managed"):
        installer.write_launcher_once(paths)

    assert paths.launcher.read_text(encoding="utf-8") == "print('foreign launcher')\n"


@pytest.mark.skipif(os.name == "nt", reason="POSIX chmod failure regression")
def test_state_permission_failure_before_replace_keeps_previous_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installer = load_installer_template()
    paths = installer.managed_paths(tmp_path / "data")
    old_python = paths.versions / "0.1.0/venv/bin/python"
    old_python.parent.mkdir(parents=True)
    old_python.touch()
    old_state = installer.InstallState(1, "0.1.0", str(old_python))
    installer.atomic_write_state(paths, old_state)
    original_state = paths.state.read_bytes()
    real_chmod = installer.os.chmod

    def fail_temporary_permission(path: str | Path, mode: int) -> None:
        if Path(path).name.startswith(".state-"):
            raise PermissionError("injected permission failure")
        real_chmod(path, mode)

    monkeypatch.setattr(installer.os, "chmod", fail_temporary_permission)
    new_python = paths.versions / "0.2.0/venv/bin/python"

    with pytest.raises(installer.InstallerError, match="state"):
        installer.atomic_write_state(
            paths,
            installer.InstallState(1, "0.2.0", str(new_python)),
        )

    assert paths.state.read_bytes() == original_state
    assert installer.read_state(paths) == old_state


@pytest.mark.skipif(os.name == "nt", reason="POSIX chmod failure regression")
def test_state_commit_has_no_fallible_permission_step_after_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installer = load_installer_template()
    paths = installer.managed_paths(tmp_path / "data")
    python = paths.versions / "0.1.0/venv/bin/python"
    python.parent.mkdir(parents=True)
    python.touch()
    state = installer.InstallState(1, "0.1.0", str(python))
    real_replace = installer.os.replace
    real_chmod = installer.os.chmod
    state_replaced = False

    def mark_state_replace(source: str | Path, destination: str | Path) -> None:
        nonlocal state_replaced
        real_replace(source, destination)
        if Path(destination) == paths.state:
            state_replaced = True

    def reject_post_commit_permission(path: str | Path, mode: int) -> None:
        if state_replaced:
            raise PermissionError("permission call after commit")
        real_chmod(path, mode)

    monkeypatch.setattr(installer.os, "replace", mark_state_replace)
    monkeypatch.setattr(installer.os, "chmod", reject_post_commit_permission)

    installer.atomic_write_state(paths, state)

    assert installer.read_state(paths) == state
