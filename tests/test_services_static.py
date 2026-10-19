"""Static BREW service encoders (no emulation, no game data).

The device info and app-start structures must match the BREW ABI.
"""
from __future__ import annotations

import struct

from bio3dpc.services import (
    COLOR_DEPTH,
    SCREEN_HEIGHT,
    SCREEN_WIDTH,
    encode_app_start,
    encode_device_info,
)

APP_CLSID = 0x0109005B
APP_DISPLAY = 0x11223344


# ------------------------------------------------------------- device info
def test_device_info_is_64_bytes_with_expected_geometry():
    data = encode_device_info()

    assert len(data) == 64
    assert (SCREEN_WIDTH, SCREEN_HEIGHT) == (176, 220)

    cx, cy, cx_alt, cy_alt = struct.unpack_from("<4H", data, 0)
    assert (cx, cy, cx_alt, cy_alt) == (176, 220, 176, 220)

    assert struct.unpack_from("<H", data, 14)[0] == 16 == COLOR_DEPTH
    assert struct.unpack_from("<H", data, 44)[0] == 64  # wStructSize


def test_device_info_reflects_arguments():
    data = encode_device_info(cx=320, cy=240, depth=32,
                              ram=0x01000000, platform=7)

    assert struct.unpack_from("<HH", data, 0) == (320, 240)
    assert struct.unpack_from("<HH", data, 4) == (320, 240)
    assert struct.unpack_from("<H", data, 14)[0] == 32
    assert struct.unpack_from("<I", data, 24)[0] == 0x01000000
    assert struct.unpack_from("<I", data, 60)[0] == 7


# --------------------------------------------------------------- app start
def test_app_start_encodes_header_and_rect():
    data = encode_app_start(APP_CLSID, APP_DISPLAY)

    error, cls_app, pdisplay = struct.unpack_from("<IiI", data, 0)
    assert error == 0
    assert cls_app == APP_CLSID
    assert pdisplay == APP_DISPLAY

    assert struct.unpack_from("<HHHH", data, 12) == (0, 0, 176, 220)


def test_app_start_is_24_bytes_and_round_trips_args():
    args = 0xCAFEF00D
    data = encode_app_start(APP_CLSID, APP_DISPLAY, args=args)

    assert len(data) == 24
    assert struct.unpack_from("<I", data, 20)[0] == args
