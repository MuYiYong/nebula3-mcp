from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from scripts import prepare_release, publish_release, release_version
from tests.unit.test_installer import load_installer_template


def test_calendar_time_and_build_identity(tmp_path: Path) -> None:
    value = release_version.release_metadata('0.5.2', '2026-09-25T23:09:01+00:00')
    assert value == {
        'tag': 'v26.09.26_Build0709', 'title': 'v26.09.26 Build0709',
        'version': '0.5.2+build.202609260709',
    }
    project = tmp_path / 'pyproject.toml'
    project.write_text('[project]\nversion = "0.5.2+build.202609260709"\n')
    assert prepare_release.validate_release_tag(value['tag'], project) == value['version']
    with pytest.raises(ValueError, match='does not match'):
        prepare_release.validate_release_tag('v26.09.26_Build0710', project)
    with pytest.raises(ValueError):
        prepare_release.validate_release_tag('v26.02.30_Build0710', project)
    with pytest.raises(ValueError, match='timezone'):
        release_version.release_metadata('0.5.2', '2026-09-26T07:09:01')


def test_calendar_installer_download_url(tmp_path: Path) -> None:
    output = tmp_path / 'install.py'
    prepare_release.render_installer(
        prepare_release.ROOT / 'installer/install.py.in', output, 'example/nebula3-mcp',
        '0.5.2+build.202609260709', tag='v26.09.26_Build0709',
    )
    assert "TARGET_TAG = 'v26.09.26_Build0709'" in output.read_text()
    installer = load_installer_template()
    assert installer.release_asset_url('example/nebula3-mcp', '0.5.2+build.202609260709',
                                       'v26.09.26_Build0709').endswith(
        '/releases/download/v26.09.26_Build0709'
    )


def test_calendar_plugin_can_be_packaged(tmp_path: Path) -> None:
    plugin = tmp_path / 'plugin'
    shutil.copytree(prepare_release.ROOT / 'plugins/nebula3-mcp', plugin)
    version = '0.5.2+build.202609260709'
    manifest = plugin / '.codex-plugin/plugin.json'
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload['version'] = version
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    assert prepare_release._plugin_manifest_version(plugin) == version
    prepare_release.build_plugin_archive(plugin, tmp_path / 'plugin.zip', version)


def test_tag_collision_does_not_overwrite(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(publish_release, 'get_optional',
                        lambda _: {'object': {'type': 'commit', 'sha': 'a' * 40}})
    def no_write(*args: str) -> str:
        pytest.fail('must not mutate a conflicting tag')
    monkeypatch.setattr(publish_release, 'gh', no_write)
    with pytest.raises(ValueError, match='tag collision'):
        publish_release.ensure_tag('example/nebula3-mcp', 'v26.09.26_Build0709', 'b' * 40)
    publish_release.ensure_tag('example/nebula3-mcp', 'v26.09.26_Build0709', 'a' * 40)


def test_tampering_rejected_before_publication(tmp_path: Path) -> None:
    version = '0.5.2'
    plugin = tmp_path.parent / f'{tmp_path.name}-plugin-src'
    shutil.copytree(prepare_release.ROOT / 'plugins/nebula3-mcp', plugin)
    manifest = plugin / '.codex-plugin/plugin.json'
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload['version'] = version
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    prepare_release.build_plugin_archive(
        plugin, tmp_path / f'nebula3-mcp-plugin-{version}.zip',
        version,
    )
    for name in ['install.py', f'nebula3_mcp-{version}.tar.gz',
                 f'nebula3_mcp-{version}-py3-none-any.whl']:
        (tmp_path / name).write_bytes(b'test asset')
    prepare_release.write_checksums(list(tmp_path.iterdir()), tmp_path / 'SHA256SUMS')
    (tmp_path / 'RELEASE_NOTES.md').write_text('test notes')
    assert len(publish_release.verify_assets(tmp_path, version)) == 5
    (tmp_path / 'install.py').write_bytes(b'modified after validation')
    with pytest.raises(ValueError, match='checksum mismatch'):
        publish_release.verify_assets(tmp_path, version)
