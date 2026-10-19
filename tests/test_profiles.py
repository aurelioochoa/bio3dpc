"""Collection discovery survives grouping and relocation of local originals."""
import json
from pathlib import Path

from bio3dpc import profiles
from bio3dpc.launcher import Preferences


def test_modules_resolve_grouped_and_legacy_collections(tmp_path):
    profile = profiles.N7705
    relative = Path(profile.folder) / profile.module_dir / 'bio3d.mod'
    grouped = tmp_path / 'release_builds' / relative
    legacy = tmp_path / relative
    assert profile.mod_path(tmp_path) == grouped
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b'legacy original')
    assert profile.mod_path(tmp_path) == legacy
    grouped.parent.mkdir(parents=True)
    grouped.write_bytes(b'grouped original')
    assert profile.mod_path(tmp_path) == grouped
    assert profile.data_path(tmp_path) == grouped.parent


def test_discovery_prefers_explicit_and_renamed_local_collection(monkeypatch, tmp_path):
    monkeypatch.setattr(profiles, '__file__', str(tmp_path / 'bio3dpc/profiles.py'))
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path / 'home'))
    monkeypatch.delenv('BIO3DPC_LIBRARY_DIR', raising=False)
    current = tmp_path / 'bioshock3d_game_files'
    legacy = tmp_path / 'bio3d_versions'
    assert profiles.discover_root() == current
    legacy.mkdir()
    assert profiles.discover_root() == legacy
    current.mkdir()
    assert profiles.discover_root() == current
    monkeypatch.setenv('BIO3DPC_LIBRARY_DIR', str(tmp_path / 'external'))
    assert profiles.discover_root() == tmp_path / 'external'


def test_remembered_paths_follow_local_move_without_changing_custom_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(profiles, '__file__', str(tmp_path / 'bio3dpc/profiles.py'))
    old = tmp_path / 'bio3d_versions'
    current = tmp_path / 'bioshock3d_game_files'
    profile = profiles.VX8350
    mod = profile.mod_path(current)
    mod.parent.mkdir(parents=True)
    mod.write_bytes(b'original')
    data = current / 'data/vx8350'
    data.mkdir(parents=True)
    old_mod = old / profile.folder / profile.module_dir / 'bio3d.mod'
    custom = str(tmp_path / 'external/custom.mod')
    path = tmp_path / 'launcher.json'
    path.write_text(json.dumps({'library': str(old), 'paths': {
        'vx8350': {'mod': str(old_mod), 'data': str(old / 'data/vx8350')},
        'custom': {'mod': custom},
    }}))
    preferences = Preferences.load(path)
    assert preferences.library == str(current)
    assert preferences.paths['vx8350'] == {'mod': str(mod), 'data': str(data)}
    assert preferences.paths['custom']['mod'] == custom
    # Loading preferences does not rewrite the user's file.
    assert json.loads(path.read_text())['library'] == str(old)
