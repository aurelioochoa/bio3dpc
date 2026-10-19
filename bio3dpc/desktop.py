"""Install or remove the per-user BioShock 3D desktop launcher."""
from __future__ import annotations

import argparse
import os
import shlex
import shutil
from pathlib import Path

from .launcher import ROOT, xdg_path

APP_ID = 'bio3dpc'


def entry_value(value):
    return str(value).replace('\\', '\\\\').replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')


def exec_value(value):
    value = str(value).replace('%', '%%')
    for character in ('\\', '"', '`', '$'):
        value = value.replace(character, '\\' + character)
    return entry_value('"' + value + '"')


def locations():
    return (Path.home() / '.local' / 'bin' / APP_ID,
            xdg_path('XDG_DATA_HOME', '.local/share') / 'applications' / (APP_ID + '.desktop'))


def icon_path():
    return xdg_path('XDG_DATA_HOME', '.local/share') / APP_ID / 'icon.png'


def install(root=ROOT):
    root = Path(root).resolve()
    source, python = root / 'bio3dpc.sh', root / '.venv/bin/python'
    if not all(path.is_file() and os.access(path, os.X_OK) for path in (source, python)):
        raise ValueError('Create the project .venv and make bio3dpc.sh executable before installing')
    launcher, entry = locations()
    source_icon = root / 'bio3dpc/ui_assets/icon.png'
    icon = icon_path()
    wrapper = '#!/bin/sh\n# Installed by BioShock 3D preservation launcher.\nexec ' + shlex.quote(str(source)) + ' "$@"\n'
    contents = '\n'.join((
        '[Desktop Entry]', 'Version=1.0', 'Type=Application', 'Name=BioShock 3D',
        'Comment=Play preserved portrait and landscape mobile builds',
        'Exec=' + exec_value(launcher), 'TryExec=' + entry_value(launcher),
        'Icon=' + entry_value(icon if source_icon.is_file() else 'applications-games'),
        'Terminal=false', 'Categories=Game;', 'StartupWMClass=Bio3dpc',
        'Keywords=BioShock;BREW;Preservation;', 'StartupNotify=false', '',
    ))
    if source_icon.is_file():
        icon.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_icon, icon)
    for path, content, mode in ((launcher, wrapper, 0o755), (entry, contents, 0o644)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
        path.chmod(mode)
    return launcher, entry


def uninstall():
    paths = locations()
    for path in (*paths, icon_path()):
        path.unlink(missing_ok=True)
    return paths


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--uninstall', action='store_true', help='Remove menu entry and command; preserve games, saves and preferences')
    args = parser.parse_args(argv)
    try:
        paths = uninstall() if args.uninstall else install()
        for path in paths:
            print(('Removed: ' if args.uninstall else 'Installed: ') + str(path))
    except (OSError, ValueError) as error:
        parser.exit(1, 'Desktop setup: ' + str(error) + '\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
