"""Compare mouse aiming with real guest input and subsequent movement."""
import hashlib
import math
import struct

import pytest
from unicorn import UC_HOOK_CODE

from bio3dpc.boot import Boot
from bio3dpc.controls import (
    AUTO_LEVEL_OFFSETS, CAMERA_BASIS_OFFSETS, GameControls, INPUT_OFFSETS,
    PITCH_LIMIT, WALK_STEP_OFFSETS,
)
from bio3dpc.profiles import PROFILES, discover_root
from bio3dpc.services import (
    AVK_2, AVK_4, AVK_6, AVK_7, AVK_8, AVK_9, AVK_SELECT, AVK_SOFT2,
    AVK_UP, EVT_KEY, EVT_KEY_RELEASE, dispatch_applet,
)


@pytest.mark.parametrize('profile', PROFILES, ids=lambda profile: profile.id)
def test_auto_level_hook_targets_native_player_pitch_leveling(profile):
    module = profile.mod_path(discover_root())
    if not module.is_file():
        pytest.skip(f'Original {profile.id} module not present')
    code = module.read_bytes()
    start = AUTO_LEVEL_OFFSETS[profile.sha256]
    # Unconditional PUSH, MOV r4,r0, LDR r1,[r4,#0x37c]: hook the native
    # leveling dispatcher before either its instant or interpolated path.
    assert struct.unpack_from('<3I', code, start) == (0xe92d4030, 0xe1a04000, 0xe594137c)


def angle(a, b):
    """Degrees between two packed 16.16 vectors."""
    a, b = struct.unpack('<3i', a), struct.unpack('<3i', b)
    cosine = sum(x*y for x, y in zip(a, b)) / math.sqrt(
        sum(x*x for x in a) * sum(y*y for y in b))
    return math.degrees(math.acos(max(-1, min(1, cosine))))


@pytest.mark.parametrize('profile', PROFILES, ids=lambda profile: profile.id)
def test_mouse_look_hooks_target_native_camera_routines(profile):
    module = profile.mod_path(discover_root())
    if not module.is_file():
        pytest.skip(f'Original {profile.id} module not present')
    code = module.read_bytes()
    # PUSH {r0-r6,lr}; ADD r6,r0,#0xc4: the camera basis rebuild.
    basis = CAMERA_BASIS_OFFSETS[profile.sha256]
    assert struct.unpack_from('<2I', code, basis) == (0xe92d407f, 0xe28060c4)
    # LDR r2,[r0,#0xb8]; ASR r1,r1,#6: the walk step scales forward.
    step, callers = WALK_STEP_OFFSETS[profile.sha256]
    assert struct.unpack_from('<2I', code, step) == (0xe59020b8, 0xe1a01341)
    for caller in callers:
        word, = struct.unpack_from('<I', code, caller - 4)
        target = caller - 4 + 8 + (((word & 0xffffff) ^ 0x800000) - 0x800000) * 4
        assert word >> 24 == 0xeb and target == step


@pytest.fixture(params=PROFILES, ids=lambda profile: profile.id)
def gameplay(request, tmp_path, monkeypatch):
    profile = request.param
    module = profile.mod_path(discover_root())
    if not module.is_file():
        pytest.skip(f'Original {profile.id} module not present')
    monkeypatch.setenv('BIO3DPC_AUDIO', '0')
    boot = Boot(module, data_dir=profile.data_path(discover_root()),
                save_dir=tmp_path, level='error')
    controls = None
    try:
        boot.load_module()
        boot.create_applet()
        boot.app_start()
        controls = GameControls(boot)

        def key(keycode, seconds=.12):
            boot.runtime.post_key(keycode)
            boot.run(seconds=seconds)
            boot.runtime.post_key(keycode, press=False)
            boot.run(seconds=.15)

        boot.run(seconds=4)
        for _ in range(90):
            state = controls.state()
            if state and (profile.id == 'n7705' or
                          (state.mode in (1, 2) and state.weapons)):
                break
            signature = hashlib.sha1(boot.runtime.snapshot_rgb()[2]).hexdigest()[:12]
            swimming = signature == '0f62be81305c'
            key(AVK_UP if swimming else AVK_SELECT, 2 if swimming else .12)
            boot.run(seconds=.6)
            if hashlib.sha1(boot.runtime.snapshot_rgb()[2]).hexdigest()[:12] == signature:
                key(AVK_SOFT2)
        else:
            pytest.fail(f'{profile.id} did not reach gameplay')
        yield boot, controls, state, key
    finally:
        if controls is not None:
            controls.close()
        boot.shutdown()


