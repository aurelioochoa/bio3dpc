#!/usr/bin/env python3
"""Capture portrait gameplay and the landscape opening for the README.

Uses disposable saves and requires matching original game files and Pillow. No game files
are copied into the repository; only rendered framebuffer captures are saved.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image
from bio3dpc.boot import Boot
from bio3dpc.controls import GameControls
from bio3dpc.profiles import PROFILES, discover_root
from bio3dpc.services import AVK_SELECT, AVK_SOFT2, AVK_UP


def capture(profile, output, seconds, fps):
    module = profile.mod_path(discover_root())
    if not module.is_file():
        raise FileNotFoundError(f'Supply your own {profile.id} module first')
    with tempfile.TemporaryDirectory(prefix='bio3dpc-capture-') as saves:
        boot = Boot(module, data_dir=profile.data_path(discover_root()),
                    save_dir=saves, level='error')
        controls = None
        try:
            boot.load_module()
            boot.create_applet()
            boot.app_start()
            controls = GameControls(boot)

            def key(code, hold=.12):
                boot.runtime.post_key(code)
                boot.run(seconds=hold)
                boot.runtime.post_key(code, press=False)
                boot.run(seconds=.15)

            def signature():
                return hashlib.sha1(boot.runtime.snapshot_rgb()[2]).hexdigest()[:12]

            boot.run(seconds=4)
            for step in range(100):
                state = controls.state()
                # Landscape exposes the player before its opening cinematic
                # finishes. Wait for the rendered health/EVE HUD, rather than
                # treating a loaded player behind a black title card as play.
                width, height, pixels = boot.runtime.snapshot_rgb()
                hud_visible = any(pixels[y * width * 3:(y * width + 70) * 3].strip(b'\0')
                                  for y in range(min(25, height)))
                armed = state and state.mode in (1, 2) and state.weapons
                if armed or (state and profile.id == 'n7705' and hud_visible):
                    boot.run(seconds=8 if not armed else .2)
                    break
                before = signature()
                swimming = before == '0f62be81305c'
                key(AVK_UP if swimming else AVK_SELECT, 2 if swimming else .12)
                boot.run(seconds=.6)
                if signature() == before:
                    key(AVK_SOFT2)
                    if signature() == before:
                        # Native landscape swimming uses a different bitmap
                        # from portrait; hold UP when confirm and skip do not
                        # advance a stationary intro prompt.
                        key(AVK_UP, 2)
                if step % 10 == 0:
                    print(f'{profile.id}: navigating intro ({step})', flush=True)
                    w, h, rgb = boot.runtime.snapshot_rgb()
                    Image.frombytes('RGB', (w, h), rgb).save(
                        Path(tempfile.gettempdir()) / f'bio3dpc-capture-{profile.id}.png')
            else:
                raise RuntimeError(f'{profile.id}: gameplay not reached')

            output.mkdir(parents=True, exist_ok=True)
            frames = []
            deadline = time.monotonic()
            count = round(seconds * fps)
            for frame in range(count):
                # A gentle look right and back through the same camera method
                # used by mouse input, without changing inventory or level data.
                controls.rotate_view(.045 if frame < count // 2 else -.045, 0)
                deadline += 1 / fps
                boot.run(seconds=max(.001, deadline - time.monotonic()))
                width, height, pixels = boot.runtime.snapshot_rgb()
                image = Image.frombytes('RGB', (width, height), pixels)
                if not frames:
                    image.save(output / f'gameplay-{profile.id}.png')
                frames.append(image.resize((width * 2, height * 2), Image.Resampling.NEAREST))
            if len({hashlib.sha256(frame.tobytes()).digest() for frame in frames}) < 4:
                raise RuntimeError('Capture did not contain changing gameplay')
            frames[0].save(output / f'gameplay-{profile.id}.gif', save_all=True,
                           append_images=frames[1:], duration=round(1000 / fps),
                           loop=0, optimize=True, disposal=2)
            print(f'{profile.id}: captured {len(frames)} rendered guest frames', flush=True)
        finally:
            if controls is not None:
                controls.close()
            boot.shutdown()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=[p.id for p in PROFILES])
    parser.add_argument('--out', type=Path, default=Path('docs/media'))
    parser.add_argument('--seconds', type=float, default=10)
    parser.add_argument('--fps', type=int, default=10)
    args = parser.parse_args()
    if not 1 <= args.seconds <= 60 or not 1 <= args.fps <= 30:
        parser.error('seconds must be 1–60 and fps must be 1–30')
    os.environ['BIO3DPC_AUDIO'] = '0'
    for profile in PROFILES:
        if args.profile is None or profile.id == args.profile:
            capture(profile, args.out, args.seconds, args.fps)


if __name__ == '__main__':
    main()
