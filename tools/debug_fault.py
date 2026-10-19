#!/usr/bin/env python3
"""Debug: boot fast, and when the machine faults, print the recent call log."""
from __future__ import annotations

import sys

sys.path.insert(0, ".")
sys.path.insert(0, "tools")

from boot_check_paths import GAME_DIR  # noqa: E402

from bio3dpc.boot import Boot  # noqa: E402


def main() -> int:
    boot = Boot(f"{GAME_DIR}/bio3d.mod", data_dir=GAME_DIR, level="warn")
    boot.load_module()
    boot.create_applet()
    boot.app_start()
    try:
        reason = boot.run(seconds=20.0)
        print(f"run returned: {reason}")
    except Exception as error:  # noqa: BLE001
        print(f"{type(error).__name__}: {error}")
    print("--- last trap calls (oldest first) ---")
    for call in boot.m.recent_calls:
        print(f"  {call}")
    print("--- helper counters ---")
    for key, count in sorted(boot.m.calls.items(), key=lambda kv: -kv[1])[:25]:
        print(f"{count:10d}  {key}")
    print("--- unsupported helpers ---")
    for key, count in sorted(boot.helper.unsupported_seen.items(),
                             key=lambda kv: -kv[1]):
        print(f"{count:10d}  helper::{key}")
    boot.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
