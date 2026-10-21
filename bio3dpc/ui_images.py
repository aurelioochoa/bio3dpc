"""Decode launcher artwork once per process and reuse resampled copies.

Artwork files are never rewritten or re-encoded; this only memoizes the exact
Pillow operations the launcher already performs, so every render is
pixel-identical.  Returned images are shared and must be treated as read-only:
crop, resize or copy before drawing on them.
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageOps

# Full-size sources are a few MB each; resampled copies are mostly small.
SOURCE_BUDGET = 64 * 1024 * 1024
DERIVED_BUDGET = 64 * 1024 * 1024
# Stable description of how a shared image was produced.  Derived entries are
# keyed on it rather than on the input object, so they never pin a full-size
# source in memory after the source cache has evicted it.
KEY = '_bio3dpc_ui_image_key'


def _cost(image):
    return len(image.getbands()) * image.width * image.height


class _Cache:
    def __init__(self, budget):
        self.budget, self.used = budget, 0
        self.entries = OrderedDict()
        self.lock = threading.Lock()

    def get(self, key):
        with self.lock:
            image = self.entries.get(key)
            if image is not None:
                self.entries.move_to_end(key)
            return image

    def put(self, key, image):
        setattr(image, KEY, key)
        cost = _cost(image)
        with self.lock:
            if key in self.entries or cost > self.budget:
                return
            self.entries[key] = image
            self.used += cost
            while self.used > self.budget:
                self.used -= _cost(self.entries.popitem(last=False)[1])

    def clear(self):
        with self.lock:
            self.entries.clear()
            self.used = 0


_sources = _Cache(SOURCE_BUDGET)
_derived = _Cache(DERIVED_BUDGET)
_inflight = {}
_inflight_lock = threading.Lock()


def _single_flight(key, cache, compute):
    """Return the cached value or compute it once, even across threads.

    A view that needs artwork while the background warm-up is decoding the
    same file waits for that decode instead of repeating it.
    """
    while True:
        value = cache.get(key)
        if value is not None:
            return value
        with _inflight_lock:
            pending = _inflight.get(key)
            if pending is None:
                pending = _inflight[key] = threading.Event()
                break
        pending.wait()
        # The owner may have failed; check again.
    try:
        return compute()
    finally:
        with _inflight_lock:
            del _inflight[key]
        pending.set()


def _source_key(path, mode):
    stat = path.stat()
    return ('source', str(path), mode, stat.st_mtime_ns, stat.st_size)


def source(path, mode=None):
    """Decoded artwork, converted like ``Image.open(path).convert(mode)``.

    Missing or unreadable files raise OSError, as Image.open does.
    """
    path = Path(path)
    key = _source_key(path, mode)

    def decode():
        with Image.open(path) as opened:
            image = opened.convert(mode) if mode else opened.copy()
        _sources.put(key, image)
        return image
    return _single_flight(key, _sources, decode)


def _letterbox(image, size):
    """Contain ``image`` in ``size`` and centre it on a transparent canvas."""
    canvas = Image.new('RGBA', size)
    fitted = ImageOps.contain(image, size, Image.Resampling.LANCZOS)
    canvas.alpha_composite(fitted, ((size[0] - fitted.width) // 2, (size[1] - fitted.height) // 2))
    return canvas


OPERATIONS = {
    'resize': lambda image, size: image.resize(size, Image.Resampling.LANCZOS),
    'fit': lambda image, size: ImageOps.fit(image, size, Image.Resampling.LANCZOS),
    'letterbox': _letterbox,
    'crop': lambda image, box: image.crop(box),
}


def _derive(image, operation, argument):
    parent = getattr(image, KEY, None)
    # Without a parent key the input is not shared artwork and has no stable
    # identity to cache under.
    if parent is None:
        return OPERATIONS[operation](image, argument)
    key = (parent, operation, argument)
    return _single_flight(key, _derived, lambda: _store(key, OPERATIONS[operation](image, argument)))


def _store(key, image):
    _derived.put(key, image)
    return image


def resized(image, size):
    return _derive(image, 'resize', tuple(size))


def fitted(image, size):
    return _derive(image, 'fit', tuple(size))


def letterboxed(image, size):
    return _derive(image, 'letterbox', tuple(size))


def cropped(image, box):
    return _derive(image, 'crop', tuple(box))


def prepared(path, mode, *steps):
    """Apply ``(operation, argument)`` steps to a decoded source."""
    image = source(path, mode)
    for operation, argument in steps:
        image = _derive(image, operation, argument)
    return image


_pool = None
_pool_lock = threading.Lock()


def _ignore_missing(function, *arguments):
    try:
        function(*arguments)
    except (OSError, ValueError):
        pass  # The drawing code reports or replaces missing artwork itself.


def warm(items):
    """Decode artwork on worker threads; Pillow releases the GIL while decoding.

    ``items`` holds ``(path, mode, *steps)`` as for ``prepared``.  Returns
    futures; failures are left to the drawing code.
    """
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix='bio3dpc-ui-art')
        return [_pool.submit(_ignore_missing, prepared, *item) for item in items]


def shutdown():
    """Drop queued warm-up work so closing the launcher never waits for it."""
    global _pool
    with _pool_lock:
        pool, _pool = _pool, None
    if pool:
        pool.shutdown(wait=False, cancel_futures=True)


def clear():
    _sources.clear()
    _derived.clear()