def test_mouse_turn_matches_native_turn_and_survives_walking(gameplay):
    boot, controls, state, key = gameplay
    m = boot.m
    camera = m.u32(state.player + 0x2b8)
    input_pass = m.load_base + INPUT_OFFSETS[boot.profile.sha256][0]

    for yaw, native_key in ((1, AVK_6), (-1, AVK_4)):
        saved_camera = m.read(camera, 0xdc)
        saved_heading = m.u32(state.player + 0x278)
        saved_counters = m.read(state.player + 0x3d4, 2)
        saved_position = m.read(camera + 0xac, 12)
        dispatch_applet(m, boot.applet, EVT_KEY, native_key)
        m.call(input_pass, [state.player])
        dispatch_applet(m, boot.applet, EVT_KEY_RELEASE, native_key)
        native_basis = m.read(camera + 0xb8, 36)
        native_heading = m.u32(state.player + 0x278)
        assert native_heading != saved_heading

        m.write(camera, saved_camera)
        m.w32(state.player + 0x278, saved_heading)
        m.write(state.player + 0x3d4, saved_counters)
        assert controls.rotate_view(yaw, 0)
        # The native sine table misses its 10 degree step by ~0.06 degrees.
        for offset in (0, 12, 24):
            assert angle(native_basis[offset:offset + 12],
                         m.read(camera + 0xb8 + offset, 12)) < .1
        assert m.u32(state.player + 0x278) == native_heading
        assert m.read(camera + 0xac, 12) == saved_position

    # The portrait fixture reaches free movement; landscape's opening scene
    # permits looking but blocks walking until its scripted sequence finishes.
    if boot.profile.id == 'vx8350':
        # The raw key events above bypass the runtime's key tracking; a
        # native input pass can still act on them once. Let that settle.
        boot.run(seconds=.3)
        assert controls.rotate_view(2, 2)
        direction = m.read(camera + 0xb8, 12)
        heading = m.u32(state.player + 0x278)
        dx, _, dz = struct.unpack('<3i', direction)
        view_heading = math.degrees(math.atan2(dx, dz)) % 360
        error = (view_heading - heading / 65536 + 180) % 360 - 180
        assert abs(error) < .3
        for movement in (AVK_7, AVK_9, AVK_2, AVK_8):
            position = m.read(camera + 0xac, 12)
            key(movement, .25)
            assert m.read(camera + 0xac, 12) != position
            # Native renormalization may move the fixed-point basis by a unit.
            assert angle(m.read(camera + 0xb8, 12), direction) < .01
            assert m.u32(state.player + 0x278) == heading


def test_fractional_mouse_aim_survives_forward_and_backward_movement(gameplay):
    boot, controls, state, key = gameplay
    m = boot.m
    camera = m.u32(state.player + 0x2b8)
    for yaw, pitch in ((.125, -.5), (4.75, 1.25), (40.25, -2.5), (-80.5, 3.5)):
        assert controls.rotate_view(yaw, pitch)
        direction = m.read(camera + 0xb8, 12)
        heading = m.u32(state.player + 0x278)
        for movement in (AVK_2, AVK_8):
            key(movement, .5)
            current = m.read(camera + 0xb8, 12)
            # Native walking renormalizes the fixed-point basis, introducing
            # small rounding differences even when aim is preserved.
            assert angle(direction, current) < .25, (yaw, pitch, movement, direction, current)
            assert m.u32(state.player + 0x278) == heading

    if boot.profile.id == 'vx8350':
        # Turning mouse look off returns walking to the handset's leveling
        # path. Whether it then moves pitch depends on slope and height.
        level = m.load_base + AUTO_LEVEL_OFFSETS[boot.profile.sha256]
        entered = []
        hook = m.uc.hook_add(UC_HOOK_CODE, lambda *args: entered.append(1),
                             begin=level + 4, end=level + 4)
        m.uc.ctl_remove_cache(level, level + 8)
        try:
            assert controls.rotate_view(0, 100)
            key(AVK_2, .5)
            assert not entered
            controls.mouse_look = False
            key(AVK_8, .5)
            assert entered
        finally:
            m.uc.hook_del(hook)


def test_mouse_look_reaches_steep_pitch_without_roll_drift_or_slow_walking(gameplay):
    boot, controls, state, key = gameplay
    m = boot.m
    camera = m.u32(state.player + 0x2b8)

    def pitch():
        return controls.view_angles(camera)[1]

    def heading_error():
        heading = m.u32(state.player + 0x278)
        heading = (heading - (1 << 32) if heading >> 31 else heading) / 0x10000
        return abs((controls.view_angles(camera)[0] - heading + 180) % 360 - 180)

    for direction in (1, -1):
        for _ in range(30):
            assert controls.rotate_view(0, 2 * direction)
            boot.run(seconds=.03)
        assert pitch() == pytest.approx(direction * PITCH_LIMIT, abs=.01)
        # Turning while pitched keeps pitch, a level right axis and the
        # player's heading, including across the per-frame basis rebuild.
        for _ in range(60):
            assert controls.rotate_view(.37, 0)
            boot.run(seconds=.02)
        assert pitch() == pytest.approx(direction * PITCH_LIMIT, abs=.01)
        right = struct.unpack('<3i', m.read(camera + 0xd0, 12))
        assert abs(right[1]) < 0x100
        assert heading_error() < .05

    for _ in range(2000):
        assert controls.rotate_view(.013, .007)
        assert controls.rotate_view(-.013, -.007)
    assert heading_error() < .05

    if boot.profile.id == 'vx8350':
        def stride():
            start = struct.unpack('<3i', m.read(camera + 0xac, 12))
            key(AVK_2, .4)
            end = struct.unpack('<3i', m.read(camera + 0xac, 12))
            key(AVK_8, .4)
            return math.hypot(end[0] - start[0], end[2] - start[2])

        assert controls.rotate_view(0, -pitch() / 3)
        stride()  # reach walking speed
        level = stride()
        assert controls.rotate_view(0, PITCH_LIMIT / 3)
        assert stride() > .6 * level
