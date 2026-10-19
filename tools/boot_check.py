#!/usr/bin/env python3
"""Boot the target build and report how far it gets (development helper)."""
from __future__ import annotations

import argparse
import sys
import time

sys.path.insert(0, ".")
sys.path.insert(0, "tools")

from boot_check_paths import DATA_DIR, GAME_DIR  # noqa: E402

from bio3dpc.boot import Boot  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=10.0)
    parser.add_argument("--trace", default=None, help="JSONL trace output path")
    parser.add_argument("--level", default="debug")
    parser.add_argument("--debug", default=None, help="host log file")
    args = parser.parse_args()

    boot = Boot(f"{GAME_DIR}/bio3d.mod", data_dir=DATA_DIR, level=args.level,
                trace_path=args.trace)
    t0 = time.monotonic()
    try:
        boot.load_module()
        boot.create_applet()
        boot.app_start()
        reason = boot.run(seconds=args.seconds)
        print(f"RESULT: stop={reason} in {time.monotonic() - t0:.1f}s")
    except Exception as error:  # noqa: BLE001
        print(f"RESULT: FAULT after {time.monotonic() - t0:.1f}s")
        print(f"{type(error).__name__}: {error}")
    finally:
        boot.shutdown()
    stats = boot.runtime.stats.calls
    print("--- service call counts (top 40) ---")
    for key, count in sorted(stats.items(), key=lambda kv: -kv[1])[:40]:
        print(f"{count:8d}  {key}")
    unsupported = boot.helper.unsupported_seen
    if unsupported:
        print("--- unimplemented helpers ---")
        for key, count in sorted(unsupported.items(), key=lambda kv: -kv[1]):
            print(f"{count:8d}  helper::{key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
