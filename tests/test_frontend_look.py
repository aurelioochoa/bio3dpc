"""Mouse look budgets and pointer bookkeeping for the tkinter frontend.

The frontend converts mouse movement into holds of the phone's look keys, and
the build only steps its camera on its own timer tick - so a hold is counted in
guest ticks, not wall seconds.  ``LookBudget`` is that accounting, kept free of
Tk so it can be tested without a display; the pointer bookkeeping (which
motion events are dropped around a warp, which clicks capture the mouse) is
exercised the same way, on a stub that only carries the attributes it reads.
"""
from __future__ import annotations

import time

from bio3dpc.frontend import (
    EDGE_WARP_MARGIN,
    LOOK_MAX_TICKS,
    PIXELS_PER_PITCH_TICK,
    PIXELS_PER_TURN_TICK,
    WARP_RESYNC_TIMEOUT,
    Frontend,
    LookBudget,
    ppm_bytes,
)
from bio3dpc.services import AVK_4, AVK_6, AVK_DOWN, AVK_UP


def yaw_budget(pixels_per_tick: float = PIXELS_PER_TURN_TICK) -> LookBudget:
    return LookBudget(AVK_6, AVK_4, pixels_per_tick)


# ------------------------------------------------------------------- ppm
def test_ppm_header_and_payload():
    data = ppm_bytes(2, 1, b"\x01\x02\x03\x04\x05\x06")
    assert data == b"P6\n2 1\n255\n\x01\x02\x03\x04\x05\x06"


# ------------------------------------------------------------------ budgets
def test_one_tick_of_travel_holds_the_key_for_one_tick():
    budget = yaw_budget()
    budget.add(PIXELS_PER_TURN_TICK)

    assert budget.hold(0) == AVK_6     # a whole tick is owed: press 6
    assert budget.hold(1) == None      # the tick fired; the camera stepped
    assert budget.hold(1) == None      # nothing owed any more


def test_two_ticks_of_travel_hold_the_key_for_two_ticks():
    budget = yaw_budget()
    budget.add(2 * PIXELS_PER_TURN_TICK)

    assert budget.hold(0) == AVK_6
    assert budget.hold(1) == AVK_6
    assert budget.hold(1) == None


def test_negative_travel_holds_the_other_key():
    budget = yaw_budget()
    budget.add(-2 * PIXELS_PER_TURN_TICK)

    assert budget.hold(0) == AVK_4
    assert budget.hold(1) == AVK_4
    assert budget.hold(1) == None


def test_sub_tick_travel_accumulates_instead_of_being_dropped():
    budget = yaw_budget()
    budget.add(PIXELS_PER_TURN_TICK / 2)
    assert budget.hold(0) is None      # half a tick is not worth a press

    budget.add(PIXELS_PER_TURN_TICK / 2)
    assert budget.hold(0) == AVK_6     # the halves add up to a whole step


def test_only_ticks_that_fired_while_the_key_was_held_are_paid():
    budget = yaw_budget()
    budget.add(PIXELS_PER_TURN_TICK)

    assert budget.hold(0) == AVK_6     # nothing fired yet: press 6
    assert budget.hold(0) == AVK_6     # still nothing: keep holding
    assert budget.hold(1) is None      # one tick fired: the step is paid for
    assert budget.hold(1) is None      # and no key is held any more


def test_a_flick_is_capped():
    budget = yaw_budget()
    budget.add(10_000 * PIXELS_PER_TURN_TICK)

    assert budget.owed == LOOK_MAX_TICKS
    assert budget.hold(0) == AVK_6
    assert budget.hold(LOOK_MAX_TICKS) == None


def test_a_tick_is_charged_in_the_direction_of_the_key_that_was_held():
    budget = yaw_budget()
    budget.add(PIXELS_PER_TURN_TICK)
    assert budget.hold(0) == AVK_6      # one step to the right is owed

    # The pointer swings back by two steps before the tick fires: the tick
    # still delivered a step to the right (the key was down for it), so two
    # steps to the left are owed now - one for the swing and one to undo the
    # delivered step.
    budget.add(-2 * PIXELS_PER_TURN_TICK)

    assert budget.hold(1) == AVK_4
    assert budget.hold(1) == AVK_4
    assert budget.hold(1) is None


def test_reversing_the_swing_releases_the_key():
    budget = yaw_budget()
    budget.add(PIXELS_PER_TURN_TICK)
    assert budget.hold(0) == AVK_6

    budget.add(-PIXELS_PER_TURN_TICK)   # the swing cancels the owed step
    assert budget.hold(0) is None

    budget.add(-PIXELS_PER_TURN_TICK)
    assert budget.hold(0) == AVK_4      # and the other way is owed now


def test_sensitivity_scales_the_travel_per_tick():
    slow = yaw_budget(PIXELS_PER_TURN_TICK * 2)     # --mouse-sens 0.5
    fast = yaw_budget(PIXELS_PER_TURN_TICK / 2)     # --mouse-sens 2.0

    slow.add(PIXELS_PER_TURN_TICK)
    fast.add(PIXELS_PER_TURN_TICK)

    assert slow.hold(0) is None                     # half a step owed
    assert fast.hold(0) == AVK_6                    # two steps owed
    assert fast.hold(1) == AVK_6
    assert fast.hold(1) is None


