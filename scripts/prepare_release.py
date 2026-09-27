from __future__ import annotations

import argparse
import hashlib
import json
import re
import tarfile
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

SEMVER_TAG = re.compile(r"^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
CALENDAR_TAG = re.compile(r"^v(\d{2}\.\d{2}\.\d{2})_Build(\d{4})$")
PACKAGE_VERSION = re.compile(r"\d+\.\d+\.\d+(?:\+build\.\d{12})?$")
PROJECT_VERSION = re.compile(
    r'^\[project\]\n(?:(?!^\[).)*?^version\s*=\s*"([^"]+)"',
    re.MULTILINE | re.DOTALL,
)
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
ROOT = Path(__file__).resolve().parents[1]
PLUGIN_MEMBERS = {
    Path(".codex-plugin/plugin.json"),
    Path(".mcp.json"),
    Path("README.md"),
}
GENERIC_MCP_MAPPING = {
    "mcpServers": {"nebula3": {"command": "nebula3-mcp", "args": []}}
}
PRIVATE_PLANNING_MEMBERS = {
    PurePosixPath("task_plan.md"),
    PurePosixPath("findings.md"),
    PurePosixPath("progress.md"),
}


def _validate_repository(repository: str) -> None:
    if REPOSITORY.fullmatch(repository) is None:
        raise ValueError("repository must be OWNER/REPOSITORY")


def read_project_version(pyproject: Path) -> str:
    matches = PROJECT_VERSION.findall(pyproject.read_text(encoding="utf-8"))
    if len(matches) != 1:
        raise ValueError("pyproject.toml must contain exactly one project version")
    return matches[0]


def validate_release_tag(tag: str, pyproject: Path) -> str:
    project_version = read_project_version(pyproject)
    calendar = CALENDAR_TAG.fullmatch(tag)
    if calendar:
        moment = datetime.strptime("_".join(calendar.groups()), "%y.%m.%d_%H%M").replace(
            tzinfo=timezone(timedelta(hours=8))
        )
        expected = f"+build.{moment:%Y%m%d%H%M}"
        if PACKAGE_VERSION.fullmatch(project_version) is None or not project_version.endswith(expected):
            raise ValueError("calendar release tag does not match stamped project version")
        return project_version
    if SEMVER_TAG.fullmatch(tag) is None:
        raise ValueError("release tag must be vMAJOR.MINOR.PATCH or vYY.MM.DD_BuildHHMM")
    if tag[1:] != project_version:
        raise ValueError(f"release tag {tag[1:]} does not match project version {project_version}")
    return project_version


def render_installer(
    template: Path,
    output: Path,
    repository: str,
    version: str,
    tag: str | None = None,
) -> None:
    _validate_repository(repository)
    text = template.read_text(encoding="utf-8")
    replacements = {
        "__NEBULA3_MCP_REPOSITORY__": repository,
        "__NEBULA3_MCP_VERSION__": version,
    }
    for token, value in replacements.items():
        if text.count(token) != 1:
            raise ValueError(f"installer template must contain {token} exactly once")
        text = text.replace(token, value)
    if tag is not None:
        if SEMVER_TAG.fullmatch(tag) is None and CALENDAR_TAG.fullmatch(tag) is None:
            raise ValueError("invalid release tag")
        text = text.replace('TARGET_TAG = f"v{TARGET_VERSION}"', f"TARGET_TAG = {tag!r}")
    output.write_text(text, encoding="utf-8")


def write_checksums(assets: Sequence[Path], output: Path) -> None:
    lines = [
        f"{hashlib.sha256(asset.read_bytes()).hexdigest()}  {asset.name}"
        for asset in sorted(assets, key=lambda item: item.name)
    ]
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_release_notes(repository: str, tag: str, output: Path) -> None:
    _validate_repository(repository)
    if SEMVER_TAG.fullmatch(tag) is None and CALENDAR_TAG.fullmatch(tag) is None:
        raise ValueError("invalid release tag")
    url = f"https://github.com/{repository}/releases/download/{tag}/install.py"
    output.write_text(
        f"# {tag.replace(chr(95), chr(32))}\n\n"
        "NebulaGraph 3.8 MCP server: read-only nGQL with interactive graph "
        "inspection (VID, tags, edge src -> dst and rank), PROFILE metrics, charts "
        "and copy feedback. Configure multiple independently named MCP servers such "
        "as dev_nebula3 and prod_nebula3.\n\n"
        "Installation does not require database connection settings. "
        "Configure them with nebula_configure_connection inside MCP, or run "
        "python3 install.py --configure for local no-echo password entry. "
        "Queries and graph space selection reuse one database session.\n\n"
        "To install a shared release folder locally: python3 install.py --assets . "
        "Python dependencies still require pip access or a configured cache.\n\n"
        "```bash\n"
        f"curl -fL -o install.py {url}\n"
        "python3 install.py --mode mcp\n\n"
        "# upgrade\n"
        "python3 install.py --mode mcp\n\n"
        "# uninstall\n"
        "python3 install.py --uninstall\n"
        "```\n\n"
        "Windows PowerShell:\n\n"
        "```powershell\n"
        f"Invoke-WebRequest -Uri \"{url}\" -OutFile install.py\n"
        "py -3 install.py --mode mcp\n\n"
        "# upgrade\n"
        "py -3 install.py --mode mcp\n\n"
        "# uninstall\n"
        "py -3 install.py --uninstall\n"
        "```\n",
        encoding="utf-8",
    )


def _read_json_object(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"plugin {label} must be readable valid JSON") from exc
    if not isinstance(value, dict):
        raise TypeError(f"plugin {label} must contain a JSON object")
    return value


def _validate_plugin_documents(plugin_root: Path, version: str) -> None:
    manifest = _read_json_object(
        plugin_root / ".codex-plugin" / "plugin.json",
        "manifest",
    )
    if (
        manifest.get("name") != "nebula3-mcp"
        or manifest.get("version") != version
        or manifest.get("mcpServers") != "./.mcp.json"
    ):
        raise ValueError("plugin manifest name, version, or MCP reference is invalid")
    mapping = _read_json_object(plugin_root / ".mcp.json", "MCP mapping")
    if mapping != GENERIC_MCP_MAPPING:
        raise ValueError("plugin MCP mapping must use only the generic nebula3-mcp command")
    try:
        readme = (plugin_root / "README.md").read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError("plugin README must be readable") from exc
    if not readme.strip():
        raise ValueError("plugin README must not be empty")


def build_plugin_archive(plugin_root: Path, output: Path, version: str) -> None:
    """Create an allowlisted, deterministic Codex plugin archive."""

    paths = list(plugin_root.rglob("*"))
    if any(path.is_symlink() for path in paths):
        raise ValueError("plugin archive cannot contain symlinks")
    members = {path.relative_to(plugin_root) for path in paths if path.is_file()}
    if members != PLUGIN_MEMBERS:
        raise ValueError("plugin archive contains unexpected or missing files")
    _validate_plugin_documents(plugin_root, version)
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for relative in sorted(members, key=lambda item: item.as_posix()):
            info = ZipInfo(relative.as_posix(), date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(
                info,
                (plugin_root / relative).read_bytes(),
                compress_type=ZIP_DEFLATED,
            )


def _plugin_manifest_version(plugin_root: Path) -> str:
    manifest = _read_json_object(
        plugin_root / ".codex-plugin" / "plugin.json",
        "manifest",
    )
    version = manifest.get("version")
    if not isinstance(version, str) or PACKAGE_VERSION.fullmatch(version) is None:
        raise ValueError("plugin manifest version must be a supported package version")
    return version


def _distribution_assets(dist: Path, version: str) -> tuple[Path, Path]:
    expected_wheel = dist / f"nebula3_mcp-{version}-py3-none-any.whl"
    expected_sdist = dist / f"nebula3_mcp-{version}.tar.gz"
    wheels = sorted(dist.glob("*.whl"))
    sdists = sorted(dist.glob("*.tar.gz"))
    if wheels != [expected_wheel] or sdists != [expected_sdist]:
        raise ValueError(
            "dist must contain exactly one wheel and one sdist for the project version"
        )
    return expected_wheel, expected_sdist


def validate_sdist_archive(sdist: Path, version: str) -> None:
    """Reject private planning data and non-installation files in the sdist."""
    archive_root = PurePosixPath(f"nebula3_mcp-{version}")
    required = {
        archive_root / "README.md",
        archive_root / "pyproject.toml",
        archive_root / "PKG-INFO",
    }
    files: set[PurePosixPath] = set()
    try:
        with tarfile.open(sdist, "r:gz") as package:
            for member in package.getmembers():
                path = PurePosixPath(member.name)
                if path == archive_root or member.isdir():
                    continue
                try:
                    relative = path.relative_to(archive_root)
                except ValueError as exc:
                    raise ValueError("sdist member is outside the versioned root") from exc
                if relative in PRIVATE_PLANNING_MEMBERS or relative.parts[:2] == (
                    "docs",
                    "superpowers",
                ):
                    raise ValueError("sdist contains private planning content")
                if not member.isfile():
                    raise ValueError("sdist contains a non-regular member")
                public_metadata = {
                    PurePosixPath(".gitignore"),
                    PurePosixPath("README.md"),
                    PurePosixPath("pyproject.toml"),
                    PurePosixPath("PKG-INFO"),
                }
                source_root = PurePosixPath("src/nebula3_mcp")
                is_source = False
                try:
                    source_relative = relative.relative_to(source_root)
                    is_source = source_relative.suffix == ".py" or source_relative in {
                        PurePosixPath("ui/query-result.html"),
                    }
                except ValueError:
                    pass
                if relative not in public_metadata and not is_source:
                    raise ValueError("sdist contains an unexpected public member")
                files.add(path)
    except (OSError, tarfile.TarError) as exc:
        raise ValueError("sdist must be a readable gzip tar archive") from exc
    if not required.issubset(files):
        raise ValueError("sdist is missing required package metadata")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare deterministic GitHub Release assets")
    parser.add_argument("--repository", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--dist", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    version = validate_release_tag(args.tag, ROOT / "pyproject.toml")
    wheel, sdist = _distribution_assets(args.dist, version)
    validate_sdist_archive(sdist, version)
    installer = args.dist / "install.py"
    render_installer(
        ROOT / "installer/install.py.in",
        installer,
        args.repository,
        version,
        tag=args.tag,
    )
    assets = [wheel, sdist, installer]
    plugin_root = ROOT / "plugins" / "nebula3-mcp"
    if not plugin_root.is_dir():
        raise ValueError("plugin source directory is missing")
    if _plugin_manifest_version(plugin_root) == version:
        plugin = args.dist / f"nebula3-mcp-plugin-{version}.zip"
        build_plugin_archive(plugin_root, plugin, version)
        assets.append(plugin)
    write_checksums(assets, args.dist / "SHA256SUMS")
    write_release_notes(args.repository, args.tag, args.dist / "RELEASE_NOTES.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
