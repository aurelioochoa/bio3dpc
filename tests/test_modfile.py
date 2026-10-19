"""Synthetic ``.mod`` parsing/loading tests (no game data required)."""
from __future__ import annotations

import io
import struct

import pytest

from bio3dpc.log import RunLog
from bio3dpc.machine import ArmMachine
from bio3dpc.modfile import MAGIC, ModFile, ModFormatError

BASE_WORD_OFFSET = 0x18C
FIRST_TEXT_WORD = 0xDEADBEEF
LAST_IMAGE_WORD = 0x12345678


def synthetic_image(size: int = 0x400) -> bytes:
    """A minimal position-independent image with plausible header words."""
    data = bytearray(size)
    struct.pack_into(
        "<16I", data, 0,
        0xEA00000E,   # cold-entry branch (to 0x40)
        0xEA00000F,   # warm-entry branch
        0,            # magic word: "BREW" written below
        1,            # format word; byte 0x15 must read 1
        0x40,         # entry: offset of AEEMod_Load
        0x00000100,   # version
        0x200,        # text_offset (conventional)
        0x100,        # text_size
        0x40,         # data_size
        0x1000,       # bss_size
        0x20,         # reloc_size
        0,            # flags
        0x200,        # image_base
        0x140,        # image_size (text + data)
        0x400000,     # heap_size
        0,            # reserved
    )
    data[8:12] = MAGIC
    struct.pack_into("<I", data, BASE_WORD_OFFSET,
                     (-BASE_WORD_OFFSET) & 0xFFFFFFFF)
    struct.pack_into("<I", data, 0x200, FIRST_TEXT_WORD)
    struct.pack_into("<I", data, size - 4, LAST_IMAGE_WORD)
    return bytes(data)


# --------------------------------------------------------------------- parsing
def test_parse_synthetic_header():
    image = synthetic_image()
    assert image[0x15] == 1  # loader checks this byte of the version word

    mod = ModFile("<synthetic>", data=image)
    assert mod.text_offset == 0x200
    assert mod.text_size == 0x100
    assert mod.data_size == 0x40
    assert mod.bss_size == 0x1000
    assert mod.reloc_size == 0x20
    assert mod.image_size == 0x140
    assert mod.heap_size == 0x400000
    assert mod.entry == 0x40
    assert mod.base_word_offset == BASE_WORD_OFFSET
    assert mod.stored_base_word == (-BASE_WORD_OFFSET) & 0xFFFFFFFF


def test_check_position_independent_accepts_self_relative_image():
    mod = ModFile("<synthetic>", data=synthetic_image())
    assert mod.check_position_independent() is None


def test_corrupt_base_word_is_rejected():
    image = bytearray(synthetic_image())
    image[BASE_WORD_OFFSET] ^= 0x01
    mod = ModFile("<synthetic>", data=bytes(image))
    with pytest.raises(ModFormatError):
        mod.check_position_independent()


def test_bad_magic_is_rejected():
    image = bytearray(synthetic_image())
    image[8] = ord("X")
    with pytest.raises(ModFormatError):
        ModFile("<synthetic>", data=bytes(image))


def test_too_small_image_is_rejected():
    with pytest.raises(ModFormatError):
        ModFile("<synthetic>", data=b"\0" * 0x100)


# --------------------------------------------------------------------- loading
def test_load_into_copies_image_into_guest_memory():
    image = synthetic_image()
    machine = ArmMachine(log=RunLog(level="error", stream=io.StringIO()))
    try:
        mod = ModFile("<synthetic>", data=image)

        base = mod.load_into(machine)
        assert base == machine.load_base
        assert machine.read(base, len(image)) == image
        assert machine.u32(base) == 0xEA00000E                    # first word
        assert machine.u32(base + len(image) - 4) == LAST_IMAGE_WORD  # last word
    finally:
        machine.close()
