"""Input ownership and real press/release sequencing without a display."""
from types import SimpleNamespace
from dataclasses import replace

import pytest

from bio3dpc.frontend import Frontend, KEYMAP, LookBudget
from bio3dpc.controls import Gameplay
from bio3dpc.input_settings import ControlSettings
from bio3dpc.services import (
    AVK_0, AVK_2, AVK_4, AVK_5, AVK_6, AVK_7, AVK_9,
    AVK_UP, AVK_DOWN, AVK_SOFT1, AVK_SOFT2,
    BrewRuntime, EVT_KEY, EVT_KEY_RELEASE,
)


class Scheduler:
    def __init__(self):
        self.jobs = {}
        self.next_id = 0

    def after_idle(self, fn):
        self.next_id += 1
        self.jobs[self.next_id] = fn
        return self.next_id

    def after_cancel(self, job):
        self.jobs.pop(job, None)

    def idle(self):
        for job, fn in list(self.jobs.items()):
            self.jobs.pop(job, None)
            fn()

    def focus_get(self):
        return None


@pytest.fixture
def frontend():
    f = Frontend.__new__(Frontend)
    f.events = []
    f.boot = SimpleNamespace(runtime=SimpleNamespace(
        guest_ticks=0, post_key=lambda key, press=True, **kw: f.events.append((key, press))))
    f.root = Scheduler()
    f.on_key = None
    f.control_keys = ControlSettings().translations()
    f.invert_y = False
    f.controls = None
    f.equipment_request = None
    f.equipment_due = 0
    f.equipment_attempts = 0
    f.held = set()
    f.input_sources = {}
    f.physical_keys = {}
    f.pending_release = {}
    f.menu_taps = {}
    f.pressed_buttons = set()
    f.suppressed_buttons = set()
    f.look_enabled = True
    f.captured = True
    f.center = (176, 220)
    f.canvas_size = (352, 440)
    f.mouse_ref = f.center
    f.warp_pending = False
    f.guest_ticks_seen = 0
    f.look = {'yaw': LookBudget(AVK_6, AVK_4, 13),
              'pitch': LookBudget(AVK_DOWN, AVK_UP, 16)}
    f.look_keys_held = {'yaw': None, 'pitch': None}
    f._set_mouse_cursor = lambda hidden: None
    f.pointer_capture = SimpleNamespace(capture=lambda: True, release=lambda: None)
    f.log = SimpleNamespace(debug=lambda *a: None)
    return f


def event(sym='w', code=25, **kw):
    return SimpleNamespace(keysym=sym, keycode=code, state=0, **kw)


def test_pc_actions_use_distinct_guest_bindings():
    assert [KEYMAP[k] for k in ('w', 'a', 'd', 'e', 'r', 'f')] == [
        AVK_2, AVK_7, AVK_9, AVK_0, AVK_SOFT1, AVK_SOFT2]


def test_alias_release_keeps_other_host_key_down(frontend):
    f = frontend
    f._press_key('w')
    f._press_key('2')
    f._release_key('w')
    assert f.events == [(AVK_2, True)]
    f._release_key('2')
    assert f.events == [(AVK_2, True), (AVK_2, False)]


def test_keyboard_and_mouse_share_guest_ownership(frontend):
    f = frontend
    f._press_key('6')
    f._set_look_key('yaw', AVK_6)
    f._set_look_key('yaw', None)
    assert f.events == [(AVK_6, True)]
    f._release_key('6')
    assert f.events[-1] == (AVK_6, False)


def test_release_uses_physical_key_even_if_shift_changed(frontend):
    f = frontend
    f._on_press(event('W'))
    f._on_release(event('w'))
    f.root.idle()
    assert f.events == [(AVK_2, True), (AVK_2, False)]
    assert not f.held and not f.physical_keys


def test_autorepeat_pair_keeps_key_down_without_delayed_stop(frontend):
    f = frontend
    f._on_press(event())
    f._on_release(event())
    f._on_press(event())
    f.root.idle()
    assert f.events == [(AVK_2, True)]
    f._on_release(event())
    f.root.idle()
    assert f.events[-1] == (AVK_2, False)


def test_tab_repeat_toggles_once(frontend):
    f = frontend
    f._on_press(event('Tab', 23))
    assert not f.captured
    f._on_release(event('Tab', 23))
    f._on_press(event('Tab', 23))
    f.root.idle()
    assert not f.captured


