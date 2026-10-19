#!/usr/bin/env python3
"""Profile a steady-state slice of the emulation (finds Python-side hotspots)."""
from __future__ import annotations

import argparse
import cProfile
import pstats
import sys
import time

sys.path.insert(0, ".")
sys.path.insert(0, "tools")

from boot_check_paths import DATA_DIR, GAME_DIR  # noqa: E402

from bio3dpc.boot import Boot  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--warmup", type=float, default=40.0)
    parser.add_argument("--seconds", type=float, default=10.0)
    parser.add_argument("--level", default="warn")
    args = parser.parse_args()

    boot = Boot(f"{GAME_DIR}/bio3d.mod", data_dir=DATA_DIR, level=args.level)
    boot.load_module()
    boot.create_applet()
    boot.app_start()
    boot.run(seconds=args.warmup)
    frames0 = boot.runtime.frames_presented

    started = time.monotonic()
    profiler = cProfile.Profile()
    profiler.enable()
    boot.run(seconds=args.seconds)
    profiler.disable()
    wall = time.monotonic() - started
    frames = boot.runtime.frames_presented - frames0
    print(f"wall={wall:.2f}s frames={frames} fps={frames / wall:.2f}")
    stats = pstats.Stats(profiler)
    stats.sort_stats("cumulative").print_stats(24)
    boot.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
