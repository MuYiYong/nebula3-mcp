"""Derive a reproducible calendar release from the source commit time."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def release_metadata(base: str, timestamp: str) -> dict[str, str]:
    if re.fullmatch(r"\d+\.\d+\.\d+", base) is None:
        raise ValueError("base version must have three numeric components")
    moment = datetime.fromisoformat(timestamp)
    if moment.tzinfo is None:
        raise ValueError("commit timestamp must include a timezone")
    local = moment.astimezone(timezone(timedelta(hours=8)))
    label = local.strftime("v%y.%m.%d_Build%H%M")
    return {
        "tag": label,
        "title": label.replace("_", " "),
        "version": f"{base}+build.{local:%Y%m%d%H%M}",
    }


def stamp(root: Path, version: str) -> None:
    pyproject = root / "pyproject.toml"
    source = pyproject.read_text(encoding="utf-8")
    source, count = re.subn(r'^version = "[^"]+"$', f'version = "{version}"', source, count=1,
                          flags=re.MULTILINE)
    if count != 1:
        raise ValueError("missing project version")
    pyproject.write_text(source, encoding="utf-8")
    (root / "src/nebula3_mcp/__init__.py").write_text(
        f'"""NebulaGraph MCP server."""\n\n__version__ = "{version}"\n', encoding="utf-8"
    )
    path = root / "plugins/nebula3-mcp/.codex-plugin/plugin.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["version"] = version
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    source = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]+)"$', source, re.MULTILINE)
    if match is None:
        raise ValueError("missing project version")
    timestamp = subprocess.check_output(
        ["git", "show", "-s", "--format=%cI", "HEAD"], cwd=ROOT, text=True
    ).strip()
    result = release_metadata(match[1].split("+", 1)[0], timestamp)
    if args.write:
        stamp(ROOT, result["version"])
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8") as stream:
            stream.writelines(f"{key}={value}\n" for key, value in result.items())
    print(json.dumps(result))


if __name__ == "__main__":
    main()
