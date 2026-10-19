#!/usr/bin/env python3
"""Debug: log CreateInstance call sites (guest LR) and module vtable calls."""
from __future__ import annotations

import sys

sys.path.insert(0, ".")
sys.path.insert(0, "tools")

from boot_check_paths import GAME_DIR  # noqa: E402
from unicorn.arm_const import UC_ARM_REG_LR  # noqa: E402

from bio3dpc.boot import Boot  # noqa: E402


def main() -> int:
    boot = Boot(f"{GAME_DIR}/bio3d.mod", data_dir=GAME_DIR, level="warn")
    boot.load_module()
    boot.create_applet()
    boot.app_start()

    m = boot.m
    runtime = boot.runtime
    original = runtime._shell_create_instance

    def wrapper(machine, this, cls, ppobj):
        lr = m.uc.reg_read(UC_ARM_REG_LR)
        ret = original(machine, this, cls, ppobj)
        obj = m.u32(ppobj) if ppobj else 0
        where = (f"+0x{lr - m.load_base:06x}" if lr >= m.load_base
                 else f"0x{lr:08x}")
        print(f"CreateInstance cls=0x{cls:08x} -> rc=0x{ret:x} obj=0x{obj:08x} lr={where}")
        return ret

    runtime._shell_create_instance = wrapper
    # The shell vtable slot 2 is bound to the original method; rebind it.
    vtable = m.u32(runtime.shell)
    slot = m.bind("IShell::CreateInstance", wrapper, nargs=3)
    m.w32(vtable + 2 * 4, slot)

    try:
        reason = boot.run(seconds=20.0)
        print(f"run returned: {reason}")
    except Exception as error:  # noqa: BLE001
        print(f"FAULT: {type(error).__name__}: {str(error).splitlines()[0]}")
    boot.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
