"""Command-line entry point for the local stdio server."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from nebula3_mcp import __version__
from nebula3_mcp.server import create_server


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nebula3-mcp",
        description="Run the NebulaGraph 3.8 MCP server over stdio.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    build_parser().parse_args(argv)
    create_server().run("stdio")


if __name__ == "__main__":
    main()
