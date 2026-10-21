"""Build guard and guest context are checked independently of the frontend."""
import math
import struct
from types import SimpleNamespace

import pytest

from bio3dpc.controls import GameControls, Gameplay, PITCH_LIMIT


class Memory:
    heap_base = 0x1000

    def __init__(self):
        self.data = bytearray(0x10000)

    def read(self, address, size):
        assert self.contains(address, size)
        return bytes(self.data[address:address + size])

    def contains(self, address, size):
        return 0 <= address <= len(self.data) - size

    def u32(self, address):
        return int.from_bytes(self.read(address, 4), 'little')

    def w16(self, address, value):
        self.data[address:address + 2] = value.to_bytes(2, 'little')

    def write(self, address, data):
        self.data[address:address + len(data)] = data

    def w32(self, address, value):
        self.word(address, value)

    def word(self, address, value):
        self.data[address:address + 4] = value.to_bytes(4, 'little')


def context():
    m = Memory()
    m.word(0x1020, 0x2000)  # applet -> game -> level -> player
    m.word(0x21ec, 0x3000)
    m.word(0x3020, 0x4000)
    m.word(0x21bc, 1)
    m.data[0x21b8] = 2
    m.data[0x4230] = 1
    m.word(0x421c, 1)
    for i in range(5):
        item = 0x6000 + i * 0x100
        m.word(0x41f8 + i * 4, item)
        m.data[item + 0x5b] = int(i in (0, 1, 3))
    m.word(0x420c, 0x7000)
    m.data[0x7056] = 1
    c = GameControls.__new__(GameControls)
    c.supported = True
    c.game_delta = 0
    c.boot = SimpleNamespace(m=m, applet=0x1000, runtime=SimpleNamespace(width=176, height=220))
    return c, m


def test_context_reports_only_owned_equipment():
    c, m = context()
    assert c.state() == Gameplay(0x4000, 1, 1, 0, (0, 1, 3), (0,))


def test_invalid_player_pointer_does_not_read_arbitrary_memory():
    c, m = context()
    m.word(0x3020, 0xfffffff0)
    assert c.state() is None


def test_unknown_binary_never_reads_guest_context():
    c = GameControls(SimpleNamespace(mod=SimpleNamespace(data=b'other build')))
    assert not c.supported and c.state() is None


def test_pause_overlay_is_not_gameplay_even_with_live_player():
    c, m = context()
    m.word(0x2210, 1)
    assert c.state() is None


def test_muting_sound_does_not_disable_gameplay_mouse_routing():
    c, m = context()
    m.word(0x21bc, 0)  # native sound enabled flag, not a gameplay discriminator
    assert c.state() is not None


def test_interaction_blocks_equipment_without_disabling_movement_context():
    c, m = context()
    m.word(0x4298, 0x8000)
    assert c.state() is not None
    assert not c.state().can_select


def menu_context(kind=4, resume=True, load=True, delta=0):
    c, m = context()
    c.game_delta = delta
    m.word(0x2000 + 0x1c4 + delta, 0x8000)
    m.data[0x8000] = kind
    m.word(0x8020, 0x9000)
    m.word(0x9010, int(resume))
    m.word(0x9014, int(load))
    return c, m


@pytest.mark.parametrize('delta', [0, -0x114])
@pytest.mark.parametrize('resume,load,values,top', [
    (True, True, tuple(range(7)), 70), (True, False, tuple(range(6)), 70),
    (False, True, tuple(range(1,7)), 85), (False, False, tuple(range(1,6)), 85),
])
def test_menu_rows_respect_save_availability_and_build_layout(delta, resume, load, values, top):
    c, m = menu_context(resume=resume, load=load, delta=delta)
    assert c.menu().rows == tuple((value, top + 20 * row) for row, value in enumerate(values))
    assert c.point_menu(88, top + 26)
    assert int.from_bytes(m.read(0x900a, 2), 'little') == values[1]
    assert not c.point_menu(1, top + 26)
    assert not c.point_menu(88, 20)


def test_menu_transition_and_invalid_pointers_are_inert():
    c, m = menu_context()
    m.word(0x8004, 1)
    assert c.menu() is None and not c.point_menu(88, 130)
    m.word(0x8004, 0)
    m.word(0x8020, 0xfffffff0)
    assert c.menu() is None


@pytest.mark.parametrize('kind,offset,top', [(1,4,125), (5,4,125), (13,0x24,125),
                                         (15,4,135), (16,4,135)])
def test_dialog_click_selects_explicit_yes_or_no(kind, offset, top):
    c, m = menu_context(kind)
    assert c.point_menu(88, top + 3)
    assert (m.u32(0x9000 + offset), m.u32(0x9004 + offset)) == (1,0)
    assert c.point_menu(88, top + 18)
    assert (m.u32(0x9000 + offset), m.u32(0x9004 + offset)) == (0,1)


