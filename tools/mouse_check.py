#!/usr/bin/env python3
"""Smoke test for the mouse: capture, deltas, warps, buttons.

Opens the real frontend (a display is needed, same as ``make smoke``), drives
the pointer across the canvas with Tk's warp events and asserts the things
that make the mouse behave like a PC shooter's captured mouse:

  * capture parks the pointer in the middle of the canvas and hides the cursor,
  * Tab hands the pointer back, cursor and all, and a click captures it again
    without that click also firing,
  * a burst of motion events is counted once each - the look tracks the
    *distance* the mouse travelled, whether Tk delivers one event or twenty
    (warps and the X server both add events; consecutive deltas telescope, so
    the total must equal the total travel),
  * motion near an edge warps the pointer home, and the events already in
    flight describe the pre-warp position, so they are dropped,
  * fast sweeps beyond every edge stay confined even before Tk handles events,
  * aiming keeps working while a button is held (Tk reports that motion as
    ``<B1-Motion>``, which used to be bound to nothing),
  * mouse travel holds the look key the phone keypad would hold (6 for a
    rightward sweep) and lets it go once the game has turned,
  * a <FocusOut> that is just the focus moving between this window's widgets
    does not release the mouse, but really losing the window focus does.

Prints one line per check and exits non-zero when any of them fails.
"""
from __future__ import annotations

import argparse
import ctypes as C
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, ".")
sys.path.insert(0, "tools")

from boot_check_paths import DATA_DIR, GAME_DIR  # noqa: E402

from bio3dpc.boot import Boot  # noqa: E402
from bio3dpc.frontend import Frontend  # noqa: E402
from bio3dpc.services import AVK_5, AVK_6  # noqa: E402

CENTER_TOLERANCE = 3    # pixels the parked pointer may be off the canvas centre
BURST_DX = 10           # five events of 10 px: 50 px of travel in a burst
SETTLE_MS = 40          # short waits keep a warp younger than its resync
                        # timeout while still letting it land


class Checker:
    """Collects pass/fail lines; the process exit code mirrors them."""

    def __init__(self):
        self.failures = 0

    def check(self, ok: bool, what: str, detail: str = "") -> bool:
        mark = "ok  " if ok else "FAIL"
        print(f"{mark} {what}{' - ' + detail if detail else ''}", flush=True)
        if not ok:
            self.failures += 1
        return ok

    def near(self, got, want, what: str, tolerance: int = 0) -> bool:
        ok = abs(got - want) <= tolerance
        return self.check(ok, what, f"got {got}, want {want}"
                          + (f" +/-{tolerance}" if tolerance else ""))


