"""Packed PNG samples are indices for palettes and intensities for grayscale."""
import struct
import zlib

import pytest

from bio3dpc.png import decode


def chunk(kind, payload):
    return (struct.pack(">I", len(payload)) + kind + payload +
            struct.pack(">I", zlib.crc32(kind + payload)))


def packed_png(depth, color_type, values, palette=b"", transparency=b""):
    raw = bytearray((len(values) * depth + 7) // 8)
    for i, value in enumerate(values):
        raw[i * depth // 8] |= value << (8 - depth - i * depth % 8)
    data = b"\x89PNG\r\n\x1a\n" + chunk(
        b"IHDR", struct.pack(">IIBBBBB", len(values), 1, depth, color_type, 0, 0, 0))
    if palette:
        data += chunk(b"PLTE", palette)
    if transparency:
        data += chunk(b"tRNS", transparency)
    return data + chunk(b"IDAT", zlib.compress(b"\0" + raw)) + chunk(b"IEND", b"")


@pytest.mark.parametrize("depth", [1, 2, 4, 8])
def test_indexed_samples_preserve_indices_and_transparency(depth):
    maximum = (1 << depth) - 1
    palette = [(i, 255 - i, i // 2) for i in range(maximum + 1)]
    indices = [0, 1, maximum, 0, 1, maximum, 1, 0, maximum]
    alphas = [255, 23] + [255] * (maximum - 1)
    png = packed_png(depth, 3, indices,
                     b"".join(bytes(rgb) for rgb in palette), bytes(alphas))
    image = decode(png)
    assert image.rgba == b"".join(bytes((*palette[i], alphas[i])) for i in indices)
    assert image.has_alpha


@pytest.mark.parametrize("depth", [1, 2, 4, 8])
def test_grayscale_samples_still_scale_to_eight_bit(depth):
    maximum = (1 << depth) - 1
    values = [0, 1, maximum, 0, 1]
    image = decode(packed_png(depth, 0, values))
    expected = [value * 255 // maximum for value in values]
    assert image.rgba == b"".join(bytes((v, v, v, 255)) for v in expected)
