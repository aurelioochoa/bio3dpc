#!/usr/bin/env python3
"""Drive the game automatically: press a key whenever the screen settles.

The game's screens are driven by wall-clock time, so a fixed script keeps
breaking; this tool watches the framebuffer instead.  Whenever the picture has
been unchanged for ``--stable`` seconds it presses the next key of the plan
(repeating the last one) and writes a screenshot, so a run doubles as a
screenshot log of the game's state machine.

Usage: auto_play.py --plan SELECT,SELECT,SELECT,UP,UP --stable 2 --budget 300
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import time

sys.path.insert(0, ".")
sys.path.insert(0, "tools")

from boot_check_paths import DATA_DIR, GAME_DIR  # noqa: E402
from screenshot import dump_bitmap  # noqa: E402

from bio3dpc.boot import Boot  # noqa: E402

from key_test import KEYS  # noqa: E402


def screen_signature(runtime) -> tuple[int, str]:
    """(non-black pixel count, hash) of the current frame."""
    snapshot = runtime.snapshot_rgb()
    if not snapshot:
        return 0, "empty"
    rgb = snapshot[2]
    nonblack = 0
    black = b"\x00\x00\x00"
    step = 3
    for offset in range(0, len(rgb), step * 8):
        if rgb[offset:offset + step] != black:
            nonblack += 8
    return nonblack, hashlib.sha1(rgb).hexdigest()[:12]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", default="SELECT",
                        help="comma-separated keys pressed on each settle")
    parser.add_argument("--stable", type=float, default=2.0,
                        help="seconds the screen must be unchanged to press")
    parser.add_argument("--budget", type=float, default=300.0)
    parser.add_argument("--warmup", type=float, default=2.0)
    parser.add_argument("--slice", type=float, default=0.25)
    parser.add_argument("--hold", type=float, default=0.35,
                        help="seconds a key stays down")
    parser.add_argument("--out", default="/tmp/opencode/bio3dpc/auto")
    parser.add_argument("--level", default="warn")
    parser.add_argument("--shots", action="store_true",
                        help="also screenshot every screen change")
    args = parser.parse_args()

    plan = [name.strip().upper() for name in args.plan.split(",") if name.strip()]
    boot = Boot(f"{GAME_DIR}/bio3d.mod", data_dir=DATA_DIR, level=args.level)
    start = time.monotonic()
    last = None
    stable = 0.0
    pressed = 0
    shots = 0
    try:
        boot.load_module()
        boot.create_applet()
        boot.app_start()
        boot.run(seconds=args.warmup)
        while time.monotonic() - start < args.budget:
            boot.run(seconds=args.slice)
            now = time.monotonic() - start
            nonblack, digest = screen_signature(boot.runtime)
            if (nonblack, digest) == last:
                stable += args.slice
            else:
                stable = 0.0
                if args.shots and now > 5:
                    dump_bitmap(boot.runtime, f"{args.out}_{shots:03d}.png")
                    shots += 1
            last = (nonblack, digest)
            if stable < args.stable:
                continue
            name = plan[min(pressed, len(plan) - 1)]
            key = KEYS.get(name)
            pressed += 1
            print(f"--- t={now:6.1f}s nonblack={nonblack:6d} press={name}"
                  f" frames={boot.runtime.frames_presented}", flush=True)
            dump_bitmap(boot.runtime, f"{args.out}_{pressed:03d}_{name}.png")
            if key is None:
                print(f"unknown key {name!r}")
                continue
            boot.runtime.post_key(key, press=True)
            boot.run(seconds=args.hold)
            boot.runtime.post_key(key, press=False)
            boot.run(seconds=0.5)
            stable = 0.0
            last = None
        print(f"--- finished presses={pressed} shots={shots}")
    except Exception as error:  # noqa: BLE001
        print(f"{type(error).__name__}: {str(error).splitlines()[0]}")
        raise
    finally:
        boot.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
