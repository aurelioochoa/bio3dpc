#!/usr/bin/env python3
"""Smoke test for the tkinter frontend: window, frames, key delivery.

Drives the real event loop (``Frontend.run``) and scripts keys with Tk timers,
so it exercises exactly what ``tools/play.py`` does.  Prints a summary and
exits non-zero when no frame was drawn or no key reached the guest.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import time

sys.path.insert(0, ".")
sys.path.insert(0, "tools")

from boot_check_paths import DATA_DIR, GAME_DIR  # noqa: E402

from bio3dpc.boot import Boot  # noqa: E402
from bio3dpc.frontend import Frontend  # noqa: E402


def screen_hash(runtime) -> str:
    snapshot = runtime.snapshot_rgb()
    return hashlib.sha1(snapshot[2]).hexdigest()[:10] if snapshot else "-"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--warmup", type=float, default=3.0)
    parser.add_argument("--seconds", type=float, default=12.0,
                        help="total seconds the window stays open")
    parser.add_argument("--keysym", default="Return")
    parser.add_argument("--scale", type=int, default=2)
    parser.add_argument("--level", default="warn")
    args = parser.parse_args()

    boot = Boot(f"{GAME_DIR}/bio3d.mod", data_dir=DATA_DIR, level=args.level)
    boot.load_module()
    boot.create_applet()
    boot.app_start()
    boot.run(seconds=args.warmup)

    keys: list[tuple] = []
    frontend = Frontend(boot, scale=args.scale, mouse_look=False,
                        on_key=lambda sym, avk, press: keys.append((sym, avk, press)))
    seen: list[tuple[float, str]] = []

    def snap() -> None:
        seen.append((time.monotonic(), screen_hash(boot.runtime)))

    def press() -> None:
        frontend.root.focus_force()  # synthetic keys need the input focus
        frontend.root.event_generate("<KeyPress>", keysym=args.keysym)

    def release() -> None:
        frontend.root.focus_force()
        frontend.root.event_generate("<KeyRelease>", keysym=args.keysym)

    def close() -> None:
        frontend._on_close()

    schedule = frontend.root.after
    schedule(50, snap)
    schedule(int((args.warmup + 1.0) * 1000), press)
    schedule(int((args.warmup + 1.4) * 1000), release)
    schedule(int((args.warmup + 2.5) * 1000), snap)
    schedule(int(args.seconds * 1000), close)
    frontend.run()

    before = seen[0][1] if seen else "-"
    after = seen[-1][1] if seen else "-"
    print(f"frames_drawn={frontend.frames_drawn} "
          f"frames_presented={boot.runtime.frames_presented} "
          f"draw={frontend.draw_time * 1000:.1f}ms error={frontend.error}")
    print(f"keys={keys}")
    print(f"screen {before} -> {after} ({'changed' if before != after else 'same'})")
    boot.shutdown()
    ok = frontend.frames_drawn > 0 and any(k[2] for k in keys)
    print("OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
