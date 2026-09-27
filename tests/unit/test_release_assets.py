from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import tarfile
import textwrap
from pathlib import Path
from zipfile import ZipFile

import pytest

from scripts import prepare_release

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_ROOT = ROOT / "plugins" / "nebula3-mcp"


def build_fixture_sdist(
    path: Path,
    version: str,
    extra_member: str | None = None,
) -> None:
    root = f"nebula3_mcp-{version}"
    with tarfile.open(path, "w:gz") as package:
        members = [
            "README.md",
            "pyproject.toml",
            "PKG-INFO",
            "src/nebula3_mcp/__init__.py",
        ]
        if extra_member is not None:
            members.append(extra_member)
        for relative in members:
            payload = relative.encode()
            info = tarfile.TarInfo(f"{root}/{relative}")
            info.size = len(payload)
            package.addfile(info, io.BytesIO(payload))


def workflow_block(text: str, header: str) -> str:
    lines = text.splitlines()
    start = lines.index(header)
    indentation = len(header) - len(header.lstrip())
    end = len(lines)
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if line and len(line) - len(line.lstrip()) <= indentation:
            end = index
            break
    return "\n".join(lines[start:end]).rstrip()


def workflow_step_names(job: str) -> list[str]:
    return re.findall(r"^      - name: (.+)$", job, re.MULTILINE)


def workflow_run_script(step: str) -> str:
    for line in step.splitlines():
        if line.startswith("        run: "):
            value = line.removeprefix("        run: ")
            if value != "|":
                return value
            run = workflow_block(step, "        run: |").splitlines()[1:]
            return textwrap.dedent("\n".join(run))
    raise AssertionError("workflow step must contain an executable run value")


