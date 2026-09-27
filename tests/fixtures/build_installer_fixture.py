from __future__ import annotations

import base64
import hashlib
import json
import zipfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread


def build_fixture_wheel(directory: Path, version: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    distribution = f"nebula3_mcp-{version}.dist-info"
    files = {
        "nebula3_mcp/__init__.py": f'__version__ = "{version}"\n'.encode(),
        "nebula3_mcp/__main__.py": (
            b"from . import __version__\n"
            b"import sys\n"
            b"if '--version' in sys.argv:\n"
            b"    print(f'nebula3-mcp {__version__}')\n"
        ),
        f"{distribution}/METADATA": (
            f"Metadata-Version: 2.1\nName: nebula3-mcp\nVersion: {version}\n"
        ).encode(),
        f"{distribution}/WHEEL": (
            b"Wheel-Version: 1.0\nGenerator: nebula3-mcp-tests\n"
            b"Root-Is-Purelib: true\nTag: py3-none-any\n"
        ),
    }
    record_rows: list[str] = []
    for name, content in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=")
        record_rows.append(f"{name},sha256={digest.decode()},{len(content)}")
    record_name = f"{distribution}/RECORD"
    record_rows.append(f"{record_name},,")
    files[record_name] = ("\n".join(record_rows) + "\n").encode()
    wheel = directory / f"nebula3_mcp-{version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return wheel


def build_fixture_plugin_archive(directory: Path, version: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    manifest = {
        "name": "nebula3-mcp",
        "version": version,
        "description": "fixture",
        "author": {"name": "fixture"},
        "license": "UNLICENSED",
        "mcpServers": "./.mcp.json",
        "interface": {
            "displayName": "Nebula3 MCP",
            "shortDescription": "fixture",
            "longDescription": "fixture",
            "developerName": "fixture",
            "category": "Developer Tools",
            "capabilities": ["MCP"],
            "defaultPrompt": ["Test connection"],
        },
    }
    mapping = {"mcpServers": {"nebula3": {"command": "nebula3-mcp", "args": []}}}
    archive_path = directory / f"nebula3-mcp-plugin-{version}.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            ".codex-plugin/plugin.json",
            json.dumps(manifest, separators=(",", ":")),
        )
        archive.writestr(".mcp.json", json.dumps(mapping, separators=(",", ":")))
        archive.writestr("README.md", "# Fixture plugin\n")
    return archive_path


def write_checksum_manifest(directory: Path, assets: Sequence[Path]) -> Path:
    manifest = directory / "SHA256SUMS"
    lines = [f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}" for path in assets]
    manifest.write_text("\n".join(sorted(lines)) + "\n", encoding="utf-8")
    return manifest


@contextmanager
def serve_directory(directory: Path) -> Iterator[str]:
    handler = partial(SimpleHTTPRequestHandler, directory=str(directory))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