class MouseProbe:
    """Scripted mouse session against a live Frontend."""

    def __init__(self, frontend: Frontend, checker: Checker):
        self.frontend = frontend
        self.checker = checker
        self.root = frontend.root
        self.canvas = frontend.canvas
        self.cx, self.cy = frontend.center
        self.keys: list[tuple[str, int, bool]] = []
        self.deltas: list[tuple[str, float]] = []
        self.b1_motions = 0
        self.phases = [self.startup, self.toggle_click, self.burst,
                       self.edge_warp, self.held_button, self.confined_sweeps,
                       self.tab_release, self.focus_loss]
        frontend.on_key = lambda sym, avk, press: self.keys.append(
            (sym, avk, press))
        for axis, budget in frontend.look.items():
            self._record_axis(axis, budget)
        # Count what the <B1-Motion> binding itself sees, so the held-button
        # check can tell which binding delivered the motion.
        self.canvas.bind("<B1-Motion>",
                         lambda event: setattr(self, "b1_motions",
                                               self.b1_motions + 1),
                         add="+")

    # ------------------------------------------------------------- plumbing
    def _record_axis(self, axis: str, budget) -> None:
        original = budget.add

        def add(pixels: float) -> None:
            self.deltas.append((axis, pixels))
            original(pixels)

        budget.add = add

    def pointer(self) -> tuple[int, int]:
        px, py = self.root.winfo_pointerxy()
        return (px - self.canvas.winfo_rootx(),
                py - self.canvas.winfo_rooty())

    def motion(self, x: int, y: int, state: int = 0) -> None:
        """One motion event that moves the real pointer, like the mouse does."""
        self.canvas.event_generate("<Motion>", state=state, warp=True, x=x, y=y)

    def click(self) -> None:
        self.canvas.event_generate("<Button-1>", x=self.cx, y=self.cy)
        self.canvas.event_generate("<ButtonRelease-1>", x=self.cx, y=self.cy)

    def tab(self) -> None:
        self.root.event_generate("<KeyPress>", keysym="Tab")
        self.root.event_generate("<KeyRelease>", keysym="Tab")

    def wait(self, ms: int = 80) -> None:
        """Run the Tk event loop for a moment so queued events land."""
        deadline = time.monotonic() + ms / 1000.0
        while time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.002)

    def park_ref(self) -> None:
        """Warp home and reset the bookkeeping to 'pointer at the centre'.

        Warps keep a burst's positions absolute, but every burst has to start
        from a known reference: the canvas centre, exactly where the frontend
        parks the pointer while captured.
        """
        self.motion(self.cx, self.cy)
        self.wait()
        frontend = self.frontend
        if not frontend.captured:
            frontend.capture_pointer()  # focus was stolen mid-run
            self.wait()
        frontend.mouse_ref = (self.cx, self.cy)
        frontend.warp_pending = False
        self.skip_deltas()

    def yaw_deltas(self) -> list[float]:
        return [d for axis, d in self.deltas if axis == "yaw"]

    def pitch_deltas(self) -> list[float]:
        return [d for axis, d in self.deltas if axis == "pitch"]

    def skip_deltas(self) -> None:
        self.deltas.clear()

    def key_names(self) -> list[str]:
        return [name for name, _, _ in self.keys]

    def pressed(self, avk: int) -> bool:
        return any(key == avk and press for _, key, press in self.keys)

    def released(self, avk: int) -> bool:
        return any(key == avk and not press for _, key, press in self.keys)

    # -------------------------------------------------------------- phases
    def startup(self) -> None:
        """Startup capture: the pointer is parked and the cursor is hidden."""
        checker = self.checker
        checker.check(self.frontend.captured, "mouse look starts captured")
        self.park_ref()
        x, y = self.pointer()
        checker.near(x, self.cx, "pointer parked at centre x", CENTER_TOLERANCE)
        checker.near(y, self.cy, "pointer parked at centre y", CENTER_TOLERANCE)
        checker.check(str(self.canvas.cget("cursor")) == "none",
                      "cursor hidden while captured")

    def toggle_click(self) -> None:
        """Tab frees the pointer; the click that captures it does not fire."""
        checker = self.checker
        self.tab()
        self.wait()
        checker.check(not self.frontend.captured, "Tab frees the pointer")
        checker.check(str(self.canvas.cget("cursor")) != "none",
                      "cursor visible while free")
        self.keys.clear()
        self.click()
        self.wait()
        checker.check(self.frontend.captured, "click captures the pointer")
        checker.check(not self.pressed(AVK_5),
                      "the capturing click does not fire",
                      f"keys={self.key_names()}")
        checker.check(str(self.canvas.cget("cursor")) == "none",
                      "cursor hidden again after capture")

    def burst(self) -> None:
        """A burst of motion events counts the travel once, not once per event."""
        checker = self.checker
        self.park_ref()
        for step in range(1, 6):        # five events queued back to back
            self.motion(self.cx + step * BURST_DX, self.cy)
        self.wait()
        checker.near(sum(self.yaw_deltas()), 5 * BURST_DX,
                     "horizontal burst counts each pixel once", tolerance=1)
        self.park_ref()
        for step in range(1, 5):
            self.motion(self.cx, self.cy + step * BURST_DX)
        self.wait()
        checker.near(sum(self.pitch_deltas()), 4 * BURST_DX,
                     "vertical burst counts each pixel once", tolerance=1)

    def edge_warp(self) -> None:
        """Near the edge the pointer is warped home; stale events are dropped."""
        checker = self.checker
        self.park_ref()
        self.motion(10, self.cy)        # walks to the left edge
        self.wait(SETTLE_MS)
        x, y = self.pointer()
        checker.near(x, self.cx, "edge warp returns pointer home", CENTER_TOLERANCE)
        checker.near(y, self.cy, "edge warp preserves centre y", CENTER_TOLERANCE)
        checker.near(sum(self.yaw_deltas()), 10 - self.cx,
                     "edge travel counted without synthetic warp travel", tolerance=1)
        self.skip_deltas()
        self.motion(self.cx + 7, self.cy + 3)   # first event after the warp
        self.wait(SETTLE_MS)
        checker.near(sum(self.yaw_deltas()), 7,
                     "post-warp event counts from the warp target",
                     tolerance=1)
        checker.check(not self.frontend.warp_pending,
                      "the warp is settled once the event lands")

    def held_button(self) -> None:
        """Motion while a button is held still aims (Tk: <B1-Motion>)."""
        checker = self.checker
        self.park_ref()
        self.keys.clear()
        self.b1_motions = 0
        self.click()
        self.wait()
        checker.check(self.pressed(AVK_5), "click while captured fires",
                      f"keys={self.key_names()}")
        checker.check(self.released(AVK_5), "releasing the button fires key up",
                      f"keys={self.key_names()}")
        self.skip_deltas()
        # Cross the fallback's whole-tick threshold, independent of sensitivity.
        distance = round(self.frontend.look['yaw'].pixels_per_tick * 2)
        self.motion(self.cx + distance, self.cy, state=0x100)   # Button1Mask
        self.wait()
        checker.near(sum(self.yaw_deltas()), distance,
                     "motion with button held still aims", tolerance=1)
        checker.check(self.b1_motions > 0,
                      "the <B1-Motion> binding delivered the move")
        checker.check(self.pressed(AVK_6),
                      "a rightward sweep holds key 6 (turn right)",
                      f"keys={self.key_names()}")

    def confined_sweeps(self) -> None:
        """Fast sweeps cannot escape even while Tk's event loop is busy."""
        if self.root.tk.call('tk', 'windowingsystem') != 'x11':
            return
        lib = C.CDLL('libX11.so.6')
        lib.XOpenDisplay.argtypes = [C.c_char_p]
        lib.XOpenDisplay.restype = C.c_void_p
        lib.XWarpPointer.argtypes = [C.c_void_p, C.c_ulong, C.c_ulong,
                                    C.c_int, C.c_int, C.c_uint, C.c_uint,
                                    C.c_int, C.c_int]
        lib.XSync.argtypes = [C.c_void_p, C.c_int]
        lib.XCloseDisplay.argtypes = [C.c_void_p]
        display = lib.XOpenDisplay(None)
        if not display:
            raise RuntimeError('could not open independent pointer connection')
        try:
            def move(x, y):
                # A separate X client moves the actual pointer. No synthetic
                # Tk event and no event-loop turn can rescue an escaped cursor.
                lib.XWarpPointer(display, 0, self.canvas.winfo_id(),
                                 0, 0, 0, 0, x, y)
                lib.XSync(display, False)
                return self.pointer()

            self.park_ref()
            self.checker.check(self.frontend.pointer_capture.active,
                               'gameplay holds native pointer confinement')
            width, height = self.frontend.canvas_size
            for name, target in [('left', (-200, self.cy)),
                                 ('right', (width + 200, self.cy)),
                                 ('top', (self.cx, -200)),
                                 ('bottom', (self.cx, height + 200))] * 3:
                self.park_ref()
                x, y = move(*target)
                self.checker.check(0 <= x < width and 0 <= y < height,
                                   f'fast {name} sweep stays inside before Tk runs',
                                   f'pointer=({x}, {y})')
                self.wait(SETTLE_MS)
                self.checker.check(bool(self.yaw_deltas() or self.pitch_deltas()),
                                   f'fast {name} sweep still aims')
                self.skip_deltas()
                move(self.cx + 7, self.cy)
                self.wait(SETTLE_MS)
                self.checker.near(sum(self.yaw_deltas()), 7,
                                  'aiming continues after the sweep', tolerance=1)
            self.tab()
            self.wait()
            x, y = move(width + 100, self.cy)
            self.checker.check(x >= width, 'Tab releases native confinement')
            self.frontend.capture_pointer()
            self.wait()
        finally:
            lib.XCloseDisplay(display)

    def tab_release(self) -> None:
        """Tab frees the pointer; the driven look keys are all let go."""
        checker = self.checker
        releases = [name for name, _, press in self.keys if not press]
        checker.check(any(name.startswith("mouse-") for name in releases),
                      "a driven look key was let go again",
                      f"releases={releases}")
        self.tab()
        self.wait()
        checker.check(not self.frontend.captured, "Tab releases the pointer")
        checker.check(all(key is None
                          for key in self.frontend.look_keys_held.values()),
                      "release holds no look keys")
        checker.check(str(self.canvas.cget("cursor")) != "none",
                      "cursor comes back with the pointer")

    def focus_loss(self) -> None:
        """Only losing the window focus gives the mouse back."""
        checker = self.checker
        if not self.frontend.captured:
            self.frontend.capture_pointer()
            self.wait()
        # The focus moving between our own widgets shows up as a FocusOut on
        # the toplevel too; it must not release the mouse (that bug made the
        # capturing click free the pointer again right after it captured).
        self.root.event_generate("<FocusOut>")
        self.wait()
        checker.check(self.frontend.captured,
                      "internal focus moves do not release the mouse")
        self.root.withdraw()            # a real focus loss, also without a WM
        self.wait(300)
        checker.check(not self.frontend.captured,
                      "losing the window focus releases the mouse")
        checker.check(str(self.canvas.cget("cursor")) != "none",
                      "cursor comes back on focus loss")
        self.root.deiconify()


