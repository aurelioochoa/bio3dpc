"""Tkinter window frontend: shows the device bitmap and forwards PC input.

The host plays the phone (screen + keypad):

    * the emulator runs in short slices from Tk's event loop,
    * every new presented frame is copied out of the device bitmap and shown,
    * PC key presses are translated to BREW AVK_* codes and delivered to the
      applet as EVT_KEY / EVT_KEY_RELEASE,
    * verified builds apply relative mouse travel through native camera
      rotation methods; other images use the phone's look keys,
    * menus keep a visible pointer, with native row highlighting and clicks;
      gameplay hides and recenters the pointer for relative aiming.

No third-party dependencies: tkinter ships with CPython, and frames are handed
to Tk as binary PPM, which Tk's built-in image reader parses directly.
"""
from __future__ import annotations

import signal
import time
from pathlib import Path
import tkinter as tk

from .machine import GuestError
from .controls import GameControls
from .input_settings import ControlSettings, normalize_key
from .pointer import PointerCapture
from .services import (
    AVK_0, AVK_1, AVK_2, AVK_3, AVK_4, AVK_5, AVK_6, AVK_7, AVK_8, AVK_9,
    AVK_CLR, AVK_DOWN, AVK_END, AVK_FUNCTION1, AVK_FUNCTION2, AVK_FUNCTION3,
    AVK_LEFT, AVK_POUND, AVK_RIGHT, AVK_SELECT, AVK_SEND, AVK_SOFT1, AVK_SOFT2,
    AVK_SOFT3, AVK_SOFT4, AVK_STAR, AVK_UP,
)

# X11 queues repeat release/press pairs together. An idle callback distinguishes
# those pairs without adding 60 ms of movement after a real release.

# --- mouse look ------------------------------------------------------------
# Sensitivity is expressed in native look-step units. Verified builds consume
# fractional steps through the native camera methods in one host slice. Unknown
# images fall back to guest-tick key holds, retaining the original rate.
PIXELS_PER_TURN_TICK = 52.0
PIXELS_PER_PITCH_TICK = 64.0
LOOK_MAX_TICKS = 32         # cap one movement at ~1.7 s of gameplay hold
LOOK_MIN_TICKS = 1.0        # the camera only steps on a whole guest tick

# The pointer is confined to the canvas during gameplay and warped to the centre
# whenever it comes within this many pixels of a side.  Events already in
# flight when the warp is issued describe the pre-warp position; they are
# dropped until one clearly belongs to the post-warp stream (or the resync
# timeout passes, for window managers that refuse warps).
EDGE_WARP_MARGIN = 48
WARP_RESYNC_TIMEOUT = 0.15
# PC actions backed by the phone's real bindings. Space confirms prompts:
# this build has no player jump/crouch action.
KEYMAP = {
    # movement / view (phone: 2/8 forward/back, 7/9 strafe, 4/6 turn)
    "w": AVK_2, "W": AVK_2,
    "s": AVK_8, "S": AVK_8,
    "a": AVK_7, "A": AVK_7,
    "d": AVK_9, "D": AVK_9,
    "Up": AVK_UP, "Down": AVK_DOWN, "Left": AVK_LEFT, "Right": AVK_RIGHT,
    # action
    "e": AVK_0, "E": AVK_0, "Return": AVK_SELECT, "KP_Enter": AVK_SELECT,
    "r": AVK_SOFT1, "R": AVK_SOFT1,
    "f": AVK_SOFT2, "F": AVK_SOFT2,
    "q": AVK_LEFT, "Q": AVK_LEFT,
    "space": AVK_SELECT,
    '@fire': AVK_5, '@mode': AVK_1,
    "Escape": AVK_CLR, "BackSpace": AVK_CLR,
    # soft keys: F2 is SKIP on the cut scenes (right soft key)
    "F1": AVK_SOFT1, "F2": AVK_SOFT2, "F3": AVK_SOFT3, "F4": AVK_SOFT4,
    "F5": AVK_FUNCTION1, "F6": AVK_FUNCTION2, "F7": AVK_FUNCTION3,
    "F10": AVK_SOFT1, "F11": AVK_SOFT2,
    "Home": AVK_SEND, "End": AVK_END,
    "0": AVK_0, "1": AVK_1, "2": AVK_2, "3": AVK_3, "4": AVK_4,
    "5": AVK_5, "6": AVK_6, "7": AVK_7, "8": AVK_8, "9": AVK_9,
    "asterisk": AVK_STAR, "KP_Multiply": AVK_STAR,
    "numbersign": AVK_POUND, "KP_Add": AVK_POUND,
    **{f"KP_{i}": AVK_0 + i for i in range(10)},
    "KP_Insert": AVK_0, "KP_End": AVK_1, "KP_Down": AVK_2,
    "KP_Next": AVK_3, "KP_Left": AVK_4, "KP_Begin": AVK_5,
    "KP_Right": AVK_6, "KP_Home": AVK_7, "KP_Up": AVK_8,
    "KP_Prior": AVK_9,
}

