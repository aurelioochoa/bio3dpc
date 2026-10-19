#!/usr/bin/env python3
"""Locate and disassemble IApplet::HandleEvent of the running game.

Prints the module offset so it can be fed to tools/disasm.py for longer views.
"""
from __future__ import annotations

import argparse
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "tools")

from boot_check_paths import DATA_DIR, GAME_DIR  # noqa: E402

from bio3dpc.boot import Boot  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--length", type=lambda v: int(v, 0), default=0x180)
    parser.add_argument("--level", default="error")
    parser.add_argument("--list", action="store_true",
                        help="list all applet vtable entries instead")
    args = parser.parse_args()

    boot = Boot(f"{GAME_DIR}/bio3d.mod", data_dir=DATA_DIR, level=args.level)
    boot.load_module()
    applet = boot.create_applet()
    m = boot.m
    base = m.load_base
    vtable = m.u32(applet)
    print(f"applet=0x{applet:x} vtable=0x{vtable:x} base=0x{base:x}")
    for index in range(8):
        entry = m.u32(vtable + 4 * index)
        print(f"  vtable[{index}] = 0x{entry:08x} (offset 0x{entry - base:x})")
    handler = m.u32(vtable + 8)
    print(f"HandleEvent offset = 0x{handler - base:x}")
    if not args.list:
        from capstone import CS_ARCH_ARM, CS_MODE_ARM, Cs
        data = open(boot.mod_path, "rb").read()
        offset = handler - base
        window = data[offset:offset + args.length]
        md = Cs(CS_ARCH_ARM, CS_MODE_ARM)
        for insn in md.disasm(window, offset):
            print(f" {insn.address:08x}: {insn.mnemonic:7s} {insn.op_str}")
    boot.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
