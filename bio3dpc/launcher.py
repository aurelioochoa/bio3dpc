"""Native preservation launcher. Original modules and game data stay in place."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .input_settings import ControlSettings

ROOT = Path(__file__).resolve().parent.parent


def xdg_path(variable: str, fallback: str) -> Path:
    value = Path(os.environ.get(variable, '')).expanduser()
    return value if value.is_absolute() else Path.home() / fallback


def settings_path() -> Path:
    return xdg_path('XDG_CONFIG_HOME', '.config') / 'bio3dpc' / 'launcher.json'


def log_path() -> Path:
    return xdg_path('XDG_STATE_HOME', '.local/state') / 'bio3dpc' / 'game.log'


@dataclass
class Preferences:
    profile: str = 'vx8350'
    library: str = ''
    scale: int = 3
    audio: bool = True
    paths: dict = field(default_factory=dict)
    animation: bool = True
    controls: ControlSettings = field(default_factory=ControlSettings)

    @classmethod
    def load(cls, path: Path | None = None) -> 'Preferences':
        path = path or settings_path()
        if not path.exists():
            return cls()
        value = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(value, dict):
            raise ValueError('Launcher settings must be a JSON object')
        scale = value.get('scale', 3)
        if type(scale) is not int or not 1 <= scale <= 6:
            raise ValueError('Window scale must be between 1 and 6')
        paths = value.get('paths', {})
        if not isinstance(paths, dict) or any(
            not isinstance(key, str) or not isinstance(item, dict)
            or any(name not in ('mod', 'data') or not isinstance(text, str)
                   for name, text in item.items()) for key, item in paths.items()
        ):
            raise ValueError('Invalid saved game paths')
        profile, library, audio = value.get('profile', 'vx8350'), value.get('library', ''), value.get('audio', True)
        animation = value.get('animation', True)
        if (not isinstance(profile, str) or not isinstance(library, str) or
                type(audio) is not bool or type(animation) is not bool):
            raise ValueError('Invalid launcher preferences')
        controls = ControlSettings.from_dict(value.get('controls', {}))
        from .profiles import relocated_local_path
        library = relocated_local_path(library)
        paths = {key: {name: relocated_local_path(text) for name, text in item.items()}
                 for key, item in paths.items()}
        return cls(profile, library, scale, audio, paths, animation, controls)

    def save(self, path: Path | None = None) -> None:
        path = path or settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        value = dict(self.__dict__, controls=ControlSettings.from_dict(self.controls.to_dict()).to_dict())
        temporary.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
        temporary.replace(path)


def profiles():
    # The launcher itself can open before the runtime or profiles are installed.
    try:
        from .profiles import PROFILES
    except ImportError:
        return ()
    return PROFILES


def default_library() -> Path:
    try:
        from .profiles import discover_root
        return Path(discover_root())
    except ImportError:
        return Path(os.environ.get('BIO3DPC_LIBRARY_DIR', ROOT / 'bioshock3d_game_files'))


def validate_paths(mod: Path, data: Path, profile=None) -> list[str]:
    problems = []
    if not mod.is_file():
        problems.append('Original game module is missing: ' + str(mod))
    if not data.is_dir():
        problems.append('Game data directory is missing: ' + str(data))
    else:
        available = {path.name.lower() for path in data.iterdir() if path.is_file()}
        if not any(name.endswith('.dat') and name != 'savedata.dat' for name in available):
            problems.append('No original .DAT game assets found in: ' + str(data))
        elif profile is not None:
            count = {'vx8350': 7, 'n7705': 2}.get(profile.id, 0)
            missing = [f'bio{index}.dat' for index in range(1, count + 1) if f'bio{index}.dat' not in available]
            if missing:
                problems.append('Missing original game assets: ' + ', '.join(missing))
    if mod.is_file() and profile is not None:
        import hashlib
        if hashlib.sha256(mod.read_bytes()).hexdigest() != profile.sha256:
            problems.append('Module does not match the selected original build. Choose its matching build.')
        if data.is_dir():
            files = {path.name.lower(): path for path in data.iterdir() if path.is_file()}
            for name, expected in getattr(profile, 'asset_hashes', ()):
                path = files.get(name)
                if path is None or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                    problems.append(f'{name} does not match this original build. Choose its matching data folder.')
                    break
    return problems


def child_command(mod: Path, data: Path, scale: int, profile=None, controls=None) -> list[str]:
    command = [sys.executable, '-u', str(ROOT / 'tools' / 'play.py'),
               '--mod', str(mod.resolve()), '--data-dir', str(data.resolve()), '--scale', str(scale)]
    if profile is not None:
        command += ['--width', str(profile.width), '--height', str(profile.height),
                    '--app-clsid', hex(profile.app_clsid)]
    if controls is not None:
        command += ['--controls-json', json.dumps(controls.to_dict())]
    return command


def start_game(mod: Path, data: Path, scale: int, audio: bool, profile=None, controls=None):
    """Return immediately; use a regular log file so no output pipe can block."""
    target = log_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment['BIO3DPC_AUDIO'] = '1' if audio else '0'
    with target.open('w', encoding='utf-8') as output:
        return subprocess.Popen(child_command(mod, data, scale, profile, controls), cwd=ROOT,
                                env=environment, stdout=output, stderr=subprocess.STDOUT)


class Launcher:
    def __init__(self, window, *, library=None, profile=None, mod=None, data=None):
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.ttk, self.window = tk, ttk, window
        self.child = None
        self.tick_job = None
        self.options = list(profiles())
        try:
            self.preferences = Preferences.load()
            settings_error = ''
        except (ValueError, OSError) as error:
            self.preferences = Preferences()
            settings_error = 'Settings could not be loaded: ' + str(error)
        preferences = self.preferences
        explicit_library = library or os.environ.get('BIO3DPC_LIBRARY_DIR')
        self.library = tk.StringVar(value=str(explicit_library or preferences.library or default_library()))
        self.scale = tk.IntVar(value=preferences.scale)
        self.audio = tk.BooleanVar(value=preferences.audio)
        self.mouse_look = tk.BooleanVar(value=preferences.controls.mouse_look)
        self.mouse_sensitivity = tk.StringVar(value=str(preferences.controls.sensitivity))
        self.invert_y = tk.BooleanVar(value=preferences.controls.invert_y)
        self.key_bindings = {action: tk.StringVar(value=key) for action, key in preferences.controls.bindings.items()}
        self.mod = tk.StringVar()
        self.data = tk.StringVar()
        self.status = tk.StringVar()
        self.installation_status = tk.StringVar()
        self.detail = tk.StringVar()
        selected = profile or preferences.profile
        self.selected = next((item for item in self.options if item.id == selected), self.options[0] if self.options else None)
        self.build = tk.StringVar(value=self.selected.title if self.selected else 'Custom original module')
        self._layout()
        self.select_build(reset=bool(explicit_library))
        if mod is not None:
            self.mod.set(str(mod))
        if data is not None:
            self.data.set(str(data))
        self.refresh_status()
        if settings_error:
            self.status.set(settings_error)
        window.protocol('WM_DELETE_WINDOW', self.close)
        self.tick_job = window.after(150, self.tick)

    def _layout(self):
        from .bathysphere import BathysphereView
        self.view = BathysphereView(self)

    def select_build(self, event=None, *, reset=False):
        if self.selected is not None and self.mod.get() and self.data.get():
            self.preferences.paths[self.selected.id] = {'mod': self.mod.get(), 'data': self.data.get()}
        self.selected = next((item for item in self.options if item.title == self.build.get()), self.selected)
        profile = self.selected
        if profile is None:
            self.detail.set('Choose an original module and its matching data folder.')
            return
        saved = {} if reset else self.preferences.paths.get(profile.id, {})
        library = Path(self.library.get()).expanduser()
        self.mod.set(saved.get('mod', str(profile.mod_path(library))))
        self.data.set(saved.get('data', str(profile.data_path(library))))
        orientation = 'Landscape' if profile.width > profile.height else 'Portrait'
        detail = f'{orientation} · {profile.width} × {profile.height} · original BREW binary'
        if profile.id == 'n7705':
            detail += '\nLocal landscape collection contains only the first two DAT packs; later campaign assets are unavailable.'
        self.detail.set(detail)
        self.refresh_status()

    def refresh_status(self):
        if self.child is not None:
            return
        mod, data = Path(self.mod.get()).expanduser(), Path(self.data.get()).expanduser()
        try:
            problems = validate_paths(mod, data, self.selected)
        except OSError as error:
            problems = ['Cannot read original game files: ' + str(error)]
        self.installation_status.set('\n'.join(problems) if problems else
                                     'Verified: module and starter assets match; required data packs are present.\n'
                                     'Files are used in place; keep this folder available when playing.')
        self.status.set('Game files need attention. Open Installation to select and verify your BREW files.'
                        if problems else 'Original files found. Ready to launch; saves use your per-user state folder.')

    def save(self):
        controls = ControlSettings.from_dict({
            'mouse_look': self.mouse_look.get(), 'sensitivity': float(self.mouse_sensitivity.get()),
            'invert_y': self.invert_y.get(),
            'bindings': {action: variable.get() for action, variable in self.key_bindings.items()},
        })
        preferences = self.preferences
        preferences.controls = controls
        preferences.profile = self.selected.id if self.selected else 'custom'
        preferences.library = self.library.get()
        preferences.scale = self.scale.get()
        preferences.audio = self.audio.get()
        preferences.animation = self.view.animate.get()
        preferences.paths[preferences.profile] = {'mod': self.mod.get(), 'data': self.data.get()}
        preferences.save()

    def launch(self):
        if self.child is not None:
            return
        mod, data = Path(self.mod.get()).expanduser(), Path(self.data.get()).expanduser()
        try:
            problems = validate_paths(mod, data, self.selected)
            if problems:
                self.refresh_status()
                self.view.installation()
                return
            self.save()
            self.child = start_game(mod, data, self.scale.get(), self.audio.get(), self.selected,
                                    self.preferences.controls)
        except (OSError, ValueError, self.tk.TclError) as error:
            self.status.set('Cannot start game: ' + str(error))
            return
        self.view.pause()
        self.launch_button.configure(state='disabled')
        self.status.set('Game running. Close its window to return here. Log: ' + str(log_path()))

    def tick(self):
        if self.child is not None:
            code = self.child.poll()
            if code is not None:
                self.child = None
                self.view.resume()
                self.launch_button.configure(state='normal')
                self.status.set('Game closed.' if code == 0 else f'Game exited with error {code}. Choose View log for details.')
        self.tick_job = self.window.after(150, self.tick)

    def show_log(self):
        view = self.tk.Toplevel(self.window)
        view.title('BioShock 3D · game log')
        view.geometry('800x450')
        text = self.tk.Text(view, wrap='word', background='#0c2025', foreground='#eef0e1')
        text.pack(fill='both', expand=True)
        try:
            # Keep the GUI responsive even after a verbose debugging session.
            with log_path().open('rb') as source:
                source.seek(0, 2)
                source.seek(max(0, source.tell() - 128 * 1024))
                value = source.read().decode('utf-8', errors='replace')
        except OSError as error:
            value = str(log_path()) + '\n' + str(error)
        text.insert('1.0', value)
        text.configure(state='disabled')

    def close(self):
        try:
            self.save()
        except (OSError, ValueError, self.tk.TclError) as error:
            from tkinter import messagebox
            messagebox.showerror('Cannot save launcher settings', str(error), parent=self.window)
            return
        if self.tick_job is not None:
            self.window.after_cancel(self.tick_job)
        self.view.close()
        # A running game remains independent when this launcher is closed.
        self.window.destroy()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', type=Path, help='Folder containing your original phone builds')
    parser.add_argument('--profile', choices=[profile.id for profile in profiles()], help='Original build identifier')
    parser.add_argument('--mod', type=Path, help='Override the selected original .mod file')
    parser.add_argument('--data-dir', type=Path, help='Override its matching original data directory')
    parser.add_argument('--smoke-test', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    import tkinter as tk
    try:
        window = tk.Tk(className='Bio3dpc')
        launcher = Launcher(window, library=args.library, profile=args.profile, mod=args.mod, data=args.data_dir)
        if args.smoke_test:
            window.update()
            print('BioShock 3D launcher ready:', launcher.build.get(), flush=True)
            launcher.view.close()
            window.destroy()
        else:
            window.mainloop()
    except tk.TclError as error:
        print('Cannot open BioShock 3D launcher: ' + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
