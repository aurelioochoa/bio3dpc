#!/usr/bin/env python3
"""Debug: run N seconds, then print trap/helper call histograms and the tail."""
from __future__ import annotations

import argparse
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "tools")

from boot_check_paths import DATA_DIR, GAME_DIR  # noqa: E402

from bio3dpc.boot import Boot  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=10.0)
    args = parser.parse_args()

    boot = Boot(f"{GAME_DIR}/bio3d.mod", data_dir=DATA_DIR, level="warn")
    boot.load_module()
    boot.create_applet()
    boot.app_start()
    try:
        reason = boot.run(seconds=args.seconds)
        print(f"run returned: {reason}")
    except Exception as error:  # noqa: BLE001
        print(f"FAULT: {type(error).__name__}: {str(error).splitlines()[0]}")
    print("--- last trap calls (oldest first) ---")
    for call in boot.m.recent_calls:
        print(f"  {call}")
    print("--- trap call counts (top 40) ---")
    for key, count in sorted(boot.m.calls.items(), key=lambda kv: -kv[1])[:40]:
        print(f"{count:10d}  {key}")
    print("--- unsupported helpers ---")
    for key, count in sorted(boot.helper.unsupported_seen.items(),
                             key=lambda kv: -kv[1]):
        print(f"{count:10d}  helper::{key}")
    print(f"--- timers pending: {len(boot.runtime.timers)} ---")
    for key, timer in list(boot.runtime.timers.items())[:10]:
        print(f"  {key} pfn=0x{timer['pfn']:x} due_in={timer['due'] * 0:.3f}")
    boot.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
