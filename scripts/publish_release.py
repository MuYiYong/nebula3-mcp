"""Publish only a verified bundle, retaining immutable already-published releases."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tempfile
from pathlib import Path
from zipfile import ZipFile


def verify_assets(dist: Path, version: str) -> list[Path]:
    expected = {
        'install.py', 'SHA256SUMS', 'RELEASE_NOTES.md',
        f'nebula3_mcp-{version}-py3-none-any.whl', f'nebula3_mcp-{version}.tar.gz',
        f'nebula3-mcp-plugin-{version}.zip',
    }
    if {p.name for p in dist.iterdir()} != expected:
        raise ValueError('unexpected or missing release assets')
    if any(p.is_symlink() or not p.is_file() for p in dist.iterdir()):
        raise ValueError('release assets must be regular files')
    rows = (dist / 'SHA256SUMS').read_text().splitlines()
    checksums = dict(row.split('  ', 1)[::-1] for row in rows)
    if len(rows) != len(checksums) or set(checksums) != expected - {
        'SHA256SUMS', 'RELEASE_NOTES.md'
    }:
        raise ValueError('invalid checksum manifest')
    for name, digest in checksums.items():
        if hashlib.sha256((dist / name).read_bytes()).hexdigest() != digest:
            raise ValueError('asset checksum mismatch')
    with ZipFile(dist / f'nebula3-mcp-plugin-{version}.zip') as archive:
        if set(archive.namelist()) != {'.codex-plugin/plugin.json', '.mcp.json', 'README.md'}:
            raise ValueError('unexpected plugin archive member')
        if archive.testzip() is not None:
            raise ValueError('invalid plugin archive')
    return [dist / name for name in sorted(expected - {'RELEASE_NOTES.md'})]


def gh(*args: str) -> str:
    return subprocess.check_output(['gh', *args], text=True)


def get_optional(endpoint: str) -> dict | None:
    result = subprocess.run(['gh', 'api', endpoint], text=True, capture_output=True, check=False)
    if result.returncode == 0:
        return json.loads(result.stdout)
    if '(HTTP 404)' in result.stderr:
        return None
    raise RuntimeError(f'GitHub API request failed: {endpoint}')


def ensure_tag(repository: str, tag: str, sha: str) -> None:
    endpoint = f'repos/{repository}/git/ref/tags/{tag}'
    ref = get_optional(endpoint)
    if ref is None:
        gh('api', f'repos/{repository}/git/refs', '-f', f'ref=refs/tags/{tag}', '-f', f'sha={sha}')
        ref = get_optional(endpoint)
    if ref is None:
        raise ValueError('release tag was not created')
    target = ref['object']
    while target['type'] == 'tag':
        target = json.loads(gh('api', f"repos/{repository}/git/tags/{target['sha']}"))['object']
    if target['type'] != 'commit' or target['sha'] != sha:
        raise ValueError('tag collision: existing tag belongs to another commit; do not overwrite')


def publish(repository: str, tag: str, sha: str, dist: Path, version: str) -> None:
    assets = verify_assets(dist, version)
    ensure_tag(repository, tag, sha)
    release = get_optional(f'repos/{repository}/releases/tags/{tag}')
    if release is not None and not release['draft']:
        # Reruns may rebuild wheel ZIP timestamps differently. The published bundle is
        # authoritative: verify its integrity instead of replacing immutable assets.
        with tempfile.TemporaryDirectory() as directory:
            gh('release', 'download', tag, '--repo', repository, '--dir', directory)
            downloaded = Path(directory)
            (downloaded / 'RELEASE_NOTES.md').write_text(release['body'] or '')
            verify_assets(downloaded, version)
        print(f'Already published and verified: {tag}')
        return
    if release is None:
        gh('release', 'create', tag, '--repo', repository, '--verify-tag', '--draft',
           '--title', tag.replace('_', ' '), '--notes-file', str(dist / 'RELEASE_NOTES.md'))
    gh('release', 'upload', tag, *map(str, assets), '--repo', repository, '--clobber')
    # Verify uploaded bytes while the release is still a draft.
    with tempfile.TemporaryDirectory() as directory:
        gh('release', 'download', tag, '--repo', repository, '--dir', directory)
        for asset in assets:
            if (Path(directory) / asset.name).read_bytes() != asset.read_bytes():
                raise ValueError('uploaded release asset differs from verified build')
    gh('release', 'edit', tag, '--repo', repository, '--draft=false', '--latest')
    print(f'Published: https://github.com/{repository}/releases/tag/{tag}')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ('repository', 'tag', 'sha', 'version'):
        parser.add_argument(f'--{option}', required=True)
    parser.add_argument('--dist', type=Path, required=True)
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    if re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', args.repository) is None:
        raise ValueError('invalid repository')
    if re.fullmatch(r'v\d{2}\.\d{2}\.\d{2}_Build\d{4}', args.tag) is None:
        raise ValueError('invalid release tag')
    if re.fullmatch(r'[0-9a-f]{40}', args.sha) is None:
        raise ValueError('invalid commit sha')
    if re.fullmatch(r'\d+\.\d+\.\d+\+build\.\d{12}', args.version) is None:
        raise ValueError('invalid package version')
    verify_assets(args.dist, args.version)
    if not args.verify_only:
        publish(args.repository, args.tag, args.sha, args.dist, args.version)


if __name__ == '__main__':
    main()