def test_pitch_axis_uses_its_own_pair_and_rate():
    pitch = LookBudget(AVK_DOWN, AVK_UP, PIXELS_PER_PITCH_TICK)

    pitch.add(PIXELS_PER_PITCH_TICK)
    assert pitch.hold(0) == AVK_DOWN

    # swing up by two steps: the delivered down step plus the swing leaves
    # two up steps owed
    pitch.add(-2 * PIXELS_PER_PITCH_TICK)
    assert pitch.hold(1) == AVK_UP
    assert pitch.hold(1) == AVK_UP
    assert pitch.hold(1) is None


def test_reset_clears_owed_hold_and_key():
    budget = yaw_budget()
    budget.add(3 * PIXELS_PER_TURN_TICK)
    assert budget.hold(0) == AVK_6

    budget.reset()

    assert budget.owed == 0.0
    assert budget.holding is None
    assert budget.hold(1) is None


# ------------------------------------------------------- pointer bookkeeping
class FakeFrontend:
    """The frontend attributes the pointer bookkeeping reads (no Tk)."""

    # the real filter: _motion_delta calls it through ``self``
    _warp_landed = Frontend._warp_landed

    def __init__(self, center=(176, 220), size=(352, 440)):
        self.center = center
        self.canvas_size = size
        self.captured = True
        self.look_enabled = True
        self.mouse_ref = None
        self.warp_pending = False
        self.warp_from = (0, 0)
        self.warp_time = 0.0


class FakeEvent:
    def __init__(self, x, y, num=1):
        self.x = x
        self.y = y
        self.num = num


def test_motion_delta_is_the_step_between_consecutive_positions():
    fake = FakeFrontend()
    fake.mouse_ref = (176, 220)

    # Absolute event positions: the delta is from the last accepted one.  This
    # is why a burst of events counts its travel once and not once per event.
    assert Frontend._motion_delta(fake, 186, 223) == (10, 3)


def test_motion_before_any_reference_is_dropped():
    fake = FakeFrontend()
    assert Frontend._motion_delta(fake, 186, 223) is None


def test_stale_pre_warp_events_are_dropped_until_the_warp_lands():
    fake = FakeFrontend()
    fake.mouse_ref = (10, 220)          # last accepted position: at the edge
    fake.warp_from = (10, 220)
    fake.warp_pending = True
    fake.warp_time = time.monotonic()

    # still closer to where the warp started: part of the pre-warp stream
    assert Frontend._motion_delta(fake, 5, 220) is None
    assert fake.warp_pending

    # closer to the warp target (the centre): the post-warp stream
    assert Frontend._motion_delta(fake, 183, 223) == (7, 3)
    assert not fake.warp_pending
    assert fake.mouse_ref == (176, 220)


def test_the_resync_timeout_trusts_the_pointer_again():
    fake = FakeFrontend()
    fake.mouse_ref = (176, 220)
    fake.warp_from = (10, 220)
    fake.warp_pending = True
    fake.warp_time = time.monotonic() - WARP_RESYNC_TIMEOUT - 1.0

    # The warp never happened (a window manager is free to refuse one): the
    # event re-anchors instead of being dropped forever.
    assert Frontend._motion_delta(fake, 5, 220) is None
    assert not fake.warp_pending
    assert fake.mouse_ref == (5, 220)


def test_edges_are_inside_the_warp_margin():
    fake = FakeFrontend()

    assert Frontend._near_edge(fake, EDGE_WARP_MARGIN, 220)
    assert not Frontend._near_edge(fake, EDGE_WARP_MARGIN + 1, 220)
    assert Frontend._near_edge(fake, fake.canvas_size[0] - EDGE_WARP_MARGIN, 220)
    assert not Frontend._near_edge(fake, fake.canvas_size[0]
                                   - EDGE_WARP_MARGIN - 1, 220)
    assert Frontend._near_edge(fake, 176, EDGE_WARP_MARGIN)
    assert Frontend._near_edge(fake, 176, fake.canvas_size[1]
                               - EDGE_WARP_MARGIN)
    assert not Frontend._near_edge(fake, 176, 220)


def test_only_uncaptured_or_uninitialised_clicks_capture():
    fake = FakeFrontend()

    fake.mouse_ref = (176, 220)
    assert not Frontend._wants_capture(fake, FakeEvent(176, 220))
    assert not Frontend._wants_capture(fake, FakeEvent(10, 220))

    fake.captured = False
    assert Frontend._wants_capture(fake, FakeEvent(176, 220))

    fake.captured = True
    fake.warp_pending = True            # aiming through a warp must not eat fire
    assert not Frontend._wants_capture(fake, FakeEvent(176, 220))

    fake.warp_pending = False
    fake.mouse_ref = None
    assert Frontend._wants_capture(fake, FakeEvent(176, 220))