@pytest.mark.parametrize('captured', [True, False])
def test_focus_loss_releases_keyboard_buttons_and_pending_work(frontend, captured):
    f = frontend
    f.captured = captured
    f._on_press(event())
    f._on_release(event())
    f._set_input('Button-1', AVK_5)
    f.pressed_buttons.add(1)
    f.look['yaw'].add(130)
    f._on_focus_out(None)
    f.root.idle()
    assert not f.input_sources and not f.held and not f.physical_keys
    assert not f.pending_release and not f.pressed_buttons
    assert f.look['yaw'].owed == 0
    assert (AVK_2, False) in f.events and (AVK_5, False) in f.events


def test_motion_back_to_center_counts_physical_travel(frontend):
    f = frontend
    f._on_motion(SimpleNamespace(x=189, y=220))
    f._on_motion(SimpleNamespace(x=176, y=220))
    assert f.look['yaw'].owed == 0
    assert f.mouse_ref == f.center


def test_warp_ack_resets_reference_without_adding_look(frontend):
    f = frontend
    f.warp_pending = True
    f._on_motion(SimpleNamespace(x=176, y=220))
    assert not f.warp_pending
    assert f.look['yaw'].owed == 0


def test_synthetic_warp_ack_waits_for_server_event(frontend):
    f = frontend
    f.warp_pending = True
    f._on_motion(SimpleNamespace(x=176, y=220, time=0))
    assert f.warp_pending
    f._on_motion(SimpleNamespace(x=176, y=220, time=1234))
    assert not f.warp_pending


def test_each_guest_tick_spends_exactly_one_look_step(frontend):
    f = frontend
    f.look['yaw'].add(13)
    f._apply_look()
    assert f.events == [(AVK_6, True)]
    for tick in range(1, 9):
        f.boot.runtime.guest_ticks = tick
        f._apply_look()
    assert f.events == [(AVK_6, True), (AVK_6, False)]
    assert f.look['yaw'].owed == 0


def test_fast_tap_is_visible_to_guest_for_one_update(monkeypatch):
    delivered = []
    monkeypatch.setattr('bio3dpc.services.dispatch_applet',
                        lambda m, a, evt, key, param: delivered.append((evt, key)))
    r = BrewRuntime.__new__(BrewRuntime)
    r.m = None
    r.applet = 1
    r.quit = False
    r.guest_ticks = 0
    r.key_down_ticks = {}
    r.key_down_timer_ticks = {}
    sampled = [0]
    r.input_clock = lambda key: sampled[0]
    r.timers = {1: {}}
    r.pending_events = [(0, EVT_KEY, AVK_5, 0),
                        (0, EVT_KEY_RELEASE, AVK_5, 0),
                        (0, EVT_KEY, AVK_6, 0)]
    r._dispatch_pending_events()
    assert delivered == [(EVT_KEY, AVK_5), (EVT_KEY, AVK_6)]
    assert len(r.pending_events) == 1
    r.guest_ticks += 1
    r._dispatch_pending_events()
    assert len(r.pending_events) == 1  # a timer alone did not sample the keypad
    sampled[0] += 1
    r._dispatch_pending_events()
    assert delivered[-1] == (EVT_KEY_RELEASE, AVK_5)
    assert not r.pending_events


def test_mouse_budget_release_does_not_wait_for_key_query_clock(monkeypatch):
    delivered = []
    monkeypatch.setattr('bio3dpc.services.dispatch_applet',
                        lambda m, a, evt, key, param: delivered.append((evt, key)))
    r = BrewRuntime.__new__(BrewRuntime)
    r.m, r.applet, r.quit, r.guest_ticks = None, 1, False, 2
    r.key_down_ticks = {AVK_4: 0}
    r.key_down_timer_ticks = {AVK_4: 0}
    r.input_clock = lambda key: 0
    r.timers = {1: {}}
    r.pending_events = []
    r.post_key(AVK_4, press=False, ensure_sample=False)
    r._dispatch_pending_events()
    assert delivered == [(EVT_KEY_RELEASE, AVK_4)]
    assert AVK_4 not in r.key_down_ticks


def test_completed_mouse_budget_keeps_physical_owner_held(frontend):
    f = frontend
    f._set_input('KP_4', AVK_4)
    f._set_look_key('yaw', AVK_4)
    f._release_look_key('yaw')
    assert f.events == [(AVK_4, True)]
    f._set_input('KP_4', None)
    assert f.events[-1] == (AVK_4, False)