# Mouse buttons delivered as phone keys.
MOUSE_BUTTONS = {"<Button-1>": AVK_5, "<Button-2>": AVK_LEFT,
                 "<Button-3>": AVK_1}

# Names reported for the mouse-driven look keys (see Frontend._apply_look).
LOOK_NAMES = {AVK_4: "mouse-left", AVK_6: "mouse-right",
              AVK_UP: "mouse-up", AVK_DOWN: "mouse-down"}


def ppm_bytes(width: int, height: int, rgb: bytes) -> bytes:
    """Binary PPM (P6) header + top-down RGB888 rows."""
    return b"P6\n%d %d\n255\n" % (width, height) + rgb


class LookBudget:
    """Owed look-key hold for one axis, counted in guest ticks.

    Mouse travel adds to the budget (pixels / ``pixels_per_tick``); every
    guest tick that fires while the key is held pays one owed tick back,
    because that is when the game stepped its camera.  The key is held while a
    whole tick is owed: a hold shorter than a tick is invisible to the build,
    which samples its key state once per tick.  The positive key turns towards
    positive mouse movement (``6`` for yaw, ``DOWN`` for pitch).
    """

    def __init__(self, pos: int, neg: int, pixels_per_tick: float,
                 max_ticks: float = LOOK_MAX_TICKS,
                 min_ticks: float = LOOK_MIN_TICKS):
        self.pos = pos
        self.neg = neg
        self.pixels_per_tick = max(0.05, pixels_per_tick)
        self.max_ticks = max_ticks
        self.min_ticks = min_ticks
        self.owed = 0.0
        self.holding: int | None = None

    def add(self, pixels: float) -> None:
        """Owe the hold that ``pixels`` of mouse travel is worth."""
        owed = self.owed + pixels / self.pixels_per_tick
        self.owed = max(-self.max_ticks, min(self.max_ticks, owed))

    def hold(self, ticks: int = 0) -> int | None:
        """Advance by the guest ``ticks`` that fired; return the key to hold.

        ``ticks`` are the guest ticks that fired while the key returned last
        time was held: each of them stepped the camera one step in that key's
        direction, so the debt is charged in that direction.  The budget is
        then the distance between where the camera is and where the mouse says
        it should be, which is what makes the look track the pointer exactly.
        """
        if ticks and self.holding is not None:
            if self.holding == self.pos:
                self.owed -= ticks
            else:
                self.owed += ticks
        if self.owed >= self.min_ticks:
            self.holding = self.pos
        elif self.owed <= -self.min_ticks:
            self.holding = self.neg
        else:
            self.holding = None
        return self.holding

    def reset(self) -> None:
        self.owed = 0.0
        self.holding = None


