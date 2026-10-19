"""Control preferences survive persistence and reject unusable input maps."""
import json

import pytest

from bio3dpc.input_settings import ControlSettings, DEFAULT_BINDINGS
from bio3dpc.launcher import Preferences, child_command


def test_controls_roundtrip_legacy_settings_and_child_dispatch(tmp_path):
    path = tmp_path / 'launcher.json'
    path.write_text('{"scale": 2}')
    assert Preferences.load(path).controls == ControlSettings()
    controls = ControlSettings.from_dict({'mouse_look': False, 'sensitivity': 2.4, 'invert_y': True,
                                         'bindings': {'forward': 'J', 'heal': ''}})
    preferences = Preferences(controls=controls)
    preferences.save(path)
    assert Preferences.load(path).controls == controls
    assert controls.bindings['forward'] == 'j'
    assert DEFAULT_BINDINGS['forward'] == 'w'
    command = child_command(tmp_path / 'game.mod', tmp_path, 2, controls=controls)
    assert ControlSettings.from_dict(json.loads(command[command.index('--controls-json') + 1])) == controls


@pytest.mark.parametrize('value', [
    [], {'mouse_look': 'yes'}, {'invert_y': 1}, {'sensitivity': True}, {'sensitivity': 0},
    {'sensitivity': 5.1}, {'sensitivity': float('nan')}, {'sensitivity': float('inf')},
    {'bindings': []}, {'bindings': {'unknown': 'j'}}, {'bindings': {'forward': 's'}},
    {'bindings': {'forward': 'Escape'}}, {'bindings': {'forward': 'KP_2'}},
    {'bindings': {'forward': 'F10'}}, {'bindings': {'forward': ['j']}},
])
def test_invalid_controls_are_rejected(value):
    with pytest.raises(ValueError):
        ControlSettings.from_dict(value)
