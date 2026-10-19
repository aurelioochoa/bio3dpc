#!/usr/bin/env python3
"""Play BioShock 3D (BREW) in a tkinter window.

Usage: tools/play.py [--scale 3] [--level info] [--mod PATH] [--data-dir DIR]

Default keyboard bindings (customize in launcher Settings → Controls):
    W A S D     move / strafe        mouse    turn + pitch
    E           use / pick up        LMB      fire
    R / F       reload / first aid   RMB      toggle weapon/plasmid
    1-5 / F1-F3 weapon/plasmid slots  wheel    next/previous owned slot
    Q / MMB     next ammo type       Tab      release/capture pointer
    Enter/Space confirm prompts      Esc      pause/back
    F2 / F11    skip cutscenes        keypad   original phone controls
    No separate jump, zoom, or quicksave action is mapped for this mobile build.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from boot_check_paths import DATA_DIR, GAME_DIR  # noqa: E402

from bio3dpc.boot import Boot  # noqa: E402
from bio3dpc.frontend import Frontend  # noqa: E402
from bio3dpc.profiles import PROFILES, default_save_dir, discover_root  # noqa: E402
from bio3dpc.input_settings import ControlSettings  # noqa: E402
from bio3dpc.launcher import Preferences  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--profile', choices=[profile.id for profile in PROFILES])
    parser.add_argument("--mod")
    parser.add_argument("--data-dir")
    parser.add_argument("--scale", type=int, default=3)
    parser.add_argument("--width", type=int)
    parser.add_argument("--height", type=int)
    parser.add_argument("--app-clsid", type=lambda value: int(value, 0))
    parser.add_argument("--save-dir", help="writable save overlay (default: per-build XDG state directory)")
    parser.add_argument("--level", default="info")
    parser.add_argument("--log-keys", action="store_true")
    mouse = parser.add_mutually_exclusive_group()
    mouse.add_argument("--no-mouse", dest="mouse", action="store_false", default=None,
                        help="do not map the mouse to the look keys")
    mouse.add_argument('--mouse', dest='mouse', action='store_true', help='enable mouse look')
    parser.add_argument("--mouse-sens", type=float,
                        help="mouse look speed (1.0 is the tuned default)")
    invert = parser.add_mutually_exclusive_group()
    invert.add_argument('--invert-y', dest='invert_y', action='store_true', default=None)
    invert.add_argument('--no-invert-y', dest='invert_y', action='store_false')
    parser.add_argument('--controls-json', help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        controls = (ControlSettings.from_dict(json.loads(args.controls_json)) if args.controls_json
                    else Preferences.load().controls)
        if args.mouse_sens is not None:
            ControlSettings.from_dict({'sensitivity': args.mouse_sens})
    except (ValueError, OSError) as error:
        parser.error('Cannot load controls: ' + str(error))
    profile = next((p for p in PROFILES if p.id == args.profile), None)
    args.mod = args.mod or (str(profile.mod_path(discover_root())) if profile else f'{GAME_DIR}/bio3d.mod')
    args.data_dir = args.data_dir or (str(profile.data_path(discover_root())) if profile else DATA_DIR)
    if not Path(args.mod).is_file() or not Path(args.data_dir).is_dir():
        parser.error('Choose an existing original module and its matching data folder, or open ./bio3dpc.sh')

    boot = Boot(args.mod, data_dir=args.data_dir, level=args.level,
                width=args.width, height=args.height, app_clsid=args.app_clsid,
                save_dir=args.save_dir or str(default_save_dir(args.mod)))

    print("BioShock 3D - BREW HLE port")
    print("  Keyboard bindings: launcher Settings → Controls")
    print("  LMB fire | RMB weapon/plasmid | wheel cycle | Enter confirm | Esc pause")
    print("  Tab frees/captures the pointer when mouse look is enabled")
    print("  F11 skips cutscenes | numeric keypad = phone controls | close window to quit", flush=True)

    def on_key(keysym, avk, press):
        boot.log.info(f"key {'down' if press else 'up'} {keysym} -> 0x{avk:x}")

    try:
        boot.load_module()
        boot.create_applet()
        boot.app_start()
        boot.run(seconds=1.0)
        frontend = Frontend(boot, scale=args.scale,
                            on_key=on_key if args.log_keys else None,
                            mouse_look=args.mouse, mouse_sensitivity=args.mouse_sens,
                            invert_y=args.invert_y, control_settings=controls)
        frontend.run()
    finally:
        boot.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