class Frontend:
    """Runs the emulator slices and mirrors the device bitmap into a window."""

    def __init__(self, boot, scale: int = 3, slice_seconds: float = 0.02,
                 on_key=None, mouse_look: bool | None = None,
                 mouse_sensitivity: float | None = None, *,
                 control_settings: ControlSettings | None = None, invert_y: bool | None = None):
        settings = control_settings or ControlSettings()
        self.control_keys = settings.translations()
        self.invert_y = settings.invert_y if invert_y is None else invert_y
        mouse_look = settings.mouse_look if mouse_look is None else mouse_look
        mouse_sensitivity = settings.sensitivity if mouse_sensitivity is None else mouse_sensitivity
        self.boot = boot
        self.log = boot.log
        self.scale = max(1, int(scale))
        self.slice = slice_seconds
        self.on_key = on_key
        self.controls = GameControls(boot, mouse_look=mouse_look)
        if self.controls.supported:
            self.boot.runtime.input_clock = self.controls.input_tick
        self.equipment_request = None
        self.equipment_due = 0
        self.equipment_attempts = 0
        self.held: set[str] = set()
        self.physical_keys: dict[int, str] = {}
        self.input_sources: dict[str, int] = {}
        self.pending_release: dict[str, str] = {}
        self.menu_taps: dict[str, tuple[int, tuple[int, int] | None]] = {}
        self.frame_seen = -1
        self.error: str | None = None
        self.closed = False
        self.frames_drawn = 0
        self.draw_time = 0.0
        self.last_title = 0.0

        self.root = tk.Tk(className='Bio3dpc')
        icon = Path(__file__).resolve().parent/'ui_assets/icon.png'
        if icon.is_file():
            self._icon = tk.PhotoImage(file=str(icon)).subsample(8)
            self.root.iconphoto(True, self._icon)
        self.root.title("BioShock 3D (BREW) - bio3dpc")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        width = self._screen_width()
        height = self._screen_height()
        self.canvas_size = (width * self.scale, height * self.scale)
        self.center = (width * self.scale // 2, height * self.scale // 2)
        self.canvas = tk.Canvas(self.root, width=width * self.scale,
                                height=height * self.scale,
                                background="black", highlightthickness=0)
        self.canvas.pack()
        self.pointer_capture = PointerCapture(self.root, self.canvas)
        self.item = self.canvas.create_image(0, 0, anchor="nw")
        self.photo = None
        self.status = self.canvas.create_text(
            width * self.scale // 2, height * self.scale // 2,
            text="loading...", fill="#808080", font=("Helvetica", 12))

        # Pending relative travel per axis, in original keypad-step units.
        # Native rotation consumes fractions; the fallback uses key holds.
        self.look_enabled = bool(mouse_look)
        self.captured = self.look_enabled and (
            not self.controls.supported or self.controls.state() is not None)
        self._set_mouse_cursor(hidden=self.captured)
        sensitivity = max(0.05, mouse_sensitivity)
        self.look = {
            "yaw": LookBudget(AVK_6, AVK_4,
                              PIXELS_PER_TURN_TICK / sensitivity,
                              max_ticks=float('inf') if self.controls.supported else LOOK_MAX_TICKS),
            "pitch": LookBudget(AVK_DOWN, AVK_UP,
                                PIXELS_PER_PITCH_TICK / sensitivity,
                                max_ticks=float('inf') if self.controls.supported else LOOK_MAX_TICKS),
        }
        # the look key currently delivered for each axis (4/6, UP/DOWN)
        self.look_keys_held: dict[str, int | None] = {"yaw": None, "pitch": None}
        self.guest_ticks_seen = 0
        self.look_ticks_seen = self.controls.look_ticks
        self.was_gameplay = self.controls.state() is not None
        self.boot.runtime.on_tick = self._after_guest_tick
        # pointer bookkeeping: where the pointer was last seen (canvas
        # coordinates), and the in-flight warp that is moving it back to the
        # centre (``mouse_ref`` is the warp target while one is pending).
        self.mouse_ref: tuple[int, int] | None = None
        self.warp_pending = False
        self.warp_from = (0, 0)
        self.warp_time = 0.0
        self.pressed_buttons: set[int] = set()
        self.suppressed_buttons: set[int] = set()

        self.root.bind("<KeyPress>", self._on_press)
        self.root.bind("<KeyRelease>", self._on_release)
        self.canvas.bind("<Motion>", self._on_motion)
        # With a button held, Tk reports motion as <B1-Motion>/<B3-Motion>;
        # without these bindings aiming while firing would stop dead.
        self.canvas.bind("<B1-Motion>", self._on_motion)
        self.canvas.bind("<B2-Motion>", self._on_motion)
        self.canvas.bind("<B3-Motion>", self._on_motion)
        self.canvas.bind("<Enter>", self._on_enter)
        self.canvas.bind("<Leave>", self._on_leave)
        self.canvas.bind("<Map>", self._on_canvas_map)
        for button in (1, 2, 3):
            self.canvas.bind(f"<Button-{button}>", self._on_mouse_press)
            self.root.bind(f"<ButtonRelease-{button}>", self._on_mouse_release)
        self.canvas.bind("<Button-4>", self._on_wheel)
        self.canvas.bind("<Button-5>", self._on_wheel)
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        # Another application taking the focus gets the mouse back (PC ports
        # do the same on an alt-tab), so the game never keeps steering while
        # its window is in the background; see _on_focus_out for the filter.
        self.root.bind("<FocusOut>", self._on_focus_out)
        # Key events only reach the bindings once the window holds the X input
        # focus: take it at startup (and again on click) so the game reacts
        # without clicking the window first.  The focus stays on the toplevel
        # widget; moving it to the canvas would fire FocusOut on the way
        # there, which is not the game losing the window focus.
        try:
            self.root.focus_force()
        except tk.TclError:  # pragma: no cover - headless/X without focus
            pass
        self.log.info('window ready', width=width, height=height, scale=self.scale)

    # ------------------------------------------------------------------ window
    def _screen_width(self) -> int:
        from .services import SCREEN_WIDTH
        return getattr(self.boot.runtime, 'width', SCREEN_WIDTH)

    def _screen_height(self) -> int:
        from .services import SCREEN_HEIGHT
        return getattr(self.boot.runtime, 'height', SCREEN_HEIGHT)

    def _on_close(self) -> None:
        if self.closed:
            return
        self._release_all_input()
        self.release_pointer()
        self.boot.runtime.on_tick = None
        self.boot.runtime.input_clock = None
        if self.controls is not None:
            self.controls.close()
        self.closed = True
        self.root.destroy()

    # ------------------------------------------------------------------- input
    def _set_input(self, source: str, key: int | None) -> None:
        """One guest key stays down until its last host owner releases it."""
        old = self.input_sources.pop(source, None)
        if old == key:
            if key is not None:
                self.input_sources[source] = key
            return
        if old is not None and old not in self.input_sources.values():
            if source.startswith('mouse-'):
                self.boot.runtime.post_key(old, press=False, ensure_sample=False)
            else:
                self.boot.runtime.post_key(old, press=False)
            if self.on_key is not None:
                self.on_key(source, old, False)
        if key is not None:
            already_down = key in self.input_sources.values()
            self.input_sources[source] = key
            if not already_down:
                self.boot.runtime.post_key(key, press=True)
                if self.on_key is not None:
                    self.on_key(source, key, True)

    def _release_all_input(self) -> None:
        self.menu_taps.clear()
        for pending in self.pending_release.values():
            self.root.after_cancel(pending)
        self.pending_release.clear()
        self.physical_keys.clear()
        self.held.clear()
        self.pressed_buttons.clear()
        self.suppressed_buttons.clear()
        self.equipment_request = None
        for source in list(self.input_sources):
            self._set_input(source, None)
        for axis in self.look:
            self.look_keys_held[axis] = None
            self.look[axis].reset()

    def _press_key(self, keysym: str) -> None:
        if keysym in self.held:
            return
        self.held.add(keysym)
        binding = self.control_keys.get(normalize_key(keysym), keysym)
        if binding is None:
            return
        state = self.controls.state() if self.controls is not None else None
        if state and binding in '123456789' and len(binding) == 1:
            self._select_equipment(1, int(binding) - 1, state)
            return
        if state and binding in ('F1', 'F2', 'F3'):
            self._select_equipment(2, int(binding[1:]) - 1, state)
            return
        if state and binding == '@mode':
            mode = 2 if state.mode == 1 else 1
            self._select_equipment(mode, state.plasmid if mode == 2 else state.weapon, state)
            return
        key = KEYMAP.get(binding)
        if self.controls is not None and self.controls.supported and state is None:
            key = {'w': AVK_UP, 's': AVK_DOWN, 'a': AVK_LEFT, 'd': AVK_RIGHT,
                   'e': AVK_SELECT, '@fire': AVK_SELECT, '@mode': AVK_CLR}.get(binding.lower(), key)
        if key is None:
            return
        self._set_input(keysym, key)
        if key == AVK_CLR and self.captured:
            self.release_pointer()

    def _release_key(self, keysym: str) -> None:
        self.pending_release.pop(keysym, None)
        if keysym not in self.held:
            return
        self.held.discard(keysym)
        self._set_input(keysym, None)

    def _on_press(self, event) -> str | None:
        keysym = event.keysym
        code = event.keycode
        if getattr(event, 'state', 0) & 0xC:  # Ctrl/Alt shortcuts belong to the desktop
            return
        original = self.physical_keys.get(code)
        if original is not None:
            pending = self.pending_release.pop(original, None)
            if pending is not None:
                self.root.after_cancel(pending)
            return "break"
        self.physical_keys[code] = keysym
        if keysym == "Tab" and self.look_enabled:
            # Free the cursor like the PC versions do; press Tab again (or
            # click) to hand it back to the game.
            if self.captured:
                self.release_pointer()
            else:
                self.capture_pointer()
            return "break"
        # X11 auto-repeat shows up as a release/press pair: a press that
        # follows a pending release cancels it and keeps the key down.
        self._press_key(keysym)
        return "break"

    def _on_release(self, event) -> str | None:
        code = event.keycode
        keysym = self.physical_keys.get(code)
        if keysym is None or keysym in self.pending_release:
            return
        def release():
            self.physical_keys.pop(code, None)
            self._release_key(keysym)
        self.pending_release[keysym] = self.root.after_idle(release)
        return "break"

    # --------------------------------------------------------------- mouse look
    def capture_pointer(self) -> None:
        """Hand the mouse to the game: confine, hide and park the pointer.

        The pointer is parked in the middle of the canvas and the look keys
        are driven from there; ``Tab`` gives it back, and so does a click when
        the game does not hold it yet (see ``_on_mouse_press``).
        """
        if self.controls is not None and self.controls.supported and self.controls.state() is None:
            return
        self.captured = True
        self._set_mouse_cursor(hidden=True)
        self.pointer_capture.capture()
        self._warp_pointer()

    def release_pointer(self) -> None:
        """Give the pointer back to the desktop, cursor and all."""
        self.captured = False
        self.pointer_capture.release()
        self._set_mouse_cursor(hidden=False)
        for axis in self.look:
            self._release_look_key(axis)
            self.look[axis].reset()
        for button in list(self.pressed_buttons):
            self._set_input(f"Button-{button}", None)
            self.suppressed_buttons.add(button)
        self.pressed_buttons.clear()
        self.mouse_ref = None
        self.warp_pending = False

    def _on_canvas_map(self, event) -> None:
        # The initial capture can precede mapping. Also restore confinement
        # after a remap: X releases it automatically when a window is hidden.
        if self.captured and self.root.focus_get() is not None:
            self.capture_pointer()

    def _set_mouse_cursor(self, hidden: bool) -> None:
        """Hide the pointer while the game owns it, like a PC shooter."""
        try:
            self.canvas.configure(cursor="none" if hidden else "")
        except tk.TclError:  # pragma: no cover - window gone
            pass

    def _pointer_canvas(self) -> tuple[int, int] | None:
        """Where the pointer is right now, in canvas coordinates."""
        try:
            px, py = self.root.winfo_pointerxy()
            return (px - self.canvas.winfo_rootx(),
                    py - self.canvas.winfo_rooty())
        except tk.TclError:  # pragma: no cover - window gone
            return None

    def _warp_pointer(self) -> None:
        """Park the pointer in the middle of the canvas.

        Warping is what keeps relative mouse look alive: without it the
        pointer would walk to the edge of the screen and stop producing
        events.  The position the warp starts from is the last accepted
        pointer position, not a fresh X query: while a synthetic warp event
        is being dispatched the X pointer has not moved yet, so a live query
        would claim the pointer is already parked and cancel the filter that
        drops the events describing the pre-warp stream.
        """
        cx, cy = self.center
        ref = self.mouse_ref
        if ref is None:
            ref = self._pointer_canvas()    # nothing observed yet: best effort
        if ref == (cx, cy):
            # already parked: there is no old position to filter against
            self.mouse_ref = (cx, cy)
            self.warp_pending = False
        else:
            self.warp_from = ref if ref is not None else (cx, cy)
            self.mouse_ref = (cx, cy)
            self.warp_pending = True
            self.warp_time = time.monotonic()
        try:
            self.canvas.event_generate("<Motion>", warp=True, x=cx, y=cy)
        except tk.TclError:  # pragma: no cover - window gone
            pass

    def _on_enter(self, event) -> None:
        # The pointer came back over the canvas (the warp landed, or the user
        # brought it back): start counting deltas from here.
        if self.captured and not self.warp_pending:
            self.mouse_ref = (event.x, event.y)

    def _on_leave(self, event) -> None:
        # The pointer left the canvas - it must never stay out there, because
        # motion events only arrive while it is over the window.
        if self.captured:
            self._warp_pointer()

    def _on_focus_out(self, event) -> None:
        """Another window took the focus: give the mouse back.

        Tk reports a <FocusOut> for moves between the widgets of one window
        too (the focus leaving the toplevel for the canvas, say); only a move
        out of the application counts, which is what ``focus_get`` returning
        nothing means.
        """
        if getattr(self, 'closed', False) or self.root.focus_get() is not None:
            return      # the focus moved between our own widgets
        self.log.debug("focus lost; releasing mouse")
        self._release_all_input()
        self.release_pointer()

    def _on_motion(self, event) -> None:
        if self.controls is not None and self.controls.supported and self.controls.state() is None:
            if not self.pressed_buttons and not self.menu_taps:
                self.controls.point_menu(event.x / self.scale, event.y / self.scale)
            return
        if not self.captured:
            return
        cx, cy = self.center
        if event.x == cx and event.y == cy:
            if self.warp_pending:
                if getattr(event, 'time', 1) == 0:
                    return  # Tk's synthetic event precedes the X server warp
                self.warp_pending = False
                self.mouse_ref = (cx, cy)
                return  # acknowledge the warp, not physical travel
        delta = self._motion_delta(event.x, event.y)
        if delta is None:
            return      # a pre-warp event: it describes a position we moved away from
        self.mouse_ref = (event.x, event.y)
        self.look["yaw"].add(delta[0])
        # event.y grows downwards, so a positive pitch delta (mouse down)
        # owes a pitch-down hold: the standard, non-inverted mapping.
        self.look["pitch"].add(-delta[1] if self.invert_y else delta[1])
        if self._near_edge(event.x, event.y):
            self._warp_pointer()

    def _motion_delta(self, x: int, y: int) -> tuple[int, int] | None:
        """Mouse movement since the last accepted position, or None to drop.

        Deltas are measured between consecutive pointer positions rather than
        from the canvas centre: with a warp only issued near the edges, events
        arrive in bursts whose positions are absolute, so consecutive deltas
        stay exact however the host batches them (measuring from the centre
        double-counts every event of a burst).
        """
        if self.warp_pending and not self._warp_landed(x, y):
            return None
        ref = self.mouse_ref
        if ref is None:
            return None
        return x - ref[0], y - ref[1]

    def _warp_landed(self, x: int, y: int) -> bool:
        """Is this event part of the stream the warp sent the pointer to?"""
        if time.monotonic() - self.warp_time > WARP_RESYNC_TIMEOUT:
            # The warp never happened (a window manager is free to refuse
            # one): trust the event, carry on from here and stop waiting.
            self.warp_pending = False
            self.mouse_ref = (x, y)
            return False
        cx, cy = self.center
        fx, fy = self.warp_from
        if (x - cx) ** 2 + (y - cy) ** 2 <= (x - fx) ** 2 + (y - fy) ** 2:
            # Closer to the warp target than to the spot the warp started
            # from: it landed, and this is the movement that followed it.
            self.warp_pending = False
            self.mouse_ref = (cx, cy)
            return True
        return False

    def _near_edge(self, x: int, y: int) -> bool:
        """Is the pointer close enough to a side to be warped back?"""
        width, height = self.canvas_size
        return (x <= EDGE_WARP_MARGIN or y <= EDGE_WARP_MARGIN
                or x >= width - EDGE_WARP_MARGIN
                or y >= height - EDGE_WARP_MARGIN)

    def _set_look_key(self, axis: str, key: int | None) -> None:
        """Hold/release one look key (yaw: 4/6, pitch: UP/DOWN)."""
        if self.look_keys_held[axis] == key:
            return
        current = self.look_keys_held[axis]
        self.look_keys_held[axis] = key
        if current is not None:
            self._set_input(LOOK_NAMES[current], None)
        if key is not None:
            self._set_input(LOOK_NAMES[key], key)

    def _release_look_key(self, axis: str) -> None:
        self._set_look_key(axis, None)

    def _apply_look(self) -> None:
        """Apply relative travel now; unknown builds fall back to timed keys."""
        runtime = self.boot.runtime
        if self.controls is not None and self.controls.supported:
            ticks = self.controls.look_ticks - self.look_ticks_seen
            self.look_ticks_seen = self.controls.look_ticks
        else:
            ticks = runtime.guest_ticks - self.guest_ticks_seen
        self.guest_ticks_seen = runtime.guest_ticks
        if not self.captured:
            return
        if self.controls is not None and self.controls.supported:
            # Spend travel in one native rotation, including fractions, before
            # the next frame. Never enqueue seconds of turn-key holds.
            yaw, pitch = self.look['yaw'].owed, self.look['pitch'].owed
            if yaw or pitch:
                self.controls.rotate_view(yaw, pitch)
            for axis, budget in self.look.items():
                self._release_look_key(axis)
                budget.reset()
            return
        for axis, budget in self.look.items():
            key = budget.hold(ticks)
            self._set_look_key(axis, key)

    def _after_guest_tick(self) -> None:
        state = self.controls.state() if self.controls is not None else None
        if self.controls is not None and self.controls.supported:
            gameplay = state is not None
            if self.was_gameplay and not gameplay:
                self._release_all_input()
                self.release_pointer()
            elif (gameplay and not self.was_gameplay and self.look_enabled and
                  self.root.focus_get() is not None):
                self.capture_pointer()
            self.was_gameplay = gameplay
        self._apply_menu_taps()
        self._apply_look()
        self._apply_equipment(state)

    def _select_equipment(self, mode, slot, state) -> None:
        available = state.weapons if mode == 1 else state.plasmids
        if slot not in available:
            return
        self.equipment_request = (mode, slot)
        self.equipment_attempts = 0
        self.equipment_due = self.boot.runtime.guest_ticks
        self._apply_equipment(state)

    def _apply_equipment(self, state) -> None:
        # Native cycle keys must be separated by guest updates. The runtime
        # preserves even a one-update tap, including between frontend slices.
        self._set_input('equipment', None)
        if self.equipment_request is None:
            return
        if state is None:
            self.equipment_request = None
            return
        mode, slot = self.equipment_request
        current = state.weapon if mode == 1 else state.plasmid
        if state.mode == mode and current == slot:
            self.equipment_request = None
            return
        if not state.can_select:
            return
        ticks = self.boot.runtime.guest_ticks
        if ticks < self.equipment_due:
            return
        if self.equipment_attempts >= 5:
            self.equipment_request = None
            return
        self._set_input('equipment', AVK_3 if mode == 1 else AVK_1)
        self.equipment_attempts += 1
        self.equipment_due = ticks + 12  # allow the native equip animation to finish

    def _on_mouse_press(self, event) -> None:
        self.root.focus_force()     # a click on the window is a focus click
        state = self.controls.state() if self.controls is not None else None
        menu = self.controls is not None and self.controls.supported and state is None
        context = self.controls.menu() if menu else None
        if self.menu_taps:
            return
        if menu and event.num == 1:
            if context is None:
                return
            if context.rows and not self.controls.point_menu(event.x / self.scale, event.y / self.scale):
                return
            if context.kind == 8:  # native loading screen
                return
        if self.look_enabled and not menu and self._wants_capture(event):
            # The click that hands the mouse to the game is not also a game
            # action: PC ports spend it on the capture (and so does this one,
            # so the focus click never fires AVK_5 at a menu or a cutscene).
            self.capture_pointer()
            self.suppressed_buttons.add(event.num)
            return
        key = MOUSE_BUTTONS.get(f"<Button-{event.num}>")
        if key is None:
            return
        if state and event.num == 3:
            mode = 2 if state.mode == 1 else 1
            self._select_equipment(mode, state.plasmid if mode == 2 else state.weapon, state)
            self.pressed_buttons.add(event.num)
            return
        if self.controls is not None and self.controls.supported and state is None:
            key = AVK_SELECT if event.num == 1 else AVK_CLR
        self.pressed_buttons.add(event.num)
        self._set_input(f"Button-{event.num}", key)
        if menu:
            # Keep the tap alive until its target menu changes, rather than
            # releasing on a preliminary key poll before the UI handles it.
            target = (context.address, context.kind) if context is not None else None
            self.menu_taps[f"Button-{event.num}"] = (self.boot.runtime.guest_ticks + 8, target)

    def _apply_menu_taps(self) -> None:
        if not self.menu_taps:
            return
        context = self.controls.menu()
        target = (context.address, context.kind) if context is not None else None
        for source, (deadline, original) in list(self.menu_taps.items()):
            if target != original or self.boot.runtime.guest_ticks >= deadline:
                self.menu_taps.pop(source)
                self._set_input(source, None)

    def _wants_capture(self, event) -> bool:
        """Spend focus/capture clicks without swallowing clicks while aiming."""
        return not self.captured or self.mouse_ref is None

    def _on_mouse_release(self, event) -> None:
        if event.num in self.suppressed_buttons:
            self.suppressed_buttons.discard(event.num)
            return
        key = MOUSE_BUTTONS.get(f"<Button-{event.num}>")
        if key is None or event.num not in self.pressed_buttons:
            return
        self.pressed_buttons.discard(event.num)
        source = f"Button-{event.num}"
        if source not in self.menu_taps:
            self._set_input(source, None)

    def _on_wheel(self, event) -> str:
        if (getattr(event, 'num', 0) not in (4, 5) and
                not getattr(event, 'delta', 0)):
            return "break"
        state = self.controls.state() if self.controls is not None else None
        if self.controls is not None and self.controls.supported and state is None:
            direction = 1 if (getattr(event, 'num', 0) == 4 or
                              getattr(event, 'delta', 0) > 0) else -1
            if self.controls.menu_adjustment(direction):
                return "break"
            key = AVK_UP if direction > 0 else AVK_DOWN
            self.boot.runtime.post_key(key)
            self.boot.runtime.post_key(key, press=False)
            return "break"
        if self.look_enabled and not self.captured:
            return "break"
        if state:
            available = state.weapons if state.mode == 1 else state.plasmids
            current = state.weapon if state.mode == 1 else state.plasmid
            if current in available:
                direction = 1 if (getattr(event, 'num', 0) == 4 or
                                  getattr(event, 'delta', 0) > 0) else -1
                if self.equipment_request and self.equipment_request[0] == state.mode:
                    current = self.equipment_request[1]
                slot = available[(available.index(current) + direction) % len(available)]
                self._select_equipment(state.mode, slot, state)
        return "break"

    # ------------------------------------------------------------------- frame
    def draw(self) -> None:
        runtime = self.boot.runtime
        if runtime.frames_presented == self.frame_seen:
            return
        snapshot = runtime.snapshot_rgb()
        if snapshot is None:
            return
        width, height, rgb = snapshot
        started = time.monotonic()
        photo = tk.PhotoImage(data=ppm_bytes(width, height, rgb))
        if self.scale > 1:
            photo = photo.zoom(self.scale)
        self.canvas.itemconfigure(self.item, image=photo)
        self.photo = photo
        if self.status is not None:
            self.canvas.delete(self.status)
            self.status = None
        self.frame_seen = runtime.frames_presented
        self.frames_drawn += 1
        self.draw_time = time.monotonic() - started

    def _tick(self) -> None:
        if self.closed:
            return
        if self.error is None:
            try:
                if self.look_enabled:
                    self._apply_look()
                reason = self.boot.run(seconds=self.slice)
                if reason == "quit":
                    self.log.info("guest quit; closing window")
                    self._on_close()
                    return
            except GuestError as error:
                self.error = str(error)
                self.log.error(f"guest faulted: {self.error}")
                self._release_all_input()
                self.release_pointer()
                if self.status is None:
                    self.status = self.canvas.create_text(
                        *self.center, text="guest faulted - see console", fill="#ff6060")
                else:
                    self.canvas.itemconfigure(
                        self.status, text="guest faulted - see console", fill="#ff6060")
        if self.error is None:
            self.draw()
        self._update_title()
        self.root.after(1, self._tick)

    def _update_title(self) -> None:
        now = time.monotonic()
        if now - self.last_title < 1.0:
            return
        self.last_title = now
        runtime = self.boot.runtime
        mouse = ("disabled" if not self.look_enabled else
                 "captured (Tab frees)" if self.captured else
                 "menu" if self.controls.supported and self.controls.state() is None else
                 "free (click to capture)")
        self.root.title(
            f"BioShock 3D (BREW) - bio3dpc - frames {runtime.frames_presented} - "
            f"mouse {mouse} - draw {self.draw_time * 1000:.0f} ms")

    def run(self) -> None:
        self.guest_ticks_seen = self.boot.runtime.guest_ticks
        if self.captured:
            # Delay the initial gameplay/fallback warp until the window
            # manager has mapped the canvas.
            self.root.after(100, lambda: self._on_canvas_map(None))
        self.root.after(1, self._tick)
        self._install_sigint()
        try:
            self.root.mainloop()
        finally:
            if not self.closed:
                self._release_all_input()
                self.release_pointer()
                self.boot.runtime.on_tick = None
                self.boot.runtime.input_clock = None
                if self.controls is not None:
                    self.controls.close()
                self.closed = True
                try:
                    self.root.destroy()
                except tk.TclError:
                    pass

    def _install_sigint(self) -> None:
        """Make Ctrl+C close the window instead of being swallowed by Tcl.

        Exceptions raised inside Tk callbacks never unwind ``mainloop``: Tk
        reports them and keeps going, so a KeyboardInterrupt in ``_tick`` would
        print a traceback and the game would keep running.
        """
        def handler(_signum, _frame):
            try:
                self.root.after_idle(self._on_close)
            except tk.TclError:  # pragma: no cover - window already gone
                pass

        try:
            signal.signal(signal.SIGINT, handler)
        except (ValueError, OSError):  # pragma: no cover - not the main thread
            pass
