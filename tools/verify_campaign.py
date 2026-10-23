#!/usr/bin/env python3
"""Load every landscape campaign level through the game's own Continue path.

A chapter-1 ``savedata.dat`` is copied into a throwaway save folder.  A
Unicorn code hook replaces only the level index that Continue reads from the
save (Nokia 0x39238 stores it to game+0x108), so each of the seven levels is
loaded by the original level loader with real player and tutorial state.  The
game image, data and the given save are never modified.  Continue keeps the
saved position, so a level may open with the player outside its geometry; the
check is load, resources and native movement, not a playthrough.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bio3dpc.boot import Boot  # noqa: E402
from bio3dpc.campaign import default_output  # noqa: E402
from bio3dpc.controls import GameControls  # noqa: E402
from bio3dpc.log import RunLog  # noqa: E402
from bio3dpc.profiles import N7705, default_save_dir, discover_root  # noqa: E402
from bio3dpc.services import AVK_0, AVK_2, AVK_7, AVK_8, AVK_9, AVK_SELECT, AVK_SOFT2, AVK_UP  # noqa: E402
from tools.screenshot import write_png  # noqa: E402

CONTINUE_STORE = 0x39238  # str r6, [game, #0x108] with the saved level
LEVEL_SWITCH = 0x69484    # cmp r0, #6 on game+0x108 inside the level loader
LEVELS = ('01_welcome_to_rapture_a', '01_welcome_to_rapture_b', '02_medical_pevilion_a',
          '02_medical_pevilion_b', '03_neptunes_bounty_a', '03_neptunes_bounty_b',
          '04_smugglers_hideout')


class Lines:
    def __init__(self):
        self.lines = []

    def write(self, text):
        self.lines.append(text)

    def flush(self):
        pass


def check_level(level, mod, data, seed, output, budget):
    from unicorn import UC_HOOK_CODE
    from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R6
    stream = Lines()
    loads = []
    with tempfile.TemporaryDirectory(prefix='bio3dpc-campaign-') as save:
        shutil.copy(seed, Path(save) / 'savedata.dat')
        boot = Boot(mod, log=RunLog('info', stream=stream), data_dir=str(data), save_dir=save)
        controls = None
        try:
            boot.load_module()
            base = boot.m.load_base
            boot.m.uc.hook_add(UC_HOOK_CODE, lambda uc, *a: uc.reg_write(UC_ARM_REG_R6, level),
                               begin=base + CONTINUE_STORE, end=base + CONTINUE_STORE)
            boot.m.uc.hook_add(UC_HOOK_CODE, lambda uc, *a: loads.append(uc.reg_read(UC_ARM_REG_R0)),
                               begin=base + LEVEL_SWITCH, end=base + LEVEL_SWITCH)
            boot.create_applet()
            boot.app_start()
            controls = GameControls(boot)
            boot.runtime.input_clock = controls.input_tick

            def drive(seconds):
                deadline = time.monotonic() + seconds
                while time.monotonic() < deadline:
                    boot.run(seconds=min(.02, max(0, deadline - time.monotonic())))

            def tap(key, seconds=.12):
                boot.runtime.post_key(key)
                drive(seconds)
                boot.runtime.post_key(key, press=False)
                drive(.15)

            drive(6)
            started, moved, previous = time.monotonic(), [], None
            while time.monotonic() - started < budget:
                state = controls.state()
                if loads and state is not None and controls.look_ticks >= 3:
                    camera = boot.m.u32(state.player + 0x2b8)
                    position = lambda: boot.m.read(camera + 0xac, 12)
                    moved = []
                    for key in (AVK_7, AVK_9, AVK_8, AVK_2):
                        before = position()
                        tap(key, .4)
                        moved.append(position() != before)
                    if sum(moved) >= 2:
                        break
                signature = hashlib.sha1(boot.runtime.snapshot_rgb()[2]).digest()
                if state is not None and state.mode == 0:
                    # The bathysphere scene locks motion while Atlas speaks.
                    for key in (AVK_SOFT2, AVK_SELECT, AVK_0):
                        tap(key)
                    tap(AVK_2, .6)
                elif signature == previous:
                    # A static screen wants a skip (RSK) or "press UP to swim up".
                    tap(AVK_SOFT2)
                    tap(AVK_UP, 1.5)
                else:
                    tap(AVK_SELECT)
                previous = signature
                drive(.6)
            snapshot = boot.runtime.snapshot_rgb()
            write_png(str(output / f'level{level}-{LEVELS[level]}.png'), *snapshot)
        finally:
            if controls is not None:
                controls.close()
            boot.shutdown()
    text = ''.join(stream.lines)
    missing = sorted({line.split('name=')[1].split()[0] for line in text.splitlines()
                      if 'OpenFile missing' in line and 'savedata' not in line and 'daturl' not in line})
    return dict(level=level, name=LEVELS[level], loaded=loads[:1] == [level], movement=moved,
                playable=loads[:1] == [level] and sum(moved) >= 2 and not missing, missing=missing,
                frame=hashlib.sha1(snapshot[2]).hexdigest()[:12])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    root = discover_root()
    parser.add_argument('--mod', type=Path, default=N7705.mod_path(root))
    parser.add_argument('--data', type=Path, default=default_output(), help='rebuilt campaign folder')
    parser.add_argument('--save', type=Path, default=default_save_dir(N7705.mod_path(root)) / 'savedata.dat',
                        help='a chapter-1 Nokia savedata.dat (read only; copied per level)')
    parser.add_argument('--levels', default='0,1,2,3,4,5,6')
    parser.add_argument('--budget', type=float, default=240)
    parser.add_argument('--out', type=Path, default=Path('/tmp/opencode/bio3dpc/campaign'))
    args = parser.parse_args()
    if not args.save.is_file():
        parser.error(f'No Nokia save at {args.save}; play chapter 1 to its first CONTINUE save, or pass --save')
    args.out.mkdir(parents=True, exist_ok=True)
    results = []
    for level in (int(value) for value in args.levels.split(',')):
        result = check_level(level, args.mod, args.data, args.save, args.out, args.budget)
        print(json.dumps(result), flush=True)
        results.append(result)
    (args.out / 'campaign-result.json').write_text(json.dumps(results, indent=2) + '\n')
    passed = all(result['playable'] for result in results)
    print('CAMPAIGN LEVELS PLAYABLE' if passed else 'CAMPAIGN CHECK FAILED', flush=True)
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
