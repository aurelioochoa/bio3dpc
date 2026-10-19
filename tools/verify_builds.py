#!/usr/bin/env python3
"""Inventory local originals and verify isolated portrait/landscape sessions."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bio3dpc.boot import Boot
from bio3dpc.controls import GameControls
from bio3dpc.profiles import KNOWN_BUILDS, PROFILES, discover_root
from bio3dpc.services import AVK_0, AVK_2, AVK_6, AVK_7, AVK_8, AVK_9, AVK_SELECT, AVK_SOFT2, AVK_UP
from tools.screenshot import write_png


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def originals(root):
    return {str(path.relative_to(root)): digest(path)
            for path in sorted(root.rglob('*')) if path.is_file()}


def inventory(root):
    builds = []
    for profile in KNOWN_BUILDS:
        mod = profile.mod_path(root)
        if not mod.is_file():
            raise AssertionError(f'Original module missing: {mod}')
        assert digest(mod) == profile.sha256, f'Unknown module bytes: {mod}'
        mif = mod.parent.parent / (profile.module_dir + '.mif')
        raw = mif.read_bytes()
        expected = profile.app_clsid.to_bytes(4, 'little')
        assert any(raw[offset:offset + 4] == expected for offset in range(0, len(raw)-3, 2)), mif
        data = profile.data_path(root)
        shipped = [path.name for path in sorted(mod.parent.glob('bio[1-7].dat'))]
        available = [path.name for path in sorted(data.glob('bio[1-7].dat'))]
        for filename in shipped:
            assert digest(mod.parent / filename) == digest(data / filename), f'Asset mismatch: {filename}'
        builds.append(dict(id=profile.id, title=profile.title, size=[profile.width, profile.height],
                           clsid=hex(profile.app_clsid), sha256=profile.sha256,
                           software=profile.software, available=available))
    sets = []
    for data in sorted((root / 'data').iterdir()):
        if not data.is_dir():
            continue
        lines = (data / 'bio3d.idx').read_text().splitlines()
        assert lines[0] == 'INDX'
        count = int(lines[1])
        assert len(lines[2:]) == count * 2
        mismatches = []
        for name, expected in zip(lines[2::2], lines[3::2]):
            path = data / name
            actual = path.stat().st_size if path.is_file() else None
            if actual != int(expected):
                mismatches.append(dict(name=name, expected=int(expected), actual=actual))
        matching = []
        for profile in KNOWN_BUILDS:
            folder = profile.mod_path(root).parent
            if all((data / name).is_file() and digest(folder / name) == digest(data / name)
                   for name in ('bio1.dat', 'bio2.dat', 'bio3d.idx')):
                matching.append(profile.id)
        sets.append(dict(name=data.name, count=count, mismatches=mismatches, matching_builds=matching))
    print(json.dumps(dict(builds=builds, data_sets=sets), indent=2), flush=True)
    print('BUILD INVENTORY VERIFIED', flush=True)


def gameplay(profile, root, output, budget=120):
    """Observe guest player movement and camera changes without patching code."""
    output.mkdir(parents=True, exist_ok=True)
    mod, data = profile.mod_path(root), profile.data_path(root)
    before = originals(root)
    with tempfile.TemporaryDirectory(prefix=f'bio3dpc-{profile.id}-') as save:
        boot = Boot(mod, data_dir=data, save_dir=save, level='error')
        controls = None
        try:
            boot.load_module()
            boot.create_applet()
            boot.app_start()
            controls = GameControls(boot)
            boot.runtime.input_clock = controls.input_tick

            def drive(seconds):
                deadline = time.monotonic() + seconds
                while time.monotonic() < deadline:
                    boot.run(seconds=min(.02, max(0, deadline-time.monotonic())))

            def tap(key, seconds=.12):
                boot.runtime.post_key(key)
                drive(seconds)
                boot.runtime.post_key(key, press=False)
                drive(.15)

            def capture(name):
                snapshot = boot.runtime.snapshot_rgb()
                assert snapshot and snapshot[:2] == (profile.width, profile.height)
                write_png(str(output / f'{profile.id}-{name}.png'), *snapshot)
                return hashlib.sha1(snapshot[2]).hexdigest()[:12]

            drive(6)
            started = time.monotonic()
            previous = None
            movement_ready = False
            for step in range(360):
                current = controls.state()
                if current is not None and controls.look_ticks >= 3 and step % 4 == 0:
                    camera = boot.m.u32(current.player + 0x2b8)
                    position = lambda: boot.m.read(camera + 0xac, 12)
                    heading = lambda: boot.m.read(current.player + 0x278, 4)
                    baseline = position()
                    drive(.3)
                    # An authored camera path must not count as user movement.
                    if position() == baseline:
                        moved = []
                        for key in (AVK_7, AVK_9, AVK_8, AVK_2):
                            before_position, before_heading = position(), heading()
                            tap(key, .4)
                            moved.append(position() != before_position and heading() == before_heading)
                        print(profile.id, 'movement readiness', moved, flush=True)
                        if sum(moved) >= 2:
                            movement_ready = True
                            break
                if time.monotonic() - started > budget:
                    raise AssertionError(f'{profile.id}: gameplay not reached; see {output}')
                signature = capture('latest')
                game = boot.m.u32(boot.applet + 0x20)
                state = boot.m.read(game + 0x1b8 + controls.game_delta, 12).hex() if game else ''
                print(profile.id, 'step', step, 'screen', signature, 'game', state,
                      'samples', controls.look_ticks, flush=True)
                if current is not None and current.mode == 0:
                    # The bathysphere intro has a HUD/player but locks motion
                    # while Johnny and Atlas speak. Skip using the native RSK.
                    tap(AVK_SOFT2)
                    tap(AVK_SELECT)
                    tap(AVK_0)
                    tap(AVK_2, .6)
                elif signature == previous or signature == '0f62be81305c':
                    tap(AVK_SOFT2)
                    tap(AVK_UP, 1.5)
                else:
                    tap(AVK_SELECT)
                previous = signature
                drive(.6)
            state = controls.state()
            assert state is not None, f'{profile.id}: gameplay not reached'
            assert movement_ready, f'{profile.id}: native movement never became available'
            # Level loading installs the player before its first input pass.
            drive(1)
            state = controls.state()
            assert state is not None, f'{profile.id}: player left gameplay'
            assert controls.look_ticks > 0, 'Camera function was never sampled'
            capture('gameplay')
            machine = boot.m
            camera = machine.u32(state.player + 0x2b8)
            position = lambda: machine.read(camera + 0xac, 12)
            heading = lambda: machine.read(state.player + 0x278, 4)
            movement = {}
            for key, name in ((AVK_7, 'left'), (AVK_9, 'right'),
                              (AVK_8, 'backward'), (AVK_2, 'forward')):
                initial, facing = position(), heading()
                tap(key, .45)
                movement[name] = position() != initial
                print(profile.id, name, movement[name], 'samples', controls.input_tick(key),
                      'position', position().hex(), 'state', controls.state(), flush=True)
                assert heading() == facing, f'{profile.id}: movement changed heading'
            # A wall can prevent travel in one direction; independent opposing
            # movement probes distinguish collision from missing guest input.
            assert sum(movement.values()) >= 2, f'{profile.id}: movement failed: {movement}'
            facing = heading()
            tap(AVK_6, .2)
            assert heading() != facing, f'{profile.id}: look key did not turn player'
            assert controls.input_tick(AVK_2) > 0 and controls.input_tick(AVK_6) > 0
            capture('after-input')
            result = dict(profile=profile.id, size=[profile.width, profile.height],
                          ticks=boot.runtime.guest_ticks, camera_samples=controls.look_ticks,
                          movement=movement, turning=True)
        finally:
            if controls is not None:
                controls.close()
            boot.shutdown()
    assert originals(root) == before, 'Original game files or saves changed'
    result['originals_unchanged'] = True
    print(json.dumps(result), flush=True)
    (output / f'{profile.id}-result.json').write_text(json.dumps(result, indent=2)+'\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', action='store_true')
    parser.add_argument('--gameplay', action='store_true')
    parser.add_argument('--profile', choices=[p.id for p in PROFILES])
    parser.add_argument('--library', type=Path, default=discover_root())
    parser.add_argument('--output', type=Path, default=Path('/tmp/bio3dpc-preservation/verification'))
    parser.add_argument('--budget', type=float, default=120)
    args = parser.parse_args()
    if args.inventory:
        inventory(args.library)
    if args.gameplay:
        os.environ['BIO3DPC_AUDIO'] = '0'
        selected = [p for p in PROFILES if args.profile is None or p.id == args.profile]
        for profile in selected:
            gameplay(profile, args.library, args.output, args.budget)
        print('PORTRAIT AND LANDSCAPE GAMEPLAY VERIFIED' if len(selected) == 2 else 'GAMEPLAY VERIFIED', flush=True)


if __name__ == '__main__':
    main()
