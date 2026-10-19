"""Per-user installation leaves original assets and user preferences intact."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from bio3dpc import desktop, launcher


def isolated_home(monkeypatch, tmp_path):
    home = tmp_path / 'home with spaces'
    home.mkdir()
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('XDG_DATA_HOME', str(home / 'share'))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(home / 'config'))
    return home


def test_install_and_uninstall_preserve_game_and_settings(monkeypatch, tmp_path):
    home = isolated_home(monkeypatch, tmp_path)
    game = home / 'game.mod'
    game.write_bytes(b'original')
    launcher.Preferences().save()
    wrapper, entry = desktop.install()
    assert wrapper.stat().st_mode & 0o111
    assert 'Exec=' + desktop.exec_value(wrapper) in entry.read_text()
    assert 'Terminal=false' in entry.read_text()
    assert 'Categories=Game;' in entry.read_text()
    assert 'StartupWMClass=Bio3dpc' in entry.read_text()
    assert desktop.icon_path().read_bytes() == (desktop.ROOT/'bio3dpc/ui_assets/icon.png').read_bytes()
    desktop.uninstall()
    assert not wrapper.exists() and not entry.exists()
    assert not desktop.icon_path().exists()
    assert game.read_bytes() == b'original'
    assert launcher.Preferences.load() == launcher.Preferences()
    desktop.uninstall()  # Idempotent removal.


def test_wrapper_quotes_source_path_and_forwards_arguments(monkeypatch, tmp_path):
    isolated_home(monkeypatch, tmp_path)
    checkout = tmp_path / "source ' $value with spaces"
    checkout.mkdir()
    (checkout / '.venv/bin').mkdir(parents=True)
    (checkout / '.venv/bin/python').symlink_to(Path(os.sys.executable))
    script = checkout / 'bio3dpc.sh'
    script.write_text('#!/bin/sh\nprintf "<%s>\\n" "$@"\n')
    script.chmod(0o755)
    wrapper, _ = desktop.install(checkout)
    result = subprocess.run([str(wrapper), 'literal $value', 'a b'], cwd=tmp_path,
                            capture_output=True, text=True, check=True)
    assert result.stdout == '<literal $value>\n<a b>\n'


def test_installer_rejects_incomplete_checkout_before_writing(monkeypatch, tmp_path):
    isolated_home(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match='Create the project'):
        desktop.install(tmp_path / 'missing checkout')
    assert not any(path.exists() for path in desktop.locations())


def test_desktop_exec_escapes_reserved_characters():
    assert desktop.exec_value('/tmp/a%b') == '"/tmp/a%%b"'
    assert '\\\\$' in desktop.exec_value('/tmp/$variable')
    assert '\\\\`' in desktop.exec_value('/tmp/`command`')


def test_installed_wrapper_opens_real_launcher_from_another_directory(monkeypatch, tmp_path):
    import tkinter as tk
    try:
        probe = tk.Tk()
        probe.destroy()
    except tk.TclError:
        if os.environ.get('BIO3DPC_REQUIRE_TK'):
            raise
        pytest.skip('No graphical display')
    isolated_home(monkeypatch, tmp_path)
    wrapper, entry = desktop.install()
    validator = shutil.which('desktop-file-validate')
    if validator:
        subprocess.run([validator, str(entry)], check=True, capture_output=True, text=True)
    elif os.environ.get('BIO3DPC_REQUIRE_TK'):
        pytest.fail('desktop-file-validate is required by the desktop integration gate')
    result = subprocess.run([str(wrapper), '--smoke-test'], cwd=tmp_path,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert 'BioShock 3D launcher ready:' in result.stdout
    assert not launcher.settings_path().exists()  # Smoke checks have no preference side effects.
