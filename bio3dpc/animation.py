"""Bounded, asynchronous decoding of a local looping launcher video."""
from __future__ import annotations

from pathlib import Path
import queue
import shutil
import subprocess
import threading
import math
import random
import time


class LampFlicker:
    """Sparse irregular brownouts, interpolated without a repeating timeline."""
    def __init__(self, *, rng=None, now=None):
        self.rng = rng or random.Random()
        now = time.monotonic() if now is None else now
        self.next_event = now + self.rng.uniform(2.5, 9)
        self.knots = []

    def sample(self, now):
        if now >= self.next_event:
            start = now
            self.knots = [(start, .78)]
            for _ in range(self.rng.randint(2, 4)):
                start += self.rng.uniform(.035, .075)
                self.knots.append((start, self.rng.uniform(.06, .32)))
                start += self.rng.uniform(.05, .13)
                self.knots.append((start, self.rng.uniform(.82, 1)))
                start += self.rng.uniform(.04, .12)
            self.knots.append((start+.12, .78))
            self.next_event = start + self.rng.uniform(3.5, 12)
        for (left, a), (right, b) in zip(self.knots, self.knots[1:]):
            if left <= now < right:
                progress = (now-left)/(right-left)
                smooth = progress*progress*(3-2*progress)
                return a+(b-a)*smooth
        return .78 + .035*math.sin(now*1.9)


class LoopVideo:
    """Keep only the latest decoded frame; never call Tk from a worker."""
    def __init__(self, path: Path, width=1152, height=768, *, crop=None):
        self.path, self.width, self.height = Path(path), width, height
        self.crop = crop
        self.frames = queue.Queue(maxsize=1)
        self.process = None
        self.worker = None
        self.error = None
        self.decoded = 0
        self._stop = threading.Event()
        self.started_at = None

    def start(self):
        if self.process is not None:
            return True
        decoder = shutil.which('ffmpeg')
        if not decoder or not self.path.is_file():
            return False
        self._stop.clear()
        self.error = None
        try:
            filters = f'scale={self.width}:{self.height}'
            if self.crop:
                filters = (f'crop={self.crop},scale={self.width}:{self.height}:'
                           f'force_original_aspect_ratio=increase,crop={self.width}:{self.height}')
            self.process = subprocess.Popen([
                decoder, '-hide_banner', '-loglevel', 'error', '-nostdin',
                '-stream_loop', '-1', '-re', '-i', str(self.path), '-an',
                '-vf', filters, '-threads', '1',
                '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1',
            ], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except OSError as error:
            self.error = str(error)
            self.process = None
            return False
        self.worker = threading.Thread(target=self._read, args=(self.process,), daemon=True)
        self.started_at = time.monotonic()
        self.worker.start()
        return True

    def _read(self, process):
        size = self.width * self.height * 3
        header = f'P6\n{self.width} {self.height}\n255\n'.encode()
        try:
            while not self._stop.is_set():
                raw = process.stdout.read(size)
                if len(raw) != size:
                    break
                frame = header + raw
                self.decoded += 1
                try:
                    self.frames.get_nowait()
                except queue.Empty:
                    pass
                try:
                    self.frames.put_nowait(frame)
                except queue.Full:
                    pass
        except (OSError, ValueError) as error:
            if not self._stop.is_set():
                self.error = str(error)
        finally:
            process.stdout.close()

    def latest(self):
        try:
            return self.frames.get_nowait()
        except queue.Empty:
            return None

    def stop(self):
        self._stop.set()
        process, self.process = self.process, None
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=.5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1)
            if self.worker is not None:
                self.worker.join(timeout=1)
        self.worker = None
        while self.latest() is not None:
            pass
