#!/usr/bin/env python3
"""Disassemble an address range of the guest image (load_base + file offset).

Usage: disasm.py <offset-hex> [length-bytes] [--mod PATH]
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, "tools")

from boot_check_paths import GAME_DIR  # noqa: E402
from capstone import CS_ARCH_ARM, CS_MODE_ARM, Cs

DEFAULT_MOD = os.path.join(GAME_DIR, "bio3d.mod")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("offset", help="file offset / load_base-relative addr, hex")
    parser.add_argument("length", nargs="?", default="0x100")
    parser.add_argument("--mod", default=DEFAULT_MOD)
    args = parser.parse_args()

    offset = int(args.offset, 16)
    length = int(args.length, 16)
    data = open(args.mod, "rb").read()
    window = data[offset:offset + length]

    md = Cs(CS_ARCH_ARM, CS_MODE_ARM)
    for insn in md.disasm(window, offset):
        mark = " "
        print(f"{mark}{insn.address:08x}: {insn.mnemonic:7s} {insn.op_str}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
