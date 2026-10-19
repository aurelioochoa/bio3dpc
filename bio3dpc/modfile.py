"""The ``.mod`` module image format used by Indiagames' BREW builds.

Verified by disassembling the loader shim that every build of this game ships
at file offset 0x40:

    word 0x00  ARM branch to 0x40 (cold entry, runs relocation once)
    word 0x04  ARM branch to 0x48 (warm entry, skips relocation)
    word 0x08  magic "BREW"
    word 0x10  offset of the cold entry
    word 0x14  format version (byte 0x15 must be 1)
    word 0x18  offset of AEEMod_Load (text start)
    word 0x1C  text size
    word 0x20  data size (relocation area follows text)
    word 0x24  BSS size (zeroed after relocation)
    word 0x28  relocation-table size
    word 0x34  image size (text + data)
    word 0x38  suggested heap size

The image is *position independent*: the shim derives its own load base by
adding the PC-relative address of the word at 0x18C to that word's value (the
file stores -0x18C).  A loader therefore only has to

    1. copy the file to any 4-aligned guest address B,
    2. write the helper-table version to [B-8] and the AEEHelperFuncs pointer
       to [B-4] (the shim forwards both to [text_start-8]/[text_start-4]),
    3. call B with (IShell*, ph, IModule**).

Relocation adds ``B + text_offset - image_base`` to every pointer listed in
the table stored right after the data section; the table and the relocation
engine itself live in what later becomes BSS and are erased once used.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

MAGIC = b"BREW"

# Legacy relocation engine ABI: the shim calls *(base + text_off + reloc_off)
# with (table, text_base, data_base, delta).
RELOC_TABLE_GAP = 0x130


@dataclass
class ModHeader:
    entry: int            # cold entry offset (runs relocation)
    entry_warm: int       # warm entry offset (skips relocation)
    version: int
    text_offset: int
    text_size: int
    data_size: int
    bss_size: int
    reloc_size: int
    flags: int
    image_base: int
    image_size: int
    heap_size: int
    stored_base_word: int  # value at text_offset-0x74 ... (0x18C in practice)


class ModFormatError(ValueError):
    pass


class ModFile:
    def __init__(self, path, data: bytes | None = None):
        self.path = str(path)
        self.data = data if data is not None else open(path, "rb").read()
        if len(self.data) < 0x200:
            raise ModFormatError(f"{path}: too small for a .mod image")
        if self.data[8:12] != MAGIC:
            raise ModFormatError(f"{path}: missing BREW magic")
        words = struct.unpack_from("<16I", self.data, 0)
        (self.magic_branch, self.warm_branch, _magic, _one, self.entry,
         self.version, self.text_offset, self.text_size, self.data_size,
         self.bss_size, self.reloc_size, self.flags, self.image_base,
         self.image_size, self.heap_size, self._reserved) = words
        self.entry_warm = 4
        # Self-relative base word lives at the fixed offset the shim uses.
        self.base_word_offset = 0x18C
        if self.base_word_offset + 4 <= len(self.data):
            self.stored_base_word = struct.unpack_from(
                "<I", self.data, self.base_word_offset)[0]
        else:
            self.stored_base_word = 0

    # ------------------------------------------------------------------ loading
    def load_into(self, machine, base: int | None = None) -> int:
        """Copy the image into guest memory; returns the image base."""
        m = machine
        if base is None:
            base = m.load_base
        if base + len(self.data) > m.heap_base:
            raise ModFormatError("module does not fit below the guest heap")
        m.write(base, self.data)
        m.code_start = base
        m.code_end = base + len(self.data)
        return base

    def check_position_independent(self) -> None:
        expected = (-self.base_word_offset) & 0xFFFFFFFF
        if self.stored_base_word != expected:
            raise ModFormatError(
                f"{self.path}: base word is {self.stored_base_word:#010x}, "
                f"expected {expected:#010x} (not a self-positioning image)")

    def __repr__(self) -> str:
        return (f"<ModFile {self.path} text={self.text_size:#x} "
                f"data={self.data_size:#x} bss={self.bss_size:#x}>")


def load(path, data=None) -> ModFile:
    return ModFile(path, data)
