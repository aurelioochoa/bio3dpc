#!/usr/bin/env python3
"""Send keys to the game after the splash screens and screenshot the result.

Usage: key_test.py --at 90 --keys SELECT,SOFT1,SOFT2,5
"""
from __future__ import annotations

import argparse
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "tools")

from boot_check_paths import DATA_DIR, GAME_DIR  # noqa: E402
from screenshot import dump_bitmap  # noqa: E402

from bio3dpc.boot import Boot  # noqa: E402
from bio3dpc.services import (  # noqa: E402
    AVK_0, AVK_1, AVK_2, AVK_3, AVK_4, AVK_5, AVK_6, AVK_7, AVK_8, AVK_9,
    AVK_CLR, AVK_DOWN, AVK_END, AVK_FUNCTION1, AVK_FUNCTION2, AVK_FUNCTION3,
    AVK_LEFT, AVK_POWER, AVK_POUND, AVK_RIGHT, AVK_SELECT, AVK_SEND, AVK_SOFT1,
    AVK_SOFT2, AVK_SOFT3, AVK_SOFT4, AVK_STAR, AVK_UP)

KEYS = {
    "0": AVK_0, "1": AVK_1, "2": AVK_2, "3": AVK_3, "4": AVK_4, "5": AVK_5,
    "6": AVK_6, "7": AVK_7, "8": AVK_8, "9": AVK_9,
    "STAR": AVK_STAR, "POUND": AVK_POUND, "POWER": AVK_POWER, "END": AVK_END,
    "SEND": AVK_SEND, "CLR": AVK_CLR, "UP": AVK_UP, "DOWN": AVK_DOWN,
    "LEFT": AVK_LEFT, "RIGHT": AVK_RIGHT, "SELECT": AVK_SELECT,
    "SOFT1": AVK_SOFT1, "SOFT2": AVK_SOFT2, "SOFT3": AVK_SOFT3,
    "SOFT4": AVK_SOFT4, "FUNCTION1": AVK_FUNCTION1,
    "FUNCTION2": AVK_FUNCTION2, "FUNCTION3": AVK_FUNCTION3,
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--at", type=float, default=90.0,
                        help="seconds to run before the first key")
    parser.add_argument("--keys", default="SELECT")
    parser.add_argument("--settle", type=float, default=3.0,
                        help="seconds to run after each key")
    parser.add_argument("--out", default="/tmp/opencode/bio3dpc/key")
    parser.add_argument("--level", default="info")
    parser.add_argument("--press-only", action="store_true")
    args = parser.parse_args()

    boot = Boot(f"{GAME_DIR}/bio3d.mod", data_dir=DATA_DIR, level=args.level)
    try:
        boot.load_module()
        boot.create_applet()
        boot.app_start()
        boot.run(seconds=args.at)
        print(f"--- t={args.at:.0f}s (before keys) frames={boot.runtime.frames_presented}")
        dump_bitmap(boot.runtime, f"{args.out}_before.png")
        for index, name in enumerate(args.keys.split(",")):
            name = name.strip().upper()
            if name in ("WAIT", "SHOT"):
                print(f"--- wait {args.settle:.1f}s frames={boot.runtime.frames_presented}")
                boot.run(seconds=args.settle)
                dump_bitmap(boot.runtime, f"{args.out}_{index:02d}_{name}.png")
                continue
            key = KEYS.get(name)
            if key is None:
                print(f"unknown key {name!r}")
                continue
            print(f"--- send {name} (0x{key:x}) frames={boot.runtime.frames_presented}")
            boot.runtime.post_key(key, press=True)
            boot.run(seconds=args.settle)
            dump_bitmap(boot.runtime, f"{args.out}_{index:02d}_{name}.png")
            if not args.press_only:
                boot.runtime.post_key(key, press=False)
                boot.run(seconds=1.0)
    except Exception as error:  # noqa: BLE001
        print(f"{type(error).__name__}: {str(error).splitlines()[0]}")
    finally:
        boot.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
