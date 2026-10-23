"""Launcher boundaries: preferences, original assets and responsive child dispatch."""
import hashlib
import json
import os
import signal
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from bio3dpc import launcher


@pytest.fixture(autouse=True)
def isolated_campaign(monkeypatch, tmp_path):
    # A rebuilt landscape campaign on this machine must not leak into tests.
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'data home'))


def profile_for(mod, data, name='vx8350'):
    return SimpleNamespace(id=name, title='Portrait · LG VX8350', width=176, height=220,
                           app_clsid=0x12345678, sha256=hashlib.sha256(mod.read_bytes()).hexdigest(),
                           mod_path=lambda root: mod, data_path=lambda root: data)


def original_files(tmp_path):
    mod = tmp_path / 'original game.mod'
    mod.write_bytes(b'original module')
    data = tmp_path / 'original data'
    data.mkdir()
    for index in range(1, 8):
        (data / f'bio{index}.DAT').write_bytes(b'original asset')
    return mod, data


def test_preferences_roundtrip_and_invalid_values(tmp_path):
    path = tmp_path / 'preferences' / 'launcher.json'
    wanted = launcher.Preferences('n7705', '/a collection', 4, False,
                                  {'n7705': {'mod': '/game.mod', 'data': '/data'}})
    wanted.save(path)
    assert launcher.Preferences.load(path) == wanted
    for invalid in ({'scale': 0}, {'scale': True}, {'audio': 'yes'}, {'animation': 'yes'}, {'paths': {'x': 'bad'}}, []):
        path.write_text(json.dumps(invalid))
        with pytest.raises(ValueError):
            launcher.Preferences.load(path)


def test_relative_xdg_path_is_ignored(monkeypatch, tmp_path):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('XDG_CONFIG_HOME', 'relative/settings')
    assert launcher.settings_path() == tmp_path / '.config/bio3dpc/launcher.json'


def test_validation_rejects_missing_assets_and_mismatched_module(tmp_path):
    mod, data = original_files(tmp_path)
    profile = profile_for(mod, data)
    assert launcher.validate_paths(mod, data, profile) == []
    mod.write_bytes(b'another original build')
    assert 'does not match' in launcher.validate_paths(mod, data, profile)[0]
    (data / 'bio1.DAT').unlink()
    assert 'Missing original game assets: bio1.dat' in launcher.validate_paths(mod, data, profile)[0]
    for asset in data.iterdir():
        asset.unlink()
    assert 'No original .DAT' in launcher.validate_paths(mod, data)[0]
    assert len(launcher.validate_paths(tmp_path / 'missing.mod', tmp_path / 'missing')) == 2


def test_child_command_contains_full_profile_and_literal_paths(tmp_path):
    mod, data = original_files(tmp_path)
    profile = profile_for(mod, data)
    assert launcher.child_command(mod, data, 2, profile) == [
        sys.executable, '-u', str(launcher.ROOT / 'tools/play.py'), '--mod', str(mod),
        '--data-dir', str(data), '--scale', '2', '--width', '176', '--height', '220',
        '--app-clsid', '0x12345678',
    ]


def test_validation_rejects_a_different_handsets_starter_assets(tmp_path):
    mod, data = original_files(tmp_path)
    profile = profile_for(mod, data)
    profile.asset_hashes = (('bio1.dat', hashlib.sha256(b'original asset').hexdigest()),)
    assert launcher.validate_paths(mod, data, profile) == []
    (data/'bio1.DAT').write_bytes(b'other handset')
    assert 'does not match this original build' in launcher.validate_paths(mod, data, profile)[0]


def test_spawn_is_nonblocking_and_captures_error_output(monkeypatch, tmp_path):
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    monkeypatch.setattr(launcher, 'child_command', lambda *args: [
        sys.executable, '-u', '-c',
        'import os,time,sys; print(os.environ["BIO3DPC_AUDIO"]); time.sleep(0.3); print("guest fault",file=sys.stderr); sys.exit(7)',
    ])
    child = launcher.start_game(tmp_path / 'game.mod', tmp_path, 3, False)
    try:
        assert child.poll() is None
        assert child.wait(timeout=5) == 7
        assert launcher.log_path().read_text() == '0\nguest fault\n'
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


