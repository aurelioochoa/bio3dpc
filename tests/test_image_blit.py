"""IImage blits into a guest bitmap: source rect, scaling and clipping.

The game renders every character of its UI from one sprite-sheet PNG:
``IPARM_SIZE`` is the cell size and ``IPARM_OFFSET`` the cell position inside
the sheet (AEEIImage.h).  Reading the offset as a *destination* offset instead
made every menu invisible, so the semantics are pinned down here.
"""
from __future__ import annotations

import io
import struct

import pytest

from bio3dpc.helper import HelperTable
from bio3dpc.log import RunLog
from bio3dpc.machine import ArmMachine
from bio3dpc.png import PngImage
from bio3dpc.services import (
    IPARM_OFFSET,
    IPARM_SIZE,
    IPARM_SCALE,
    RO_TRANSPARENT,
    BrewRuntime,
)


@pytest.fixture()
def env():
    log = RunLog(level="error", stream=io.StringIO())
    machine = ArmMachine(log=log)
    helper = HelperTable(machine, log)
    helper.build()
    runtime = BrewRuntime(machine, log, helper)
    yield runtime, machine
    runtime.shutdown()
    machine.close()


def gradient(width: int, height: int) -> PngImage:
    """An image whose pixels encode their own coordinates."""
    rgba = bytearray()
    for y in range(height):
        for x in range(width):
            rgba += bytes(((x * 16) & 0xFF, (y * 16) & 0xFF, 0, 255))
    return PngImage(width, height, bytes(rgba))


def attach(runtime, image, target):
    """An IImage viewer showing ``image`` and drawing into ``target``."""
    viewer = runtime._new_png_viewer()
    state = runtime.images[viewer]
    state["image"] = image
    state["offscreen"] = target
    return viewer


def read_pixel(runtime, bitmap, x, y) -> int:
    width, height, pitch, depth, pixels = runtime.bitmap_geometry(bitmap)
    assert depth == 16
    return struct.unpack_from("<H", runtime.m.read(pixels + y * pitch + x * 2, 2))[0]


def native(runtime, cell) -> int:
    """RGB565 the destination holds for gradient cell (x, y)."""
    x, y = cell
    return runtime._rgba_to_native((x * 16, y * 16, 0), 16)


def test_int_parm_names_stay_in_sync(env):
    """The selectors above must be the AEEIImage.h values the game passes."""
    assert (IPARM_SIZE, IPARM_OFFSET, IPARM_SCALE) == (0, 1, 12)


def test_offset_selects_the_source_cell(env):
    runtime, machine = env
    sheet = gradient(8, 8)
    target = runtime._new_bitmap(8, 8)
    viewer = attach(runtime, sheet, target)

    runtime._image_set_parm(machine, viewer, IPARM_SIZE, 4, 3)
    runtime._image_set_parm(machine, viewer, IPARM_OFFSET, 2, 2)
    runtime._image_blit(machine, viewer, 1, 1)

    # Cell pixels 2..5 x 2..4 land at 1..4 x 1..3 ...
    for dy in range(3):
        for dx in range(4):
            assert read_pixel(runtime, target, 1 + dx, 1 + dy) == \
                native(runtime, (2 + dx, 2 + dy))
    # ... and nothing else is written.
    assert read_pixel(runtime, target, 0, 0) == 0
    assert read_pixel(runtime, target, 5, 4) == 0
    assert read_pixel(runtime, target, 6, 6) == 0


def test_offset_is_not_added_to_the_destination(env):
    runtime, machine = env
    target = runtime._new_bitmap(16, 16)
    viewer = attach(runtime, gradient(16, 16), target)

    runtime._image_set_parm(machine, viewer, IPARM_SIZE, 2, 2)
    runtime._image_set_parm(machine, viewer, IPARM_OFFSET, 6, 6)
    runtime._image_blit(machine, viewer, 0, 0)

    assert read_pixel(runtime, target, 0, 0) == native(runtime, (6, 6))
    assert read_pixel(runtime, target, 1, 0) == native(runtime, (7, 6))
    assert read_pixel(runtime, target, 0, 1) == native(runtime, (6, 7))
    assert read_pixel(runtime, target, 6, 6) == 0


def test_size_defaults_to_the_whole_image(env):
    runtime, machine = env
    target = runtime._new_bitmap(4, 4)
    viewer = attach(runtime, gradient(4, 4), target)

    runtime._image_blit(machine, viewer, 0, 0)

    assert read_pixel(runtime, target, 0, 0) == native(runtime, (0, 0))
    assert read_pixel(runtime, target, 3, 3) == native(runtime, (3, 3))


def test_scale_stretches_the_cell(env):
    runtime, machine = env
    target = runtime._new_bitmap(8, 8)
    viewer = attach(runtime, gradient(8, 8), target)

    runtime._image_set_parm(machine, viewer, IPARM_SIZE, 2, 2)
    runtime._image_set_parm(machine, viewer, IPARM_OFFSET, 4, 4)
    runtime._image_set_parm(machine, viewer, IPARM_SCALE, 4, 4)
    runtime._image_blit(machine, viewer, 0, 0)

    # Nearest-neighbour: each source pixel fills a 2x2 block.
    assert read_pixel(runtime, target, 0, 0) == native(runtime, (4, 4))
    assert read_pixel(runtime, target, 1, 1) == native(runtime, (4, 4))
    assert read_pixel(runtime, target, 2, 0) == native(runtime, (5, 4))
    assert read_pixel(runtime, target, 3, 3) == native(runtime, (5, 5))
    assert read_pixel(runtime, target, 4, 4) == 0


