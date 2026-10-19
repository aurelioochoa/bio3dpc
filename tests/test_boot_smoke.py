"""Real-game smoke checks skip absent assets and fail on guest faults."""
from __future__ import annotations

import os

import pytest

from bio3dpc.boot import Boot
from bio3dpc.profiles import PROFILES, discover_root

GAME_MOD = str(PROFILES[0].mod_path(discover_root()))

def test_boot_lifecycle(tmp_path):
    if not os.path.exists(GAME_MOD):
        pytest.skip(f'Original module not present: {GAME_MOD}')
    boot = Boot(GAME_MOD, level="error", save_dir=tmp_path)
    try:
        module = boot.load_module()
        # load_module raises BootError unless AEEMod_Load returned SUCCESS
        # (rc 0), so a non-zero module implies rc == 0.
        assert module != 0
        assert boot.module == module

        applet = boot.create_applet()
        assert applet != 0
        assert boot.applet == applet

        boot.app_start()
        assert boot.started
        assert boot.helper.applet == boot.applet
    finally:
        boot.shutdown()


@pytest.mark.parametrize('profile', PROFILES, ids=lambda profile: profile.id)
def test_boot_short_run_has_correct_geometry_and_no_guest_fault(profile, tmp_path):
    module = profile.mod_path(discover_root())
    if not module.is_file():
        pytest.skip(f'Original {profile.id} module not present')
    boot = Boot(module, data_dir=profile.data_path(discover_root()),
                save_dir=tmp_path, level="error")
    try:
        boot.load_module()
        boot.create_applet()
        boot.app_start()
        boot.run(seconds=0.2)
        assert boot.app_clsid == profile.app_clsid
        assert boot.runtime.snapshot_rgb()[:2] == (profile.width, profile.height)
    finally:
        boot.shutdown()