def test_relative_rotation_aims_native_camera_and_player_together():
    c, m = context()
    m.load_base = 0
    c.aim = None
    c.camera_basis, c.pitch_getter = 0x300, 0x400
    m.word(0x42b8, 0x8000)
    # Level camera facing +z: right is -x.
    m.write(0x80b8, struct.pack('<3i', 0, 0, 0x10000))
    m.write(0x80d0, struct.pack('<3i', -0x10000, 0, 0))
    calls = []
    m.call = lambda target, args: calls.append((target, args))
    # Nine right steps turn 90 degrees; pitch stops at the limit.
    assert c.rotate_view(9, 100)
    assert m.u32(0x4278) == (-9 * 0xa0000) & 0xffffffff
    # The native routine derives up; the native getter refreshes its cache.
    assert calls == [(0x300, [0x8000, 2]), (0x400, [0x8000])]
    pitch = math.radians(PITCH_LIMIT)
    forward = struct.unpack('<3i', m.read(0x80b8, 12))
    right = struct.unpack('<3i', m.read(0x80d0, 12))
    assert forward == tuple(round(v * 0x10000) for v in
                            (-math.cos(pitch), -math.sin(pitch), 0))
    assert right == (0, 0, -0x10000)
    assert c.view_angles(0x8000) == pytest.approx((-90, PITCH_LIMIT), abs=1e-3)

    calls.clear()
    assert c.rotate_view(0, 0)
    assert calls == []
    # A scripted pitch controller keeps its pitch.
    m.word(0x43d8, 1)
    assert c.rotate_view(0, -10)
    assert calls == []
    m.word(0x2210, 1)
    assert not c.rotate_view(10, 10)
    assert calls == []


def test_mouse_aim_reuses_written_angles_until_the_camera_moves():
    c, m = context()
    m.load_base = 0
    c.aim = None
    c.camera_basis, c.pitch_getter = 0x300, 0x400
    m.word(0x42b8, 0x8000)
    m.write(0x80b8, struct.pack('<3i', 0, 0, 0x10000))
    m.write(0x80d0, struct.pack('<3i', -0x10000, 0, 0))
    m.call = lambda target, args: None
    # Many tiny moves accumulate exactly instead of rereading the basis; the
    # camera follows the player's 16.16 heading, including its rounding.
    for _ in range(1000):
        assert c.rotate_view(.001, .001)
    heading = m.u32(0x4278) - (1 << 32)
    assert c.aim[1:] == pytest.approx((heading / 0x10000, 3))
    # Something else turned the camera: start from where it now points.
    m.write(0x80b8, struct.pack('<3i', 0x10000, 0, 0))
    m.write(0x80d0, struct.pack('<3i', 0, 0, 0x10000))
    assert c.rotate_view(0, 1)
    assert c.aim[1:] == pytest.approx((90, 3))


@pytest.mark.parametrize('delta', [0, -0x114])
def test_pause_overlay_targets_level_widget_instead_of_stale_shell_menu(delta):
    c, m = menu_context(delta=delta)
    m.word(0x2000 + 0x210 + delta, 1)
    m.word(0x2000 + 0x1ec + delta, 0x3000)
    m.word(0x3018, 0xa000)
    m.data[0xa004] = 1
    pause = c.menu()
    assert pause.kind == 0x101 and pause.rows[0] == (1, 30)
    assert c.point_menu(88, 141)
    assert int.from_bytes(m.read(0xa006, 2), 'little') == 8
    m.data[0xa004] = 2
    assert c.menu().kind == 0x102
    assert c.point_menu(88, 141)
    assert m.read(0xa00e, 1) == b'\x01'
    assert int.from_bytes(m.read(0xa006, 2), 'little') == 1


def test_scripted_pause_exposes_only_native_resume_target():
    c, m = menu_context()
    m.word(0x2210, 1)
    m.word(0x3018, 0xa000)
    m.data[0xa004] = 1
    c.pause_guard = 0x500
    m.load_base = 0
    m.call = lambda target, args: 0  # native predicate disables the full menu
    assert c.menu().rows == ((1, 100),)
    assert not c.point_menu(88, 141)
    assert c.point_menu(88, 106)


@pytest.mark.parametrize('mode,top', [(0x13,125), (0x15,125), (0x19,135)])
def test_pause_confirmation_targets_explicit_no_choice(mode, top):
    c, m = menu_context()
    m.word(0x2210, 1)
    m.word(0x3018, 0xa000)
    m.data[0xa004] = mode
    assert c.point_menu(88, top + 21)
    assert int.from_bytes(m.read(0xa006, 2), 'little') == 2
