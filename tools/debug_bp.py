#!/usr/bin/env python3
"""Debug: watch a module offset; log registers when executed."""
from __future__ import annotations

import argparse
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "tools")

from boot_check_paths import GAME_DIR  # noqa: E402
from unicorn import UC_HOOK_CODE  # noqa: E402

from bio3dpc.boot import Boot  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("offsets", nargs="+", help="module-relative hex offsets")
    parser.add_argument("--seconds", type=float, default=20.0)
    args = parser.parse_args()
    offsets = {int(value, 16) for value in args.offsets}

    boot = Boot(f"{GAME_DIR}/bio3d.mod", data_dir=GAME_DIR, level="warn")
    boot.load_module()
    boot.create_applet()
    boot.app_start()

    m = boot.m
    uc = m.uc
    load_base = m.load_base
    watch = {load_base + offset for offset in offsets}
    from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1,
                                   UC_ARM_REG_R2, UC_ARM_REG_R3,
                                   UC_ARM_REG_R4, UC_ARM_REG_R6,
                                   UC_ARM_REG_R12, UC_ARM_REG_LR,
                                   UC_ARM_REG_PC)

    count = 0

    def on_code(uc_, address, size, user):
        nonlocal count
        if address not in watch:
            return
        count += 1
        regs = uc_.reg_read_batch([UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
                                   UC_ARM_REG_R3, UC_ARM_REG_R4, UC_ARM_REG_R6,
                                   UC_ARM_REG_R12, UC_ARM_REG_LR,
                                   UC_ARM_REG_PC])
        names = ["r0", "r1", "r2", "r3", "r4", "r6", "ip", "lr", "pc"]
        if count <= 40:
            print(f"[{count}] +0x{address - load_base:06x} " + " ".join(
                f"{n}=0x{v:08x}" for n, v in zip(names, regs)))
            # follow the vtable chain used by the bx ip call
            try:
                obj = m.u32(m.uc.reg_read(UC_ARM_REG_R4) + 0x1c)
                if obj and m.contains(obj, 4):
                    vtbl = m.u32(obj)
                    slot = m.u32(vtbl + 0x14) if m.contains(vtbl + 0x14, 4) else 0
                    print(f"      [r4+0x1c]=0x{obj:08x} [obj]=0x{vtbl:08x} [obj]+0x14=0x{slot:08x}")
            except Exception as error:  # noqa: BLE001
                print(f"      chain failed: {error}")

    uc.hook_add(UC_HOOK_CODE, on_code, begin=min(watch), end=max(watch) + 4)
    try:
        reason = boot.run(seconds=args.seconds)
        print(f"run returned: {reason}")
    except Exception as error:  # noqa: BLE001
        print(f"FAULT: {type(error).__name__}: {str(error).splitlines()[0]}")
        print("--- last trap calls ---")
        for call in boot.m.recent_calls[-12:]:
            print(f"  {call}")
    boot.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