def test_mouse_step_does_not_overshoot_across_multiple_timers(frontend, monkeypatch):
    f = frontend
    r = BrewRuntime.__new__(BrewRuntime)
    f.boot.runtime = r
    held, sampled = set(), []
    def dispatch(m, a, evt, key, param):
        if evt == EVT_KEY:
            held.add(key)
        else:
            held.discard(key)
    monkeypatch.setattr('bio3dpc.services.dispatch_applet', dispatch)
    def tick(pfn, args, timeout):
        sampled.append(set(held))
        if len(sampled) == 4:
            r.quit = True
        else:
            r.timers[(1, 0)] = {'pfn': 1, 'user': 0, 'due': 0}
    r.m = SimpleNamespace(call=tick)
    r.log = SimpleNamespace(trace=lambda *a, **kw: None)
    r.applet, r.quit, r.guest_ticks = 1, False, 0
    r.pending_events, r.key_down_ticks = [], {}
    r.key_down_timer_ticks = {}
    r.input_clock = None
    r.timers = {(1, 0): {'pfn': 1, 'user': 0, 'due': 0}}
    r._dispatch_deferred = r._dispatch_scheduled = lambda: None
    r.on_tick = f._apply_look
    f.look['yaw'].add(13)
    f._apply_look()
    r.pump(max_seconds=.1)
    assert sampled == [{AVK_6}, set(), set(), set()]
    assert f.look['yaw'].owed == 0


def equipment_context(f, mode=1, weapon=0, plasmid=0):
    state = Gameplay(1, mode, weapon, plasmid, (0, 1, 2), (0, 1))
    f.controls = SimpleNamespace(supported=True, state=lambda: state)
    return state


def test_number_row_requests_weapon_without_moving(frontend):
    f = frontend
    state = equipment_context(f)
    f._press_key('2')
    assert f.equipment_request == (1, 1)
    assert f.events == [(KEYMAP['3'], True)]
    f._release_key('2')
    f._apply_equipment(Gameplay(1, 1, 1, 0, state.weapons, state.plasmids))
    assert f.equipment_request is None
    assert f.events[-1] == (KEYMAP['3'], False)
    assert not any(key == AVK_2 for key, _ in f.events)


def test_wheel_directions_request_adjacent_owned_slots(frontend):
    f = frontend
    equipment_context(f)
    f._on_wheel(SimpleNamespace(num=5, delta=0))
    assert f.equipment_request == (1, 2)
    f.equipment_request = None
    f._on_wheel(SimpleNamespace(num=4, delta=0))
    assert f.equipment_request == (1, 1)


def test_unowned_slot_does_not_send_native_key(frontend):
    f = frontend
    equipment_context(f)
    f._press_key('5')
    assert not f.events and f.equipment_request is None


def test_equipment_request_waits_for_native_animation(frontend):
    f = frontend
    state = equipment_context(f)
    f._select_equipment(1, 1, replace(state, can_select=False))
    for _ in range(20):
        f._apply_equipment(replace(state, can_select=False))
    assert f.equipment_request == (1, 1) and not f.events
    assert f.equipment_attempts == 0
    f._apply_equipment(state)
    assert f.events == [(KEYMAP['3'], True)]


def test_wasd_and_use_navigate_menus(frontend):
    f = frontend
    f.controls = SimpleNamespace(supported=True, state=lambda: None)
    f._press_key('w')
    f._press_key('e')
    assert f.events == [(AVK_UP, True), (KEYMAP['Return'], True)]


def test_rebinding_moves_the_action_and_preserves_physical_release(frontend):
    f = frontend
    f.control_keys = ControlSettings.from_dict({'bindings': {'forward': 'j'}}).translations()
    f._press_key('w')
    assert f.events == []
    f._on_press(event('J', 44))
    f._on_release(event('j', 44))
    f.root.idle()
    assert f.events == [(AVK_2, True), (AVK_2, False)]
    f.events.clear()
    f.controls = SimpleNamespace(supported=True, state=lambda: None)
    f._press_key('j')
    f._release_key('j')
    assert f.events == [(AVK_UP, True), (AVK_UP, False)]