def test_clipped_draw_keeps_the_visible_part(env):
    runtime, machine = env
    target = runtime._new_bitmap(4, 4)
    viewer = attach(runtime, gradient(4, 4), target)

    runtime._image_blit(machine, viewer, -2, -1)

    assert read_pixel(runtime, target, 0, 0) == native(runtime, (2, 1))
    assert read_pixel(runtime, target, 1, 2) == native(runtime, (3, 3))
    assert read_pixel(runtime, target, 3, 3) == 0


def dotted(width: int, height: int, gap: int) -> PngImage:
    """A gradient image whose pixels at multiples of ``gap`` are holes."""
    rgba = bytearray()
    for y in range(height):
        for x in range(width):
            alpha = 0 if (x % gap == 1 or y % gap == 1) else 255
            rgba += bytes(((x * 16) & 0xFF, (y * 16) & 0xFF, 0, alpha))
    return PngImage(width, height, bytes(rgba), has_alpha=True)


def test_transparent_draw_leaves_holes_alone(env):
    """alpha-0 pixels are holes: the destination keeps whatever was there."""
    runtime, machine = env
    target = runtime._new_bitmap(8, 8)
    viewer = attach(runtime, dotted(4, 4, 2), target)
    runtime.images[viewer]["rop"] = RO_TRANSPARENT
    width, height, pitch, _depth, pixels = runtime.bitmap_geometry(target)
    machine.write(pixels, b"\xAB" * (pitch * height))

    runtime._image_blit(machine, viewer, 1, 1)
    assert any(key[0] == RO_TRANSPARENT
               for key in runtime.images[viewer]["image"].native_cache)

    for dy in range(4):
        for dx in range(4):
            hole = dx % 2 == 1 or dy % 2 == 1
            value = read_pixel(runtime, target, 1 + dx, 1 + dy)
            if hole:
                assert value == 0xABAB, (dx, dy)
            else:
                assert value == native(runtime, (dx, dy)), (dx, dy)
    assert read_pixel(runtime, target, 0, 0) == 0xABAB
    assert read_pixel(runtime, target, 5, 5) == 0xABAB


def test_transparent_draw_clipped_at_the_edge(env):
    """A clipped transparent blit still writes exactly its opaque runs."""
    runtime, machine = env
    target = runtime._new_bitmap(4, 4)
    viewer = attach(runtime, dotted(4, 4, 2), target)
    runtime.images[viewer]["rop"] = RO_TRANSPARENT
    width, height, pitch, _depth, pixels = runtime.bitmap_geometry(target)
    machine.write(pixels, b"\xCD" * (pitch * height))

    runtime._image_blit(machine, viewer, -2, -1)
    assert any(key[0] == RO_TRANSPARENT
               for key in runtime.images[viewer]["image"].native_cache)

    for dy in range(3):
        for dx in range(2):
            sx, sy = 2 + dx, 1 + dy
            hole = sx % 2 == 1 or sy % 2 == 1
            value = read_pixel(runtime, target, dx, dy)
            if hole:
                assert value == 0xCDCD, (dx, dy)
            else:
                assert value == native(runtime, (sx, sy)), (dx, dy)
    assert read_pixel(runtime, target, 3, 3) == 0xCDCD


def test_native_cache_eviction_keeps_blits_correct(env, monkeypatch):
    """Evicting memoised tables must cost time, never pixels.

    The byte budget is shrunk below one 2x2 RGB565 cell (8 bytes), so every
    blit of a fresh cell clears the cache and cells are translated again when
    they come back.  Re-blits must still land byte-for-byte.
    """
    runtime, machine = env
    monkeypatch.setattr(runtime, "NATIVE_CACHE_BYTES", 8)
    sheet = gradient(8, 8)
    target = runtime._new_bitmap(8, 8)
    viewer = attach(runtime, sheet, target)
    runtime._image_set_parm(machine, viewer, IPARM_SIZE, 2, 2)

    cells = [(0, 0), (4, 2), (0, 0), (4, 2)]
    for slot, (ox, oy) in enumerate(cells):
        runtime._image_set_parm(machine, viewer, IPARM_OFFSET, ox, oy)
        runtime._image_blit(machine, viewer, slot * 2, 0)
        # Each new cell forced out the previous table and became the only one.
        assert len(sheet.native_cache) == 1
        assert sheet.native_bytes == 8

    for slot, (ox, oy) in enumerate(cells):
        for dy in range(2):
            for dx in range(2):
                assert read_pixel(runtime, target, slot * 2 + dx, dy) == \
                    native(runtime, (ox + dx, oy + dy)), (slot, dx, dy)
    # Nothing outside the four 2x2 cells was touched.
    assert read_pixel(runtime, target, 0, 3) == 0
    assert read_pixel(runtime, target, 7, 7) == 0
