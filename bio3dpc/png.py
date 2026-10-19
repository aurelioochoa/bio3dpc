"""Minimal PNG decoder (stdlib zlib only).

Decodes the images shipped with BioShock 3D (8-bit palette PNGs with tRNS) and
the common PNG variants in general: bit depths 1/2/4/8/16, colour types 0/2/3/4/6,
filters 0-4, non-interlaced.  Interlaced (Adam7) images raise ``PngError``.

``decode(data)`` returns a ``PngImage`` whose ``rgba`` is a bytes object of
``width * height * 4`` bytes, row-major, top-down, 8-bit RGBA.
"""
from __future__ import annotations

import struct
import zlib


class PngError(ValueError):
    pass


class PngImage:
    __slots__ = ("width", "height", "rgba", "palette", "has_alpha",
                 "native_cache", "native_bytes")

    def __init__(self, width, height, rgba, palette=None, has_alpha=False):
        self.width = width
        self.height = height
        self.rgba = rgba
        self.palette = palette
        self.has_alpha = has_alpha
        # (depth, rect) -> native-pixel bytes, filled in by the blitter: the
        # same sprite is drawn on many frames, so the conversion is not worth
        # repeating every time.  ``native_bytes`` is the cache's total payload,
        # used to bound it (see BrewRuntime._native_runs).
        self.native_cache = {}
        self.native_bytes = 0


def _chunks(data: bytes):
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise PngError("missing PNG signature")
    offset = 8
    while offset + 8 <= len(data):
        length, kind = struct.unpack_from(">I4s", data, offset)
        payload = data[offset + 8:offset + 8 + length]
        if len(payload) != length:
            raise PngError("truncated chunk")
        yield kind, payload
        offset += 12 + length
        if kind == b"IEND":
            return


def _unfilter(raw: bytes, height: int, stride: int, bpp: int) -> bytearray:
    """Reverse the per-scanline filters (PNG spec 9.2)."""
    out = bytearray(height * stride)
    pos = 0
    prev = bytearray(stride)
    for row in range(height):
        if pos >= len(raw):
            raise PngError("truncated image data")
        filt = raw[pos]
        pos += 1
        line = bytearray(raw[pos:pos + stride])
        if len(line) < stride:
            raise PngError("truncated scanline")
        pos += stride
        if filt == 0:
            pass
        elif filt == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif filt == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif filt == 3:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif filt == 4:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                if pa <= pb and pa <= pc:
                    pred = a
                elif pb <= pc:
                    pred = b
                else:
                    pred = c
                line[i] = (line[i] + pred) & 0xFF
        else:
            raise PngError(f"unknown filter {filt}")
        out[row * stride:(row + 1) * stride] = line
        prev = line
    return out


def _samples(line: bytes, count: int, depth: int, *, indexed=False) -> list[int]:
    """Unpack samples, scaling intensities but preserving palette indices."""
    if depth == 8:
        return list(line[:count])
    if depth == 16:
        return [line[2 * i] for i in range(count)]
    values = []
    per_byte = 8 // depth
    mask = (1 << depth) - 1
    scale = 1 if indexed else 255 // mask
    for i in range(count):
        byte = line[i // per_byte]
        shift = 8 - depth * (i % per_byte + 1)
        values.append(((byte >> shift) & mask) * scale)
    return values


def decode(data: bytes) -> PngImage:
    """Decode a PNG bytestring to a PngImage."""
    header = None
    palette = None
    trns = None
    idat = bytearray()
    for kind, payload in _chunks(data):
        if kind == b"IHDR":
            (width, height, depth, color_type, compression, filt_method,
             interlace) = struct.unpack_from(">IIBBBBB", payload, 0)
            if compression != 0 or filt_method != 0:
                raise PngError("unsupported compression/filter method")
            if interlace != 0:
                raise PngError("interlaced PNG not supported")
            header = (width, height, depth, color_type)
        elif kind == b"PLTE":
            palette = [tuple(payload[i:i + 3]) for i in range(0, len(payload), 3)]
        elif kind == b"tRNS":
            trns = payload
        elif kind == b"IDAT":
            idat += payload
        elif kind == b"IEND":
            break
    if header is None:
        raise PngError("missing IHDR")
    width, height, depth, color_type = header
    if width <= 0 or height <= 0:
        raise PngError("bad image dimensions")

    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color_type]
    bpp = max(1, (depth * channels + 7) // 8)
    stride = (width * depth * channels + 7) // 8
    try:
        raw = zlib.decompress(bytes(idat))
    except zlib.error as error:
        raise PngError(f"zlib error: {error}") from error
    pixels = _unfilter(raw, height, stride, bpp)

    rgba = bytearray(width * height * 4)
    has_alpha = False
    if color_type == 3 and palette is None:
        raise PngError("palette PNG without PLTE")

    for y in range(height):
        line = pixels[y * stride:(y + 1) * stride]
        samples = _samples(line, width * channels, depth, indexed=color_type == 3)
        base = y * width * 4
        if color_type == 0:
            transparent_gray = None
            if trns and len(trns) >= 2:
                transparent_gray = struct.unpack(">H", trns[:2])[0] & ((1 << depth) - 1)
                transparent_gray = (transparent_gray * 255 // ((1 << depth) - 1))
            for x in range(width):
                gray = samples[x]
                alpha = 0 if gray == transparent_gray else 255
                if alpha == 0:
                    has_alpha = True
                o = base + x * 4
                rgba[o:o + 4] = bytes((gray, gray, gray, alpha))
        elif color_type == 2:
            transparent_rgb = None
            if trns and len(trns) >= 6:
                tr = struct.unpack(">HHH", trns[:6])
                step = 1 if depth == 8 else 257
                transparent_rgb = tuple(v // step for v in tr)
            for x in range(width):
                o = base + x * 4
                r, g, b = samples[3 * x:3 * x + 3]
                alpha = 255
                if transparent_rgb is not None and (r, g, b) == transparent_rgb:
                    alpha = 0
                    has_alpha = True
                rgba[o:o + 4] = bytes((r, g, b, alpha))
        elif color_type == 3:
            for x in range(width):
                index = samples[x]
                if index >= len(palette):
                    raise PngError(f"palette index {index} out of range")
                r, g, b = palette[index]
                alpha = 255
                if trns is not None and index < len(trns):
                    alpha = trns[index]
                    if alpha != 255:
                        has_alpha = True
                o = base + x * 4
                rgba[o:o + 4] = bytes((r, g, b, alpha))
        elif color_type == 4:
            for x in range(width):
                gray, alpha = samples[2 * x:2 * x + 2]
                if alpha != 255:
                    has_alpha = True
                o = base + x * 4
                rgba[o:o + 4] = bytes((gray, gray, gray, alpha))
        else:  # 6
            for x in range(width):
                o = base + x * 4
                chunk = samples[4 * x:4 * x + 4]
                if chunk[3] != 255:
                    has_alpha = True
                rgba[o:o + 4] = bytes(chunk)

    return PngImage(width, height, bytes(rgba), palette, has_alpha)
