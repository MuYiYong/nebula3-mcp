from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_env_example_documents_configuration_options() -> None:
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    for required in (
        "不会被 Server 自动读取",
        "Release 安装后",
        "MCP 内配置连接",
        "真实值不得提交",
        "NEBULA_ALLOW_MUTATIONS=false",
        "NEBULA_MAX_ROWS=100",
    ):
        assert required in text


def test_evaluations_define_four_query_routing_workflows() -> None:
    root = ElementTree.parse(ROOT / "evaluations" / "remote_readonly.xml").getroot()
    cases = {item.attrib["id"]: item for item in root.findall("workflow_case")}

    assert set(cases) == {
        "explicit-ngql-scalar",
        "natural-language-graph",
        "natural-language-aggregate",
        "follow-up-query",
    }
    for item in cases.values():
        sequence = item.findtext("expected_tool_sequence") or ""
        invariant = item.findtext("final_answer_invariant") or ""
        assert sequence.strip()
        assert invariant.strip()
        assert "NEBULA_PASSWORD" not in ElementTree.tostring(item, encoding="unicode")

    assert "nebula_execute_query" in (
        cases["explicit-ngql-scalar"].findtext("expected_tool_sequence") or ""
    )
    assert "ngql-skills" in (
        cases["natural-language-graph"].findtext("expected_tool_sequence") or ""
    )
    assert "nebula_execute_query" in (
        cases["follow-up-query"].findtext("expected_tool_sequence") or ""
    )


def test_plugin_readme_documents_generic_and_managed_commands() -> None:
    text = (ROOT / "plugins" / "nebula3-mcp" / "README.md").read_text(encoding="utf-8")

    assert '"command": "nebula3-mcp"' in text
    assert "nebula_configure_connection" in text
    assert "NEBULA_PASSWORD" in text
    assert "不要提交" in text
    assert "安装器" in text


def test_cli_help_does_not_require_database_configuration() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "nebula3_mcp", "--help"],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert "stdio" in completed.stdout


def test_repository_contains_no_runtime_password() -> None:
    runtime_password = os.getenv("NEBULA_PASSWORD")
    if not runtime_password:
        pytest.skip("runtime password is not present in this process")
    tracked = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout.split(b"\0")
    for raw_path in tracked:
        if raw_path:
            path = ROOT / os.fsdecode(raw_path)
            assert runtime_password not in path.read_text(errors="ignore")


def test_readme_covers_install_configure_use_upgrade_and_uninstall() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    for heading in ("## 安装", "## 配置连接", "### 多套环境", "## 使用", "## 升级", "## 卸载"):
        assert heading in text
    assert "https://github.com/MuYiYong/nebula3-mcp/releases/latest/download/install.py" in text
    for command in ("python3 install.py --mode mcp", "py -3 install.py --mode mcp",
                    "python3 install.py --uninstall", "py -3 install.py --uninstall"):
        assert command in text
    for key in ("NEBULA_ADDRESSES", "NEBULA_USERNAME", "NEBULA_PASSWORD",
                "NEBULA_CONNECT_TIMEOUT_MS", "NEBULA_ALLOW_MUTATIONS", "NEBULA_ENVIRONMENT"):
        assert key in text
    assert "CONFIGURATION_REQUIRED" in text
    assert "dev_nebula3" in text and "prod_nebula3" in text
    assert "SPACE_SELECTION_REQUIRED" in text
    assert "NEBULA_DEFAULT_SPACE" in text


def test_maintainer_details_are_separate_from_user_readme() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    for marker in ("## 自动发布", "## 开发与本地验证", "## 工具", "cytoscape-elements-v1",
                   "pip install -e", "--replace-registration"):
        assert marker not in text
    contributor = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    assert "## 自动发布" in contributor
    assert "python -m pytest" in contributor
    for name in ("configuration.md", "tools.md"):
        assert f"/docs/{name}" in text
        assert (ROOT / "docs" / name).is_file()


def test_tool_reference_matches_registered_tools() -> None:
    tree = ast.parse((ROOT / "src/nebula3_mcp/server.py").read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "tool"
        ):
            for keyword in node.keywords:
                if keyword.arg == "name" and isinstance(keyword.value, ast.Constant):
                    names.add(keyword.value.value)
    reference = (ROOT / "docs/tools.md").read_text(encoding="utf-8")
    mentioned = set(re.findall(r"nebula_[a-z_]+", reference))
    # The YueShu-only names are mentioned solely to explain what they map to.
    assert names == mentioned - {"nebula_render_graph", "nebula_validate_gql"}
    assert "兼容入口" in reference
    assert "不能切换 Codex 中的独立 MCP 服务器" in reference
    assert "`--` 是无向边" in reference
