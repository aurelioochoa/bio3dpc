#!/usr/bin/env python3
"""Boot the game, run N seconds, then dump the device bitmap to a PNG file.

Development helper: proves whether the guest rasterizer is producing pixels,
independent of any windowing frontend.
"""
from __future__ import annotations

import argparse
import struct
import sys
import zlib

sys.path.insert(0, ".")
sys.path.insert(0, "tools")

from boot_check_paths import DATA_DIR, GAME_DIR  # noqa: E402

from bio3dpc.boot import Boot  # noqa: E402


def write_png(path: str, width: int, height: int, pixels: bytes) -> None:
    """pixels: RGB888 rows, top-down."""
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + kind + payload
                + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))

    raw = b"".join(b"\x00" + pixels[y * width * 3:(y + 1) * width * 3]
                   for y in range(height))
    data = (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6))
            + chunk(b"IEND", b""))
    with open(path, "wb") as handle:
        handle.write(data)


def dump_bitmap(runtime, path: str) -> bool:
    bitmap = runtime.roles.get("device_bitmap")
    if not bitmap:
        print("no device bitmap yet")
        return False
    width, height, pitch, depth, pixels = runtime.bitmap_geometry(bitmap)
    if not pixels:
        print("device bitmap has no pixels")
        return False
    m = runtime.m
    rgb = bytearray()
    for y in range(height):
        row = m.read(pixels + y * pitch, width * 2)
        values = struct.unpack(f"<{width}H", row)
        for value in values:
            r = (value >> 11) & 0x1F
            g = (value >> 5) & 0x3F
            b = value & 0x1F
            rgb += bytes(((r << 3) | (r >> 2), (g << 2) | (g >> 4),
                          (b << 3) | (b >> 2)))
    write_png(path, width, height, bytes(rgb))
    nonzero = sum(1 for i in range(0, len(rgb), 3) if rgb[i:i + 3] != b"\0\0\0")
    print(f"dumped {width}x{height} depth={depth} {path} "
          f"({nonzero}/{width * height} non-black pixels)")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=8.0)
    parser.add_argument("--out", default="/tmp/opencode/bio3dpc/screen.png")
    parser.add_argument("--level", default="warn")
    args = parser.parse_args()

    boot = Boot(f"{GAME_DIR}/bio3d.mod", data_dir=DATA_DIR, level=args.level)
    try:
        boot.load_module()
        boot.create_applet()
        boot.app_start()
        reason = boot.run(seconds=args.seconds)
        print(f"stop={reason} frames_presented={boot.runtime.frames_presented}")
        dump_bitmap(boot.runtime, args.out)
    except Exception as error:  # noqa: BLE001
        print(f"{type(error).__name__}: {str(error).splitlines()[0]}")
        dump_bitmap(boot.runtime, args.out)
    finally:
        boot.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
