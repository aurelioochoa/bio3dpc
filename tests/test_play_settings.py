"""The play entry point applies saved controls and session overrides."""
import importlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from bio3dpc.input_settings import ControlSettings
from bio3dpc.launcher import Preferences


@pytest.mark.parametrize('arguments, overrides', [
    ([], (None, None, None)),
    (['--mouse', '--mouse-sens', '0.5', '--no-invert-y'], (True, 0.5, False)),
    (['--no-mouse', '--invert-y'], (False, None, True)),
])
def test_play_loads_preferences_and_forwards_session_overrides(monkeypatch, tmp_path, arguments, overrides):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'tools'))
    play = importlib.import_module('tools.play')
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    controls = ControlSettings.from_dict({'mouse_look': False, 'sensitivity': 2.4, 'invert_y': True,
                                         'bindings': {'forward': 'j'}})
    Preferences(controls=controls).save()
    mod = tmp_path / 'game.mod'
    mod.write_bytes(b'module fixture')
    calls = []
    boot = SimpleNamespace(load_module=lambda: None, create_applet=lambda: None,
                           app_start=lambda: None, run=lambda **kwargs: None,
                           shutdown=lambda: calls.append('shutdown'))
    monkeypatch.setattr(play, 'Boot', lambda *args, **kwargs: boot)
    def frontend(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(run=lambda: calls.append('run'))
    monkeypatch.setattr(play, 'Frontend', frontend)
    monkeypatch.setattr(sys, 'argv', ['play.py', '--mod', str(mod), '--data-dir', str(tmp_path), *arguments])
    assert play.main() == 0
    assert calls[0]['control_settings'] == controls
    assert tuple(calls[0][name] for name in ('mouse_look', 'mouse_sensitivity', 'invert_y')) == overrides
    assert calls[1:] == ['run', 'shutdown']
    assert Preferences.load().controls == controls  # Session overrides do not rewrite preferences.


def test_launcher_supplied_controls_override_saved_preferences(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'tools'))
    play = importlib.import_module('tools.play')
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    Preferences(controls=ControlSettings.from_dict({'bindings': {'forward': 'k'}})).save()
    supplied = ControlSettings.from_dict({'bindings': {'forward': 'j'}})
    mod = tmp_path / 'game.mod'
    mod.write_bytes(b'module fixture')
    boot = SimpleNamespace(load_module=lambda: None, create_applet=lambda: None,
                           app_start=lambda: None, run=lambda **kwargs: None, shutdown=lambda: None)
    monkeypatch.setattr(play, 'Boot', lambda *args, **kwargs: boot)
    received = []
    monkeypatch.setattr(play, 'Frontend', lambda *args, **kwargs:
                        (received.append(kwargs['control_settings']) or SimpleNamespace(run=lambda: None)))
    monkeypatch.setattr(sys, 'argv', ['play.py', '--mod', str(mod), '--data-dir', str(tmp_path),
                                     '--controls-json', json.dumps(supplied.to_dict())])
    assert play.main() == 0
    assert received == [supplied]
