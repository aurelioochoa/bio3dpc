#!/usr/bin/env python3
"""Boot the game and dump the device bitmap every N seconds.

Development helper: shows how the guest presentation evolves over time
(splash screens, menus) without any windowing frontend.
"""
from __future__ import annotations

import argparse
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "tools")

from boot_check_paths import DATA_DIR, GAME_DIR  # noqa: E402
from screenshot import dump_bitmap  # noqa: E402

from bio3dpc.boot import Boot  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=40.0,
                        help="total guest runtime")
    parser.add_argument("--step", type=float, default=5.0,
                        help="seconds between screenshots")
    parser.add_argument("--out", default="/tmp/opencode/bio3dpc/shot")
    parser.add_argument("--level", default="info")
    args = parser.parse_args()

    boot = Boot(f"{GAME_DIR}/bio3d.mod", data_dir=DATA_DIR, level=args.level)
    elapsed = 0.0
    index = 0
    try:
        boot.load_module()
        boot.create_applet()
        boot.app_start()
        while elapsed < args.seconds:
            reason = boot.run(seconds=args.step)
            elapsed += args.step
            print(f"--- t={elapsed:.0f}s stop={reason} "
                  f"frames={boot.runtime.frames_presented}")
            dump_bitmap(boot.runtime, f"{args.out}{index:02d}.png")
            index += 1
            if reason == "quit":
                break
    except Exception as error:  # noqa: BLE001
        print(f"{type(error).__name__}: {str(error).splitlines()[0]}")
        dump_bitmap(boot.runtime, f"{args.out}{index:02d}.png")
    finally:
        boot.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