def test_tk_launcher_dispatches_selected_profile_without_blocking(monkeypatch, tmp_path):
    import tkinter as tk
    try:
        window = tk.Tk()
    except tk.TclError:
        if os.environ.get('BIO3DPC_REQUIRE_TK'):
            raise
        pytest.skip('No graphical display; set BIO3DPC_REQUIRE_TK=1 to require real Tk')
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    mod, data = original_files(tmp_path)
    profile = profile_for(mod, data)
    landscape = profile_for(mod, data, 'n7705')
    landscape.title, landscape.width, landscape.height = 'Landscape · Nokia 7705', 320, 240
    monkeypatch.setattr(launcher, 'profiles', lambda: (profile, landscape))
    monkeypatch.setattr(launcher, 'child_command', lambda *args: [sys.executable, '-c', 'import time; time.sleep(1)'])
    child = None
    try:
        app = launcher.Launcher(window, library=tmp_path, profile='n7705')
        window.update()
        assert '320 × 240' in app.detail.get()
        assert 'first two DAT' in app.detail.get()
        assert 'Ready to launch' in app.status.get()
        app.audio.set(False)
        app.launch()
        child = app.child
        assert child is not None and child.poll() is None
        assert app.view.decoder.process is None  # Gameplay gets the CPU.
        assert str(app.launch_button.cget('state')) == 'disabled'
        assert launcher.Preferences.load().profile == 'n7705'
        # An actual Tk event must run while the child is still executing.
        ticks = []
        window.after(0, lambda: ticks.append('responsive'))
        window.update()
        assert ticks == ['responsive'] and child.poll() is None
        child.wait(timeout=5)
        app.tick()
        assert app.child is None and app.status.get() == 'Game closed.'
        if __import__('shutil').which('ffmpeg'):
            assert app.view.decoder.process is not None
        assert mod.read_bytes() == b'original module'
        assert (data / 'bio1.DAT').read_bytes() == b'original asset'
        app.close()
    finally:
        if child is not None and child.poll() is None:
            child.kill()
            child.wait()
        try:
            window.destroy()
        except tk.TclError:
            pass


def test_landscape_prefers_a_verified_rebuilt_campaign(monkeypatch, tmp_path):
    import tkinter as tk
    try:
        window = tk.Tk()
    except tk.TclError:
        if os.environ.get('BIO3DPC_REQUIRE_TK'):
            raise
        pytest.skip('No graphical display; set BIO3DPC_REQUIRE_TK=1 to require real Tk')
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    mod, data = original_files(tmp_path)
    landscape = profile_for(mod, data, 'n7705')
    landscape.title, landscape.width, landscape.height = 'Landscape · Nokia 7705', 320, 240
    rebuilt = tmp_path / 'rebuilt campaign'
    rebuilt.mkdir()
    for index in range(1, 8):
        (rebuilt / f'bio{index}.dat').write_bytes(b'built from my files')
    monkeypatch.setattr(launcher, 'profiles', lambda: (landscape,))
    monkeypatch.setattr(launcher, 'rebuilt_campaign', lambda: rebuilt)
    monkeypatch.setattr(launcher, 'landscape_campaign', lambda folder: Path(folder) == rebuilt)
    try:
        app = launcher.Launcher(window, library=tmp_path, profile='n7705')
        assert Path(app.data.get()) == rebuilt
        assert 'built on this computer from your own files' in app.detail.get()
        assert 'campaign built from your own files' in app.installation_status.get()
        # The starter-pack hashes are covered by the campaign verification.
        assert launcher.validate_paths(mod, rebuilt, landscape) == []
        app.data.set(str(data))
        app.refresh_status()
        assert 'first two DAT' in app.detail.get()
        assert 'Complete campaign' in app.detail.get()
        # Installation → Complete campaign guides the player through their own files.
        app.view.installation()
        app.view.campaign_button.invoke()
        dialog = app.view.campaign_window
        assert dialog is not None
        guide = app.view.campaign_guide.get('1.0', 'end')
        assert 'does not include, download or share' in guide and 'supply yourself' in guide
        assert 'VX9900' in guide and 'VX8350' in guide and 'keep it private' in guide
        own = tmp_path / 'my vx9900'
        own.mkdir()
        monkeypatch.setattr('bio3dpc.bathysphere.filedialog.askdirectory', lambda **kwargs: str(own))
        app.view.campaign_browse_buttons[0].invoke()
        assert Path(app.campaign_vx9900.get()) == own
        assert 'bio3.dat is missing' in app.campaign_status.get()
        # Without the player's files the build explains itself and changes nothing.
        monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'empty data home'))
        app.view.campaign_build_button.invoke()
        assert 'Cannot build' in app.campaign_status.get()
        assert Path(app.data.get()) == data
        assert not (tmp_path / 'empty data home').exists()
        app.save()
        assert launcher.Preferences.load().paths['n7705']['vx9900'] == str(own)
        app.view.close()
    finally:
        window.destroy()


