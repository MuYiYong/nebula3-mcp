from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--config-file", type=Path)
    args, command = parser.parse_known_args(argv)
    args.log.parent.mkdir(parents=True, exist_ok=True)
    with args.log.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(command) + "\n")
    if command == ["mcp", "get", "nebula3", "--json"]:
        if not args.state.exists():
            print("No MCP server named 'nebula3' found.", file=sys.stderr)
            return 1
        payload = json.loads(args.state.read_text(encoding="utf-8"))
        if args.config_file is not None and args.config_file.exists():
            marker = "# fake-codex-nebula\n"
            content = args.config_file.read_text(encoding="utf-8")
            if marker in content:
                environment: dict[str, str] = {}
                for line in content.split(marker, 1)[1].splitlines():
                    if line.startswith("NEBULA_"):
                        key, value = line.split(" = ", 1)
                        environment[key] = json.loads(value)
                payload["transport"]["env"] = environment
        print(json.dumps(payload))
        return 0
    if command[:3] == ["mcp", "add", "nebula3"]:
        separator = command.index("--")
        environment: dict[str, str] = {}
        index = 3
        while index < separator:
            if command[index] != "--env" or index + 1 >= separator:
                print("unsupported fake codex mcp add option", file=sys.stderr)
                return 2
            key, value = command[index + 1].split("=", 1)
            environment[key] = value
            index += 2
        launch = command[separator + 1 :]
        payload = {
            "name": "nebula3",
            "transport": {
                "type": "stdio",
                "command": launch[0],
                "args": launch[1:],
                "env": environment or None,
                "env_vars": [],
                "cwd": None,
            },
        }
        args.state.write_text(json.dumps(payload), encoding="utf-8")
        if args.config_file is not None:
            prefix = ""
            if args.config_file.exists():
                prefix = args.config_file.read_text(encoding="utf-8").split(
                    "# fake-codex-nebula\n", 1
                )[0]
            args.config_file.parent.mkdir(parents=True, exist_ok=True)
            rendered = [
                prefix.rstrip("\n"),
                "# fake-codex-nebula",
                "[mcp_servers.nebula3]",
                f"command = {json.dumps(launch[0])}",
                f"args = {json.dumps(launch[1:])}",
                "",
                "[mcp_servers.nebula3.env]",
            ]
            rendered.extend(
                f"{key} = {json.dumps(value)}" for key, value in sorted(environment.items())
            )
            args.config_file.write_text("\n".join(rendered) + "\n", encoding="utf-8")
        return 0
    if command == ["mcp", "remove", "nebula3"]:
        if (
            os.environ.get("FAKE_CODEX_REMOVE_FAIL") == "1"
            or os.environ.get("NEBULA_FAKE_CODEX_REMOVE_FAIL") == "1"
        ):
            print("simulated remove failure", file=sys.stderr)
            return 1
        args.state.unlink(missing_ok=True)
        if args.config_file is not None and args.config_file.exists():
            prefix = args.config_file.read_text(encoding="utf-8").split(
                "# fake-codex-nebula\n", 1
            )[0]
            args.config_file.write_text(prefix, encoding="utf-8")
        return 0
    plugin_state = args.state.with_name("fake-codex-plugin.json")
    marketplace_state = args.state.with_name("fake-codex-marketplace.json")
    if command[:3] == ["plugin", "marketplace", "add"] and command[-1] == "--json":
        marketplace_root = Path(command[3])
        manifest = marketplace_root / ".agents" / "plugins" / "marketplace.json"
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        marketplace_state.write_text(json.dumps(payload), encoding="utf-8")
        print(json.dumps({"name": payload["name"], "path": str(marketplace_root)}))
        return 0
    if command == ["plugin", "add", "nebula3-mcp@nebula3-mcp-local", "--json"]:
        plugin_state.write_text(json.dumps({"installed": True}), encoding="utf-8")
        print(json.dumps({"name": "nebula3-mcp", "marketplace": "nebula3-mcp-local"}))
        return 0
    if command == ["plugin", "remove", "nebula3-mcp@nebula3-mcp-local", "--json"]:
        if os.environ.get("FAKE_CODEX_PLUGIN_REMOVE_FAIL") == "1":
            print("simulated plugin remove failure", file=sys.stderr)
            return 1
        plugin_state.unlink(missing_ok=True)
        print(json.dumps({"removed": True}))
        return 0
    if command == ["plugin", "marketplace", "remove", "nebula3-mcp-local"]:
        if os.environ.get("FAKE_CODEX_MARKETPLACE_REMOVE_FAIL") == "1":
            print("simulated marketplace remove failure", file=sys.stderr)
            return 1
        marketplace_state.unlink(missing_ok=True)
        return 0
    print("unsupported fake codex command", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
