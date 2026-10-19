#!/usr/bin/env python3
"""Exercise real Tk events against a real guest, using an isolated save copy.

Requires a display and the user's VX8350 data. Equipment checks provision
owned slots in this disposable guest only, allowing later-game controls to be
verified without a full playthrough. No user saves are modified. Failure to
reach gameplay or any Tk callback exception fails; guest faults are not skipped.
"""
from __future__ import annotations

import hashlib
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from boot_check_paths import DATA_DIR, GAME_DIR
from bio3dpc.boot import Boot
from bio3dpc.frontend import Frontend, PIXELS_PER_TURN_TICK, PIXELS_PER_PITCH_TICK
from bio3dpc.input_settings import ControlSettings
from bio3dpc.profiles import N7705, discover_root
from bio3dpc.services import (
    AVK_0, AVK_2, AVK_4, AVK_5, AVK_6, AVK_7, AVK_9,
    AVK_DOWN, AVK_UP, AVK_SELECT, AVK_SOFT1, AVK_SOFT2,
)


def require(ok, message):
    if not ok:
        raise AssertionError(message)
    print('ok ', message, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--display', help='X display for an isolated GUI test')
    parser.add_argument('--profile', choices=('vx8350', 'n7705'), default='vx8350')
    parser.add_argument('--mouse-only', action='store_true', help='stop after menu and camera checks')
    args = parser.parse_args()
    if args.profile == 'n7705' and not args.mouse_only:
        parser.error('use --mouse-only with n7705; the equipment fixture is specific to VX8350')
    if args.display:
        os.environ['DISPLAY'] = args.display
    os.environ['BIO3DPC_AUDIO'] = '0'
    data_dir, game_dir = DATA_DIR, GAME_DIR
    if args.profile == 'n7705':
        data_dir = N7705.data_path(discover_root())
        game_dir = N7705.mod_path(discover_root()).parent
    with tempfile.TemporaryDirectory(prefix='bio3d-input-check-') as work:
        shutil.copytree(data_dir, work, dirs_exist_ok=True)
        boot = Boot(f'{game_dir}/bio3d.mod', data_dir=work, level='error')
        frontend = None
        try:
            boot.load_module()
            boot.create_applet()
            boot.app_start()
            frontend = Frontend(boot, scale=2, control_settings=ControlSettings())
            root, canvas = frontend.root, frontend.canvas
            menu_x, middle_y = boot.runtime.width // 2, boot.runtime.height // 2
            root.update()
            root.focus_force()
            root.update()
            # Tk swallows callback exceptions by default. Preserve them so
            # synthetic input cannot silently fail this verification.
            errors = []
            root.report_callback_exception = lambda *error: errors.append(error)

            def drive(seconds):
                deadline = time.monotonic() + seconds
                while time.monotonic() < deadline:
                    root.update()
                    boot.run(seconds=.02)
                    frontend.draw()
                if errors:
                    raise AssertionError(f'Tk callback failed: {errors[0]}')

            def raw(key, hold=.12):
                boot.runtime.post_key(key)
                drive(hold)
                boot.runtime.post_key(key, press=False)
                drive(.15)

            def rgb():
                return boot.runtime.snapshot_rgb()[2]

            def signature():
                return hashlib.sha1(rgb()).hexdigest()[:12]

            def key(sym, hold=.25):
                root.event_generate('<KeyPress>', keysym=sym)
                drive(hold)
                root.event_generate('<KeyRelease>', keysym=sym)
                drive(.15)

            def until(predicate, seconds=5):
                deadline = time.monotonic() + seconds
                while not predicate() and time.monotonic() < deadline:
                    drive(.04)
                return predicate()

            def equipped(mode, slot):
                current = frontend.controls.state()
                return bool(current and current.mode == mode and current.can_select and
                            (current.weapon if mode == 1 else current.plasmid) == slot)

            def click(x, y, button=1, hold=.01):
                canvas.event_generate('<Motion>', warp=True, x=x * frontend.scale, y=y * frontend.scale)
                root.update()
                canvas.event_generate(f'<Button-{button}>', x=x * frontend.scale, y=y * frontend.scale)
                drive(hold)
                canvas.event_generate(f'<ButtonRelease-{button}>', x=x * frontend.scale, y=y * frontend.scale)
                drive(.4)

            drive(7)
            require(not frontend.captured and str(canvas.cget('cursor')) != 'none',
                    'menus start with a visible free pointer')
            require(frontend.controls.menu().kind == 1, 'sound prompt recognised')
            click(menu_x, middle_y + 35)
            require(not frontend.captured, 'sound choice click keeps pointer visible')
            require(until(lambda: frontend.controls.menu() and frontend.controls.menu().kind == 3),
                    'click selects NO on sound prompt')
            click(menu_x, middle_y + 70)
            require(until(lambda: frontend.controls.menu() and frontend.controls.menu().kind == 4),
                    'mouse advances title to main menu')
            menu = frontend.controls.menu()
            options_value = 3 if boot.m.u32(menu.address + 0x14) else 2
            options_y = dict(menu.rows)[options_value] + 6
            canvas.event_generate('<Motion>', x=menu_x * frontend.scale, y=options_y * frontend.scale)
            root.update()
            require(boot.m.u16(menu.address + 0xa) == options_value,
                    'hover highlights OPTIONS at native row position')
            click(2, 2)
            require(frontend.controls.menu().kind == 4, 'blank menu click is inert')
            click(menu_x, options_y)
            require(until(lambda: frontend.controls.menu() and frontend.controls.menu().kind == 11),
                    'click on OPTIONS opens options without closing window')
            click(menu_x, 20, button=3)
            require(until(lambda: frontend.controls.menu() and frontend.controls.menu().kind == 4),
                    'right click returns to main menu')
            require(not frontend.captured and str(canvas.cget('cursor')) != 'none',
                    'menu navigation preserves visible pointer')
            require(not frontend.pointer_capture.active, 'menus release native confinement')
            first_y = frontend.controls.menu().rows[0][1] + 6
            click(menu_x, first_y)
            for step in range(90):
                ready = frontend.controls.state()
                if ready is not None and (args.mouse_only or (ready.mode in (1, 2) and ready.weapons)):
                    break
                before = signature()
                raw(AVK_UP if before == '0f62be81305c' else AVK_SELECT,
                    2 if before == '0f62be81305c' else .12)
                drive(.6)
                if signature() == before:
                    raw(AVK_SOFT2)
                if step % 10 == 0:
                    print(f'boot step {step}', flush=True)
            require(frontend.controls.state() is not None, 'real gameplay reached')
            frontend.capture_pointer()
            drive(.1)
            if root.tk.call('tk', 'windowingsystem') == 'x11':
                require(frontend.pointer_capture.active, 'gameplay confines the native pointer')
            state = frontend.controls.state()
            require(state is not None, 'input context recognises gameplay')
            m = boot.m
            camera = m.u32(state.player + 0x2b8)

            def position():
                return m.read(camera + 0xac, 12)

            def orientation():
                return m.read(state.player + 0x278, 4)

            trace = []
            input_object = m.u32(m.u32(boot.applet + 0x20) + 0x1c8 + frontend.controls.game_delta)
            def traced_tick():
                trace.append((boot.runtime.guest_ticks, int.from_bytes(orientation(), 'little'),
                              frontend.look['yaw'].holding, frontend.look['yaw'].owed,
                              m.read(state.player + 0x3d4, 2).hex(),
                              m.u32(input_object + 4 + (AVK_6 - 0xe020) * 4),
                              m.read(m.u32(boot.applet + 0x20) + 0x1b8 + frontend.controls.game_delta, 1)[0]))
                frontend._after_guest_tick()
            boot.runtime.on_tick = traced_tick

            # Measure actual camera/position fields, independently of the
            # frontend's key logs and ambient framebuffer animation.
            if not args.mouse_only:
                for sym in ('a', 'd', 'w', 's'):
                    before, facing = position(), orientation()
                    key(sym)
                    require(position() != before, f'{sym} moves the real player')
                    require(orientation() == facing, f'{sym} preserves heading')

                frontend.control_keys = ControlSettings.from_dict({'bindings': {'forward': 'j'}}).translations()
                before, facing = position(), orientation()
                key('w')
                require(position() == before, 'rebound forward no longer responds to W')
                key('j')
                require(position() != before and orientation() == facing,
                        'rebound J moves the real player without turning')
                require(AVK_2 not in boot.runtime.key_down_ticks, 'rebound movement releases in the guest')
                frontend.control_keys = ControlSettings().translations()

            for dx, dy, name in ((26, 0, 'right'), (-26, 0, 'left'),
                                 (0, 32, 'down'), (0, -32, 'up')):
                frontend._warp_pointer()
                drive(.05)
                aim = orientation if dx else lambda: m.read(camera + 0xbc, 4)
                before = aim()
                trace.clear()
                cx, cy = frontend.center
                canvas.event_generate('<Motion>', warp=True, x=cx + dx, y=cy + dy)
                drive(.5)
                require(aim() != before, f'mouse {name} changes real camera')
                if dx:
                    delta = (int.from_bytes(orientation(), 'little') -
                             int.from_bytes(before, 'little')) & 0xffffffff
                    expected = round(-dx / PIXELS_PER_TURN_TICK * 0xa0000) & 0xffffffff
                    if delta != expected:
                        print('mouse trace', trace, flush=True)
                    require(delta == expected, f'mouse {name} spends the requested yaw travel '
                            f'(delta={delta:#x}, expected={expected:#x})')
                require(all(k is None for k in frontend.look_keys_held.values()),
                        f'mouse {name} stops after spending travel')

            # A large flick used to leave up to 32 native turn steps queued.
            # It must complete in the first host slice and remain stationary.
            frontend._warp_pointer()
            drive(.05)
            cx, cy = frontend.center
            before = int.from_bytes(orientation(), 'little')
            canvas.event_generate('<Motion>', warp=True, x=cx + 130, y=cy)
            root.update()
            frontend._apply_look()
            expected = (before + round(-130 / PIXELS_PER_TURN_TICK * 0xa0000)) & 0xffffffff
            require(int.from_bytes(orientation(), 'little') == expected,
                    'large flick applies all travel in one host slice')
            require(frontend.look['yaw'].owed == 0, 'large flick leaves no turn debt')
            stopped = orientation()
            drive(.8)
            require(orientation() == stopped, 'camera stays stopped after flick for 0.8 seconds')
            frontend.controls.rotate_view(-130 / PIXELS_PER_TURN_TICK, 0)

            # Fractions smaller than one old keypad step must aim immediately.
            frontend._warp_pointer()
            drive(.05)
            before = orientation()
            canvas.event_generate('<Motion>', warp=True, x=cx + 2, y=cy + 2)
            root.update()
            frontend._apply_look()
            require(orientation() != before, 'small diagonal movement aims without a whole-step threshold')
            frontend.controls.rotate_view(-2 / PIXELS_PER_TURN_TICK, -2 / PIXELS_PER_PITCH_TICK)

            # Escape must open pause and drop mouse look; Enter resumes it.
            key('Escape', .01)
            require(frontend.controls.state() is None and not frontend.captured,
                    'Escape pauses and releases pointer')
            pause = frontend.controls.menu()
            require(pause is not None and pause.kind == 0x101, 'native pause overlay recognised')
            if len(pause.rows) > 1:
                click(menu_x, dict(pause.rows)[8] + 6)
                require(until(lambda: frontend.controls.menu() and frontend.controls.menu().kind == 0x102),
                        'mouse opens pause Options')
                click(menu_x, 20, button=3)
                require(until(lambda: frontend.controls.menu() and frontend.controls.menu().kind == 0x101),
                        'right click returns from pause Options')
            else:
                require(pause.rows[0][0] == 1, 'scripted scene exposes only native Resume action')
            click(menu_x, frontend.controls.menu().rows[0][1] + 6)
            require(until(lambda: frontend.controls.state() is not None),
                    f'click on Resume resumes gameplay (context={frontend.controls.menu()})')
            require(frontend.captured, 'resuming recaptures mouse')
            if root.tk.call('tk', 'windowingsystem') == 'x11':
                require(frontend.pointer_capture.active, 'resuming restores native confinement')
            if args.mouse_only:
                require(frontend.error is None and not errors, 'session has no guest or Tk faults')
                print(f'MOUSE MENU CHECK PASSED ({args.profile})', flush=True)
                return

            delivered = []
            frontend.on_key = lambda name, avk, down: delivered.append((avk, down))
            for sym, avk in (('e', AVK_0), ('r', AVK_SOFT1), ('f', AVK_SOFT2)):
                key(sym, .001)
                require((avk, True) in delivered and (avk, False) in delivered,
                        f'{sym} delivers its correct action and release')
                require(frontend.controls.state() is not None,
                        f'{sym} stays in gameplay')
            # A press and release queued within the same event-loop turn
            # must still run one actual guest update with attack held.
            frontend.mouse_ref = frontend.center
            cx, cy = frontend.center
            canvas.event_generate('<Button-1>', x=cx, y=cy)
            canvas.event_generate('<ButtonRelease-1>', x=cx, y=cy)
            drive(.3)
            require((AVK_5, True) in delivered and (AVK_5, False) in delivered,
                    'short left click delivers attack and release')
            require(AVK_5 not in boot.runtime.key_down_ticks, 'attack is released in guest')

            # Aliases share one guest key; releasing W must not stop held KP2.
            root.event_generate('<KeyPress>', keysym='w')
            root.event_generate('<KeyPress>', keysym='KP_2')
            drive(.12)
            root.event_generate('<KeyRelease>', keysym='w')
            drive(.12)
            require(AVK_2 in boot.runtime.key_down_ticks, 'alias remains down after W release')
            root.event_generate('<KeyRelease>', keysym='KP_2')
            drive(.12)
            require(AVK_2 not in boot.runtime.key_down_ticks, 'last alias releases guest movement')

            # Raw phone keys remain available on the numeric keypad; number
            # row weapon selection must never move the player.
            before = position()
            for sym in ('1', '2', '3', '4', '5'):
                key(sym, .01)
            require(position() == before, 'number row selects equipment without moving')
            key('F1', .01)
            require(frontend.controls.state() is not None, 'plasmid slot key stays in gameplay')

            # Provision later-level equipment in the disposable guest using
            # the same constructors, virtual initializers and owner setter as
            # the native player initializer at module offsets 0x402f4–0x40374.
            for slot, constructor in ((1, 0xe3bd4), (2, 0xe5da0)):
                address = state.player + 0x1f8 + slot * 4
                if not m.u32(address):
                    item = m.call(m.load_base + constructor, [0])
                    require(bool(item), f'native constructor creates weapon {slot}')
                    m.w32(address, item)
                    table = m.u32(item)
                    m.call((table + m.u32(table + 4)) & 0xffffffff, [item])
                    m.call(m.load_base + 0x40b0, [item, state.player])
            if not m.u32(state.player + 0x210):
                item = m.call(m.load_base + 0xe5fa8, [0])
                require(bool(item), 'native constructor creates second plasmid')
                m.w32(state.player + 0x210, item)
                table = m.u32(item)
                m.call((table + m.u32(table + 4)) & 0xffffffff, [item])
            # The production adapter only reads ownership and sends keys.
            for offset, count, flag, owned in ((0x1f8, 5, 0x5b, 3),
                                               (0x20c, 3, 0x56, 2)):
                for slot in range(count):
                    item = m.u32(state.player + offset + slot * 4)
                    if slot < owned:
                        require(item >= m.heap_base and m.contains(item, flag + 1),
                                f'equipment fixture has slot {offset:#x}/{slot}')
                    if item:
                        m.write(item + flag, bytes([int(slot < owned)]))
            for sym, mode, slot in (('2', 1, 1), ('3', 1, 2), ('1', 1, 0),
                                     ('F2', 2, 1), ('F1', 2, 0)):
                key(sym, .01)
                until(lambda: equipped(mode, slot))
                selected = frontend.controls.state()
                if not selected or selected.mode != mode or (selected.weapon if mode == 1 else selected.plasmid) != slot:
                    print('equipment context', selected, 'request', frontend.equipment_request,
                          'attempts', frontend.equipment_attempts, 'native pending',
                          m.read(state.player + 0x22d, 7).hex(), flush=True)
                require(selected is not None and selected.mode == mode and
                        (selected.weapon if mode == 1 else selected.plasmid) == slot,
                        f'{sym} equips requested owned slot through native controls')
            for mode in (1, 2):
                frontend.mouse_ref = frontend.center
                canvas.event_generate('<Button-3>', x=cx, y=cy)
                canvas.event_generate('<ButtonRelease-3>', x=cx, y=cy)
                until(lambda: frontend.controls.state() and
                      frontend.controls.state().mode == mode)
                require(frontend.controls.state().mode == mode,
                        f'right-click toggles to equipment mode {mode}')
            key('1', .01)
            require(until(lambda: equipped(1, 0)), 'weapon mode restored for wheel checks')
            for button, slot in ((4, 1), (5, 0), (5, 2)):
                canvas.event_generate(f'<Button-{button}>', x=cx, y=cy)
                until(lambda: equipped(1, slot))
                require(frontend.controls.state().weapon == slot,
                        f'wheel button {button} selects weapon {slot}')

            root.event_generate('<KeyPress>', keysym='w')
            canvas.event_generate('<Button-1>', x=cx, y=cy)
            drive(.12)
            # A separate process is essential: two Tk interpreters in one
            # process can share Tk's display-level focus bookkeeping.
            other = subprocess.Popen([sys.executable, '-c',
                'import tkinter as t; r=t.Tk(); r.geometry("80x80+500+0"); '
                'r.update(); r.focus_force(); r.update(); '
                'print("focused", flush=True); r.after(3000, r.destroy); r.mainloop()'],
                stdout=subprocess.PIPE, text=True)
            try:
                require(other.stdout.readline().strip() == 'focused', 'another process owns focus')
                drive(.2)
                require(not frontend.held and not frontend.input_sources,
                        'real focus loss clears every input owner')
                require(not boot.runtime.key_down_ticks, 'real focus loss releases guest keys')
                require(not frontend.pointer_capture.active, 'focus loss releases native confinement')
            finally:
                if other.poll() is None:
                    other.terminate()
                other.wait(timeout=3)
                other.stdout.close()
            root.update()
            root.focus_force()
            drive(.1)
            delivered.clear()
            canvas.event_generate('<Button-1>', x=cx, y=cy)
            canvas.event_generate('<ButtonRelease-1>', x=cx, y=cy)
            drive(.15)
            require(frontend.captured and not delivered, 'recapture click does not fire')
            require(not errors and frontend.error is None, 'session has no guest or Tk faults')
            require(frontend.frames_drawn > 0, 'real frontend rendered frames')
            print('INPUT CHECK PASSED', flush=True)
        finally:
            if frontend is not None and not frontend.closed:
                frontend._on_close()
            boot.shutdown()


if __name__ == '__main__':
    main()