def test_real_launcher_starts_landscape_guest_and_pauses_video(monkeypatch, tmp_path):
    import tkinter as tk
    from bio3dpc.profiles import N7705, discover_root
    if not N7705.mod_path(discover_root()).is_file():
        pytest.skip('Original landscape module absent')
    try:
        window = tk.Tk()
    except tk.TclError:
        if os.environ.get('BIO3DPC_REQUIRE_TK'):
            raise
        pytest.skip('No display')
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path/'config'))
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path/'state'))
    app = launcher.Launcher(window, profile='n7705')
    child = None
    try:
        app.audio.set(False)
        app.scale.set(1)
        app.launch()
        child = app.child
        assert child is not None
        assert app.view.decoder.process is None
        deadline = time.monotonic()+12
        while time.monotonic() < deadline and child.poll() is None:
            window.update()
            if 'window ready width=320 height=240 scale=1' in launcher.log_path().read_text():
                break
            time.sleep(.02)
        log = launcher.log_path().read_text()
        assert 'window ready width=320 height=240 scale=1' in log, log
        # Let the frontend enter its event loop and install its Ctrl+C handler.
        for _ in range(10):
            window.update()
            time.sleep(.01)
        child.send_signal(signal.SIGINT)
        assert child.wait(timeout=5) == 0
        app.tick()
        assert app.child is None
        assert 'guest faulted' not in launcher.log_path().read_text()
    finally:
        if child is not None and child.poll() is None:
            child.terminate()
            child.wait(timeout=3)
        app.close()


def test_installation_guides_missing_files_verifies_and_remembers_collection(monkeypatch, tmp_path):
    import tkinter as tk
    from dataclasses import replace
    from bio3dpc.profiles import VX8350

    try:
        window = tk.Tk()
    except tk.TclError:
        if os.environ.get('BIO3DPC_REQUIRE_TK'):
            raise
        pytest.skip('No graphical display')
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    monkeypatch.delenv('BIO3DPC_LIBRARY_DIR', raising=False)
    root = tmp_path / 'my game collection'
    mod = VX8350.mod_path(root)
    mod.parent.mkdir(parents=True)
    mod.write_bytes(b'original module')
    data = root / 'data' / 'vx8350'
    data.mkdir(parents=True)
    for index in range(1, 8):
        (data / f'bio{index}.dat').write_bytes(b'original asset')
    (data / 'bio3d.idx').write_bytes(b'original index')
    profile = replace(VX8350, sha256=hashlib.sha256(mod.read_bytes()).hexdigest(), asset_hashes=tuple(
        (name, hashlib.sha256((data / name).read_bytes()).hexdigest())
        for name in ('bio1.dat', 'bio2.dat', 'bio3d.idx')))
    monkeypatch.setattr(launcher, 'profiles', lambda: (profile,))
    before = {path: path.read_bytes() for path in root.rglob('*') if path.is_file()}
    app = launcher.Launcher(window, library=tmp_path / 'missing collection')
    try:
        app.view.animate.set(False)
        assert 'Open Installation' in app.status.get()
        app.launch()  # A first-run Play explains how to supply missing files.
        assert app.child is None
        dialog = app.view.installation_window
        assert dialog is not None
        guide = app.view.installation_guide.get('1.0', 'end')
        assert 'not included or downloaded' in guide
        assert 'bio3d.mod' in guide and 'bio3d.idx' in guide
        assert 'used in place' in guide and 'later campaign packs' in guide
        assert 'missing' in app.installation_status.get()
        app.view.installation_button.command()
        assert app.view.installation_window is dialog

        monkeypatch.setattr('bio3dpc.bathysphere.filedialog.askdirectory', lambda **kwargs: str(root))
        app.view.browse_buttons[0].invoke()
        assert Path(app.mod.get()) == mod and Path(app.data.get()) == data
        app.view.verify_button.invoke()
        assert 'Verified:' in app.installation_status.get()
        assert 'Ready to launch' in app.status.get()

        (data / 'bio1.dat').write_bytes(b'other handset')
        app.view.verify_button.invoke()
        assert 'does not match this original build' in app.installation_status.get()
        (data / 'bio1.dat').write_bytes(before[data / 'bio1.dat'])
        (data / 'bio3d.idx').unlink()
        app.view.verify_button.invoke()
        assert 'bio3d.idx does not match' in app.installation_status.get()
        (data / 'bio3d.idx').write_bytes(before[data / 'bio3d.idx'])
        window.update()
        dialog.tk.call(dialog.protocol('WM_DELETE_WINDOW'))
        assert app.view.installation_window is None
        saved = launcher.Preferences.load()
        assert saved.library == str(root)
        assert saved.paths['vx8350'] == {'mod': str(mod), 'data': str(data)}
        assert {path: path.read_bytes() for path in root.rglob('*') if path.is_file()} == before
    finally:
        app.close()

    # An explicit collection overrides remembered paths from a previous installation.
    monkeypatch.setenv('BIO3DPC_LIBRARY_DIR', str(tmp_path / 'another collection'))
    window = tk.Tk()
    app = launcher.Launcher(window)
    try:
        assert app.library.get() == str(tmp_path / 'another collection')
        assert Path(app.mod.get()) == profile.mod_path(tmp_path / 'another collection')
        assert 'Open Installation' in app.status.get()
    finally:
        app.close()