def executable_shell_lines(script: str) -> list[str]:
    return [
        line.strip()
        for line in script.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def workflow_step_run_lines(job: str, name: str) -> list[str]:
    step = workflow_block(job, f"      - name: {name}")
    return executable_shell_lines(workflow_run_script(step))


def assert_lines_in_order(lines: list[str], required: list[str]) -> None:
    for line in required:
        assert line in lines
    positions = [lines.index(line) for line in required]
    assert positions == sorted(positions)


def test_tag_must_exactly_match_project_version(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "nebula3-mcp"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )

    assert prepare_release.validate_release_tag("v0.1.0", pyproject) == "0.1.0"
    with pytest.raises(ValueError, match="does not match"):
        prepare_release.validate_release_tag("v0.6.0", pyproject)


def test_current_project_server_and_plugin_versions_match() -> None:
    assert prepare_release.read_project_version(ROOT / "pyproject.toml") == "0.1.0"
    assert '__version__ = "0.1.0"' in (ROOT / "src" / "nebula3_mcp" / "__init__.py").read_text(
        encoding="utf-8"
    )
    assert 'version=__version__' in (ROOT / "src" / "nebula3_mcp" / "server.py").read_text(
        encoding="utf-8"
    )
    manifest = json.loads(
        (PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    assert manifest["version"] == "0.1.0"


def test_sdist_build_is_limited_to_installable_public_files() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert (
        '[tool.hatch.build.targets.sdist]\ninclude = [\n'
        '  "/src",\n'
        '  "/README.md",\n'
        '  "/pyproject.toml",\n'
        "]\n"
    ) in pyproject


def test_release_rejects_unexpected_source_artifacts(tmp_path: Path) -> None:
    archive = tmp_path / "nebula3_mcp-0.1.0.tar.gz"
    build_fixture_sdist(
        archive,
        "0.1.0",
        extra_member="src/nebula3_mcp/ui/query-result 2.html",
    )

    with pytest.raises(ValueError, match="unexpected public"):
        prepare_release.validate_sdist_archive(archive, "0.1.0")


@pytest.mark.parametrize(
    "private_member",
    [
        "task_plan.md",
        "findings.md",
        "progress.md",
        "docs/superpowers/plan.md",
    ],
)
def test_release_rejects_private_sdist_members(
    tmp_path: Path,
    private_member: str,
) -> None:
    archive = tmp_path / "nebula3_mcp-0.1.0.tar.gz"
    payload = b"private"
    with tarfile.open(archive, "w:gz") as package:
        info = tarfile.TarInfo(f"nebula3_mcp-0.1.0/{private_member}")
        info.size = len(payload)
        package.addfile(info, io.BytesIO(payload))

    with pytest.raises(ValueError, match="private planning"):
        prepare_release.validate_sdist_archive(archive, "0.1.0")


def test_plugin_manifest_mcp_mapping_and_marketplace_are_exact() -> None:
    manifest = json.loads(
        (PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    mcp = json.loads((PLUGIN_ROOT / ".mcp.json").read_text(encoding="utf-8"))
    marketplace = json.loads(
        (ROOT / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8")
    )

    assert manifest["name"] == "nebula3-mcp"
    assert manifest["version"] == "0.1.0"
    assert manifest["mcpServers"] == "./.mcp.json"
    assert mcp == {"mcpServers": {"nebula3": {"command": "nebula3-mcp", "args": []}}}
    assert marketplace == {
        "name": "nebula3-mcp-local",
        "interface": {"displayName": "Nebula3 MCP Local"},
        "plugins": [
            {
                "name": "nebula3-mcp",
                "source": {"source": "local", "path": "./plugins/nebula3-mcp"},
                "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
                "category": "Developer Tools",
            }
        ],
    }


def test_plugin_archive_is_allowlisted_and_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"

    prepare_release.build_plugin_archive(PLUGIN_ROOT, first, "0.1.0")
    prepare_release.build_plugin_archive(PLUGIN_ROOT, second, "0.1.0")

    assert hashlib.sha256(first.read_bytes()).digest() == hashlib.sha256(
        second.read_bytes()
    ).digest()
    with ZipFile(first) as archive:
        assert archive.testzip() is None
        assert archive.namelist() == [
            ".codex-plugin/plugin.json",
            ".mcp.json",
            "README.md",
        ]
        assert all(not name.startswith("/") and ".." not in name.split("/") for name in archive.namelist())
        archived_manifest = json.loads(archive.read(".codex-plugin/plugin.json"))
    assert archived_manifest["version"] == "0.1.0"


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("extra", "unexpected or missing files"),
        ("symlink", "cannot contain symlinks"),
        ("absolute-command", "MCP mapping"),
        ("parent-argument", "MCP mapping"),
        ("secret", "MCP mapping"),
    ],
)
def test_plugin_archive_rejects_unsafe_or_unexpected_content(
    tmp_path: Path,
    mutation: str,
    message: str,
) -> None:
    plugin = tmp_path / "plugin"
    shutil.copytree(PLUGIN_ROOT, plugin)
    if mutation == "extra":
        (plugin / "unexpected.txt").write_text("extra", encoding="utf-8")
    elif mutation == "symlink":
        (plugin / "link").symlink_to(plugin / "README.md")
    else:
        mapping = json.loads((plugin / ".mcp.json").read_text(encoding="utf-8"))
        server = mapping["mcpServers"]["nebula3"]
        if mutation == "absolute-command":
            server["command"] = "/tmp/nebula3-mcp"
        elif mutation == "parent-argument":
            server["args"] = ["../launcher.py"]
        else:
            server["env"] = {"NEBULA_PASSWORD": "not-a-real-secret"}
        (plugin / ".mcp.json").write_text(json.dumps(mapping), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        prepare_release.build_plugin_archive(plugin, tmp_path / "plugin.zip", "0.1.0")


def test_rendered_installer_contains_exact_repository_and_version(tmp_path: Path) -> None:
    output = tmp_path / "install.py"

    prepare_release.render_installer(
        template=ROOT / "installer/install.py.in",
        output=output,
        repository="example-org/nebula3-mcp",
        version="0.1.0",
    )

    text = output.read_text(encoding="utf-8")
    assert 'TARGET_REPOSITORY = "example-org/nebula3-mcp"' in text
    assert 'TARGET_VERSION = "0.1.0"' in text
    assert "__NEBULA3_MCP_" not in text


def test_rendered_installer_executes_version_smoke(tmp_path: Path) -> None:
    output = tmp_path / "install.py"
    prepare_release.render_installer(
        template=ROOT / "installer/install.py.in",
        output=output,
        repository="example-org/nebula3-mcp",
        version="0.1.0",
    )

    completed = subprocess.run(
        [sys.executable, str(output), "--version"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert completed.stdout == "0.1.0\n"
    assert completed.stderr == ""


def test_checksums_are_sorted_and_release_notes_use_exact_asset_url(tmp_path: Path) -> None:
    wheel = tmp_path / "nebula3_mcp-0.1.0-py3-none-any.whl"
    sdist = tmp_path / "nebula3_mcp-0.1.0.tar.gz"
    installer = tmp_path / "install.py"
    wheel.write_bytes(b"wheel")
    sdist.write_bytes(b"sdist")
    installer.write_bytes(b"installer")
    checksums = tmp_path / "SHA256SUMS"
    notes = tmp_path / "RELEASE_NOTES.md"

    prepare_release.write_checksums([wheel, sdist, installer], checksums)
    prepare_release.write_release_notes("example-org/nebula3-mcp", "v0.1.0", notes)

    names = [line.split("  ", 1)[1] for line in checksums.read_text().splitlines()]
    assert names == sorted(names)
    assert (
        "```bash\n"
        "curl -fL -o install.py "
        "https://github.com/example-org/nebula3-mcp/releases/download/v0.1.0/install.py\n"
        "python3 install.py --mode mcp\n\n"
        "# upgrade\n"
        "python3 install.py --mode mcp\n\n"
        "# uninstall\n"
        "python3 install.py --uninstall\n"
        "```\n"
    ) in notes.read_text(encoding="utf-8")
    for fragment in ("nebula_configure_connection", "--configure", "--assets .", "one database session"):
        assert fragment in notes.read_text(encoding="utf-8")
    notes_text = notes.read_text(encoding="utf-8")
    for required in (
        "Windows PowerShell",
        "Invoke-WebRequest",
        "py -3 install.py",
        "py -3 install.py --uninstall",
    ):
        assert required in notes_text


def test_cli_prepares_only_exact_current_build_assets(tmp_path: Path) -> None:
    wheel = tmp_path / "nebula3_mcp-0.1.0-py3-none-any.whl"
    sdist = tmp_path / "nebula3_mcp-0.1.0.tar.gz"
    wheel.write_bytes(b"wheel")
    build_fixture_sdist(sdist, "0.1.0")

    result = prepare_release.main(
        [
            "--repository",
            "local-validation/nebula3-mcp",
            "--tag",
            "v0.1.0",
            "--dist",
            str(tmp_path),
        ]
    )

    assert result == 0
    assert [
        line.split("  ", 1)[1]
        for line in (tmp_path / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    ] == ["install.py", "nebula3-mcp-plugin-0.1.0.zip", wheel.name, sdist.name]
    assert (tmp_path / "RELEASE_NOTES.md").is_file()


def test_cli_includes_plugin_archive_when_release_version_matches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release_root = tmp_path / "source"
    dist = tmp_path / "dist"
    (release_root / "installer").mkdir(parents=True)
    dist.mkdir()
    (release_root / "pyproject.toml").write_text(
        '[project]\nname = "nebula3-mcp"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    (release_root / "installer" / "install.py.in").write_text(
        'REPOSITORY = "__NEBULA3_MCP_REPOSITORY__"\n'
        'VERSION = "__NEBULA3_MCP_VERSION__"\n',
        encoding="utf-8",
    )
    shutil.copytree(PLUGIN_ROOT, release_root / "plugins" / "nebula3-mcp")
    (dist / "nebula3_mcp-0.1.0-py3-none-any.whl").write_bytes(b"wheel")
    build_fixture_sdist(dist / "nebula3_mcp-0.1.0.tar.gz", "0.1.0")
    monkeypatch.setattr(prepare_release, "ROOT", release_root)

    assert (
        prepare_release.main(
            [
                "--repository",
                "local-validation/nebula3-mcp",
                "--tag",
                "v0.1.0",
                "--dist",
                str(dist),
            ]
        )
        == 0
    )

    plugin = dist / "nebula3-mcp-plugin-0.1.0.zip"
    assert plugin.is_file()
    checksum_names = [
        line.split("  ", 1)[1]
        for line in (dist / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    ]
    assert checksum_names == [
        "install.py",
        plugin.name,
        "nebula3_mcp-0.1.0-py3-none-any.whl",
        "nebula3_mcp-0.1.0.tar.gz",
    ]


def test_cli_rejects_old_or_extra_distribution_assets(tmp_path: Path) -> None:
    (tmp_path / "nebula3_mcp-0.1.0-py3-none-any.whl").write_bytes(b"wheel")
    (tmp_path / "nebula3_mcp-0.1.0.tar.gz").write_bytes(b"sdist")
    (tmp_path / "nebula3_mcp-0.1.4-py3-none-any.whl").write_bytes(b"old wheel")

    with pytest.raises(ValueError, match="exactly one wheel and one sdist"):
        prepare_release.main(
            [
                "--repository",
                "local-validation/nebula3-mcp",
                "--tag",
                "v0.1.0",
                "--dist",
                str(tmp_path),
            ]
        )


def assert_release_workflow_contract(workflow: str) -> None:
    validate = workflow_block(workflow, "  validate:")
    compatibility = workflow_block(workflow, "  installer-compatibility:")
    publish = workflow_block(workflow, "  publish:")
    assert workflow_block(workflow, "permissions:") == "permissions:\n  contents: read"
    assert "contents: write" not in validate + compatibility
    assert "contents: write" in publish
    assert "needs: [validate, installer-compatibility]" in publish
    assert "github.ref == 'refs/heads/main' && github.event_name != 'pull_request'" in publish
    assert "branches: [main]" in workflow_block(workflow, "  push:")
    assert "  pull_request:" in workflow and "  workflow_dispatch:" in workflow
    assert "os: [ubuntu-latest, windows-latest]" in compatibility
    for action in re.findall(r"uses: ([^\s]+)", workflow):
        assert re.fullmatch(r"actions/[a-z-]+@[a-f0-9]{40}", action)
    assert_lines_in_order(workflow_step_names(validate), ["Run test suite",
        "Stamp reproducible release version", "Build distributions and prepare Release assets",
        "Upload test artifacts"])
    assert_lines_in_order(workflow_step_run_lines(validate, "Run test suite"),
        ["python -m pytest -q", "python -m ruff check --extension in:python .", "python -m mypy src"])
    for command in ["npm test", "npm run typecheck"]:
        assert command in workflow_step_run_lines(validate, "Run UI tests and typecheck")
    assert "git diff --exit-code -- ../src/nebula3_mcp/ui/query-result.html" in (
        workflow_step_run_lines(validate, "Build UI and verify committed artifact"))
    assets = workflow_step_run_lines(validate, "Build distributions and prepare Release assets")
    assert "(cd dist && shasum -a 256 -c SHA256SUMS)" in assets
    assert any("tests/protocol/test_stdio.py" in line for line in assets)
    assert any("publish_release.py --verify-only" in line for line in assets)
    final = workflow_step_run_lines(publish, "Verify and publish exact assets")
    assert len(final) == 1 and "publish_release.py" in final[0] and "--verify-only" not in final[0]
    assert "GH_TOKEN" not in validate + compatibility
    assert "GH_TOKEN: ${{ github.token }}" in publish


def test_release_workflow_is_ordered_and_least_privileged_at_publish_boundary() -> None:
    assert_release_workflow_contract((ROOT / ".github/workflows/release.yml").read_text())


@pytest.mark.parametrize("gate", [
    "          python -m pytest -q",
    "          (cd dist && shasum -a 256 -c SHA256SUMS)",
    "          git diff --exit-code -- ../src/nebula3_mcp/ui/query-result.html",
])
def test_release_workflow_contract_rejects_commented_executable_gate(gate: str) -> None:
    workflow = (ROOT / ".github/workflows/release.yml").read_text()
    assert_release_workflow_contract(workflow)
    with pytest.raises(AssertionError):
        assert_release_workflow_contract(workflow.replace(gate, gate.replace("          ", "          # ")))