def run(frontend: Frontend, checker: Checker, interval: int = 400) -> None:
    probe = MouseProbe(frontend, checker)

    def step(index: int = 0) -> None:
        if index >= len(probe.phases):
            frontend._on_close()
            return
        name = probe.phases[index].__name__
        print(f"--- {name}", flush=True)
        try:
            probe.phases[index]()
        except Exception as error:  # a broken phase must not hang the window
            checker.check(False, f"{name} raised", repr(error))
            frontend._on_close()
            return
        frontend.root.after(interval, step, index + 1)

    frontend.root.after(interval, step, 0)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--warmup", type=float, default=3.0,
                        help="seconds to let the game boot before the window")
    parser.add_argument("--scale", type=int, default=2)
    parser.add_argument("--level", default="warn")
    parser.add_argument("--display", help="X display for an isolated GUI test")
    args = parser.parse_args()
    if args.display:
        os.environ['DISPLAY'] = args.display
    os.environ['BIO3DPC_AUDIO'] = '0'
    with tempfile.TemporaryDirectory(prefix='bio3d-mouse-check-') as work:
        shutil.copytree(DATA_DIR, work, dirs_exist_ok=True)
        boot = Boot(f"{GAME_DIR}/bio3d.mod", data_dir=work, level=args.level)
        frontend = None
        try:
            boot.load_module()
            boot.create_applet()
            boot.app_start()
            boot.run(seconds=args.warmup)
            checker = Checker()
            frontend = Frontend(boot, scale=args.scale)
            # Pointer transport is independent of context routing, which is
            # covered against real gameplay by tools/input_check.py.
            frontend.controls.supported = False
            frontend.capture_pointer()
            run(frontend, checker)
            frontend.run()
            checker.check(frontend.error is None, 'no guest fault', frontend.error or '')
        finally:
            if frontend is not None and not frontend.closed:
                frontend._on_close()
            boot.shutdown()
    print(f"draw {frontend.draw_time * 1000:.1f}ms "
          f"frames_drawn={frontend.frames_drawn} error={frontend.error}")
    print(f"{checker.failures} failed check(s)")
    if not checker.failures:
        print('MOUSE CHECK PASSED')
    return 1 if checker.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