def test_controls_dialog_rebinds_resets_validates_and_saves(monkeypatch, tmp_path):
    import tkinter as tk
    from bio3dpc.input_settings import ControlSettings

    try:
        window = tk.Tk()
    except tk.TclError:
        if os.environ.get('BIO3DPC_REQUIRE_TK'):
            raise
        pytest.skip('No graphical display')
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    app = launcher.Launcher(window, library=tmp_path / 'collection')
    errors = []
    window.report_callback_exception = lambda *args: errors.append(args)
    messages = []
    monkeypatch.setattr('tkinter.messagebox.showerror', lambda *args, **kwargs: messages.append(args))
    try:
        app.view.animate.set(False)
        app.view.controls_button.invoke()
        view = app.view
        dialog = view.settings_window
        assert view.settings_notebook.select() == str(view.controls_tab)
        dialog.geometry('620x460')
        window.update()
        view.sensitivity_slider.set(.3)
        assert float(app.mouse_sensitivity.get()) == .3
        app.mouse_sensitivity.set('0.25')
        assert view.sensitivity_slider.get() == .25
        assert app.mouse_sensitivity.get() == '0.25'
        view.control_binding_buttons['forward'].invoke()
        window.update()
        prompt = view.binding_capture
        prompt.event_generate('<KeyPress>', keysym='s')  # Already assigned.
        window.update()
        assert prompt.winfo_exists()
        assert app.key_bindings['forward'].get() == 'w'
        prompt.event_generate('<KeyPress>', keysym='J')
        window.update()
        assert not prompt.winfo_exists()
        assert app.key_bindings['forward'].get() == 'j'
        assert view.control_binding_buttons['forward'].cget('text') == 'J'

        view.control_binding_buttons['heal'].invoke()
        window.update()
        view.binding_capture.event_generate('<KeyPress>', keysym='BackSpace')
        window.update()
        assert app.key_bindings['heal'].get() == ''
        view.reset_controls_button.invoke()
        assert {name: var.get() for name, var in app.key_bindings.items()} == ControlSettings().bindings
        assert view.sensitivity_slider.get() == ControlSettings().sensitivity

        # Focus scrolls controls into view even at the smallest settings size.
        view.reset_controls_button.focus_force()
        window.update()
        canvas = view.controls_canvas
        assert canvas.winfo_rooty() <= view.reset_controls_button.winfo_rooty()
        assert (view.reset_controls_button.winfo_rooty() + view.reset_controls_button.winfo_height()
                <= canvas.winfo_rooty() + canvas.winfo_height() + 2)

        close = dialog.protocol('WM_DELETE_WINDOW')
        app.mouse_sensitivity.set('nan')
        dialog.tk.call(close)
        assert messages and view.settings_window is dialog
        app.mouse_sensitivity.set('2.4')
        app.mouse_look.set(False)
        app.invert_y.set(True)
        app.key_bindings['forward'].set('j')
        dialog.tk.call(close)
        assert view.settings_window is None
        saved = launcher.Preferences.load().controls
        assert not saved.mouse_look and saved.sensitivity == 2.4 and saved.invert_y
        assert saved.bindings['forward'] == 'j'
        assert not app.key_bindings['forward'].trace_info()
        assert not app.mouse_sensitivity.trace_info()
        # Reopening uses the saved values and attaches exactly one set of callbacks.
        view.settings()
        window.update()
        assert view.control_binding_buttons['forward'].cget('text') == 'J'
        assert len(app.key_bindings['forward'].trace_info()) == 1
        assert not errors
    finally:
        app.mouse_sensitivity.set('1.0')
        app.close()