def test_rebound_equipment_and_keyboard_mouse_actions(frontend):
    f = frontend
    equipment_context(f)
    f.control_keys = ControlSettings.from_dict({'bindings': {
        'weapon2': 'p', 'fire': 'c', 'mode': 'v',
    }}).translations()
    f._press_key('2')
    assert f.equipment_request is None and f.events == []
    f._press_key('p')
    assert f.equipment_request == (1, 1)
    f.equipment_request = None
    f._press_key('v')
    assert f.equipment_request == (2, 0)
    f._press_key('c')
    assert (AVK_5, True) in f.events
    f._release_key('c')
    assert f.events[-1] == (AVK_5, False)


def test_invert_y_reverses_pitch_without_changing_yaw(frontend):
    f = frontend
    f.invert_y = True
    f._on_motion(SimpleNamespace(x=189, y=236))
    assert f.look['yaw'].hold() == AVK_6
    assert f.look['pitch'].hold() == AVK_UP


def test_native_mouse_flick_is_consumed_once_without_held_keys(frontend):
    f = frontend
    rotations = []
    f.controls = SimpleNamespace(supported=True, look_ticks=0,
                                 rotate_view=lambda yaw, pitch: rotations.append((yaw, pitch)))
    f.look_ticks_seen = 0
    f.look['yaw'].add(130)
    f.look['pitch'].add(2)
    f._apply_look()
    assert rotations == [(10, .125)]
    assert f.events == []
    assert all(budget.owed == 0 and budget.holding is None for budget in f.look.values())
    for tick in range(1, 40):
        f.boot.runtime.guest_ticks = tick
        f.controls.look_ticks = tick
        f._apply_look()
    assert rotations == [(10, .125)] and not f.events


def test_menu_click_targets_scaled_position_and_never_captures(frontend):
    f = frontend
    f.root.focus_force = lambda: None
    f.scale = 3
    f.captured = False
    points = []
    f.controls = SimpleNamespace(supported=True, state=lambda: None,
                                 menu=lambda: SimpleNamespace(address=1, kind=4, rows=((3, 130),)),
                                 point_menu=lambda x, y: points.append((x, y)) or True)
    click = SimpleNamespace(x=264, y=408, num=1)
    f._on_mouse_press(click)
    assert points == [(88, 136)] and not f.captured
    assert f.events == [(KEYMAP['Return'], True)]
    f._on_mouse_release(click)
    assert len(f.events) == 1 and not f.pressed_buttons
    f.controls.menu = lambda: SimpleNamespace(address=2, kind=11)
    f._apply_menu_taps()
    assert f.events[-1] == (KEYMAP['Return'], False)
    assert not f.menu_taps


def test_click_outside_menu_rows_does_not_confirm_current_selection(frontend):
    f = frontend
    f.root.focus_force = lambda: None
    f.scale = 2
    f.captured = False
    f.controls = SimpleNamespace(supported=True, state=lambda: None,
                                 menu=lambda: SimpleNamespace(address=1, kind=4, rows=((3, 130),)),
                                 point_menu=lambda x, y: False)
    f._on_mouse_press(SimpleNamespace(x=1, y=1, num=1))
    assert not f.events and not f.captured


def test_tab_cannot_capture_in_a_native_menu(frontend):
    f = frontend
    f.captured = False
    f.controls = SimpleNamespace(supported=True, state=lambda: None)
    f._on_press(event('Tab', 23))
    assert not f.captured


def test_wheel_navigates_menus_with_mouse_look_disabled(frontend):
    f = frontend
    f.captured = f.look_enabled = False
    f.controls = SimpleNamespace(supported=True, state=lambda: None, menu_adjustment=lambda d: False)
    f._on_wheel(SimpleNamespace(num=5, delta=0))
    f._on_wheel(SimpleNamespace(num=4, delta=0))
    assert f.events == [(AVK_DOWN, True), (AVK_DOWN, False), (AVK_UP, True), (AVK_UP, False)]


def test_menu_tap_has_bounded_lifetime_without_screen_transition(frontend):
    f = frontend
    f.controls = SimpleNamespace(menu=lambda: SimpleNamespace(address=1, kind=11))
    f._set_input('Button-1', KEYMAP['Return'])
    f.menu_taps['Button-1'] = (8, (1, 11))
    for tick in range(8):
        f.boot.runtime.guest_ticks = tick
        f._apply_menu_taps()
        assert f.events == [(KEYMAP['Return'], True)]
    f.boot.runtime.guest_ticks = 8
    f._apply_menu_taps()
    assert f.events[-1] == (KEYMAP['Return'], False)
    assert not f.menu_taps
