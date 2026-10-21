"""Cached launcher artwork must equal the direct Pillow operations it replaces."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from PIL import Image, ImageChops, ImageOps

from bio3dpc import ui_images

ASSETS = Path(__file__).resolve().parents[1] / 'bio3dpc' / 'ui_assets'


@pytest.fixture(autouse=True)
def empty_cache():
    ui_images.clear()
    yield
    ui_images.clear()


def same(a, b):
    return a.mode == b.mode and a.size == b.size and ImageChops.difference(a, b).getbbox() is None


def test_cached_operations_are_pixel_identical():
    with Image.open(ASSETS/'bathysphere-source.png') as opened:
        plate = opened.convert('RGB').resize((1152, 768), Image.Resampling.LANCZOS)
    cached = ui_images.prepared(ASSETS/'bathysphere-source.png', 'RGB', ('resize', (1152, 768)))
    assert same(cached, plate)
    left = plate.crop((0, 0, 736, 768))
    assert same(ui_images.fitted(ui_images.cropped(cached, (0, 0, 736, 768)), (500, 640)),
                ImageOps.fit(left, (500, 640), Image.Resampling.LANCZOS))

    with Image.open(ASSETS/'controls'/'logo.png') as opened:
        logo = opened.convert('RGBA')
    expected = Image.new('RGBA', (348, 152))
    fitted = ImageOps.contain(logo, (348, 152), Image.Resampling.LANCZOS)
    expected.alpha_composite(fitted, ((348 - fitted.width) // 2, (152 - fitted.height) // 2))
    assert same(ui_images.letterboxed(ui_images.source(ASSETS/'controls'/'logo.png', 'RGBA'), (348, 152)), expected)


def test_sources_decode_once_and_resizes_are_reused(monkeypatch):
    opened = []
    original = Image.open
    monkeypatch.setattr(ui_images.Image, 'open', lambda path, *args: opened.append(path) or original(path, *args))
    path = ASSETS/'controls'/'play.png'
    first = ui_images.resized(ui_images.source(path, 'RGBA'), (120, 30))
    second = ui_images.resized(ui_images.source(path, 'RGBA'), (120, 30))
    assert first is second
    assert len(opened) == 1


def test_parallel_warm_shares_results():
    path = ASSETS/'controls'/'secondary.png'
    for future in ui_images.warm([(path, 'RGBA', ('resize', (160, 38)))] * 4):
        future.result()
    assert ui_images.prepared(path, 'RGBA', ('resize', (160, 38))) is \
        ui_images.resized(ui_images.source(path, 'RGBA'), (160, 38))
    ui_images.shutdown()


def test_changed_file_is_decoded_again(tmp_path):
    path = tmp_path/'art.png'
    Image.new('RGBA', (4, 4), 'red').save(path)
    assert ui_images.source(path, 'RGBA').getpixel((0, 0)) == (255, 0, 0, 255)
    Image.new('RGBA', (4, 4), 'blue').save(path)
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    assert ui_images.source(path, 'RGBA').getpixel((0, 0)) == (0, 0, 255, 255)


def test_missing_artwork_raises_like_image_open(tmp_path):
    with pytest.raises(OSError):
        ui_images.source(tmp_path/'missing.png')
    assert all(future.result() is None for future in ui_images.warm([(tmp_path/'missing.png', 'RGBA')]))
    ui_images.shutdown()
