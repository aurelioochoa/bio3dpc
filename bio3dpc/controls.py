"""Build-guarded input adapter for the verified mobile binaries.

PC equipment selection is implemented by pressing the game's own cycle keys.
These offsets are build-specific; an image hash prevents applying them to a
different module. Relative mouse look aims the native camera, rebuilt by the
game's own basis routine, and keeps the player's heading in sync. Mouse look
suppresses the handset's walking camera resets and pitch slowdown through
hash-guarded hooks; module files and inventory are untouched.
"""
from dataclasses import dataclass
import hashlib
import math
import struct


VX8350_SHA256 = '53736c667d6635a5a6bbaecb3d63db6da047c4f6be2e6bc0135d13321c33d4fa'
N7705_SHA256 = 'ff2bdd5814230aea556af04e4df1f93c2512fa90a22c21cf09095cdd4cd5d09d'
# Native functions must match both static instructions and live input sampling.
INPUT_OFFSETS = {
    VX8350_SHA256: (0xbef50, 0x2c898),
    N7705_SHA256: (0xbf2e0, 0x2cccc),
}
# Native player pitch leveling (both immediate slope alignment and animated
# interpolation). PC mouse look bypasses it, leaving movement/collision and
# scripted camera operations on their native paths.
AUTO_LEVEL_OFFSETS = {
    VX8350_SHA256: 0xa4bbc,
    N7705_SHA256: 0xa4eb4,
}
# Walking/collision restores a cached direction through this setter. Preserve
# the current mouse direction at those two callers, keeping other setters live.
WALK_DIRECTION_OFFSETS = {
    VX8350_SHA256: (0x9fe34, (0xd60c, 0x10318)),
    N7705_SHA256: (0xa02b8, (0xd694, 0x103a0)),
}
# Native walk step (camera, speed) adds forward.x/z * speed to the position,
# so a pitched camera walks slower. Its two callers are the player's forward
# and backward input (return addresses listed). The handset levels the
# camera while walking; mouse look keeps pitch and restores the level speed.
WALK_STEP_OFFSETS = {
    VX8350_SHA256: (0x59b54, (0xbe700, 0xbe76c)),
    N7705_SHA256: (0x59e04, (0xbea74, 0xbeae0)),
}
# Camera basis: forward +0xb8, up +0xc4, right +0xd0 (16.16 vectors). The
# keypad turns through vtable +0xc/+0x10, which rotate forward about up/right
# and then call this routine to rebuild the other axes. Mode 2 keeps forward
# and right and derives up, the native pitch path.
CAMERA_BASIS_OFFSETS = {
    VX8350_SHA256: 0xb864c,
    N7705_SHA256: 0xb8860,
}
TURN_STEP = 10      # degrees per native left/right step
PITCH_STEP = 3      # degrees per native up/down step
# The keypad stops tilting past 25 degrees (28 with its last step). Mouse
# look reaches nearly straight up and down, keeping that point on screen.
# Each frame, player routine 0x67f8c (portrait) rebuilds right as forward x
# world up with a 10-bit cross product: at 90 degrees right is zeroed, and
# past 85 degrees its rounding rolls the view by more than one degree.
PITCH_LIMIT = 85
# Within this many degrees, the camera still holds the aim written last; reuse
# that exact aim, since reading back the fixed-point basis drifts over time.
AIM_TOLERANCE = .05


@dataclass(frozen=True)
class Gameplay:
    player: int
    mode: int
    weapon: int
    plasmid: int
    weapons: tuple[int, ...]
    plasmids: tuple[int, ...]
    can_select: bool = True


@dataclass(frozen=True)
class Menu:
    address: int
    kind: int
    rows: tuple[tuple[int, int], ...]  # native selection value, top of text


class GameControls:
    def __init__(self, boot, *, mouse_look=True):
        self.boot = boot
        self.mouse_look = mouse_look
        module = getattr(boot, 'mod', None)
        digest = hashlib.sha256(module.data).hexdigest() if module else None
        offsets = INPUT_OFFSETS.get(digest)
        self.supported = offsets is not None
        self.camera_basis = CAMERA_BASIS_OFFSETS.get(digest)
        self.pitch_getter = 0x2d27c if digest == VX8350_SHA256 else 0x2d6b0
        self.aim = None     # (camera, heading, pitch) set by rotate_view
        self.pause_guard = 0x47748 if digest == VX8350_SHA256 else 0x479cc
        # The Nokia shell omits 0x114 bytes of handset-specific engine fields.
        # Player/equipment structures retain the same layout in both builds.
        self.game_delta = -0x114 if digest == N7705_SHA256 else 0
        self.look_ticks = 0
        self.native_player = 0
        self.key_samples = {}
        self.hooks = []
        if self.supported:
            from unicorn import UC_HOOK_CODE
            from unicorn.arm_const import UC_ARM_REG_R0
            # A timer can run without sampling camera input. Observe its
            # native input pass, without altering execution or guest data.
            def camera_sampled(uc, address, size, user):
                self.look_ticks += 1
                self.native_player = uc.reg_read(UC_ARM_REG_R0)
            address = boot.m.load_base + offsets[0]
            self.hooks.append(boot.m.uc.hook_add(
                UC_HOOK_CODE, camera_sampled, begin=address, end=address))
            boot.m.uc.ctl_remove_cache(address, address + 4)

            from unicorn.arm_const import UC_ARM_REG_PC, UC_ARM_REG_LR
            def preserve_mouse_pitch(uc, address, size, user):
                if not self.mouse_look:
                    return
                player = uc.reg_read(UC_ARM_REG_R0)
                state = self.state()
                if (state is not None and state.player == player and
                        boot.m.u32(player + 0x3d8) == 0):
                    uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
            address = boot.m.load_base + AUTO_LEVEL_OFFSETS[digest]
            self.hooks.append(boot.m.uc.hook_add(
                UC_HOOK_CODE, preserve_mouse_pitch, begin=address, end=address))
            boot.m.uc.ctl_remove_cache(address, address + 4)

            direction_setter, direction_callers = WALK_DIRECTION_OFFSETS[digest]
            def preserve_walking_direction(uc, address, size, user):
                if not self.mouse_look:
                    return
                caller = uc.reg_read(UC_ARM_REG_LR) - boot.m.load_base
                if caller not in direction_callers:
                    return
                state = self.state()
                if (state is not None and boot.m.u32(state.player + 0x3d8) == 0 and
                        uc.reg_read(UC_ARM_REG_R0) == boot.m.u32(state.player + 0x2b8)):
                    uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
            address = boot.m.load_base + direction_setter
            self.hooks.append(boot.m.uc.hook_add(
                UC_HOOK_CODE, preserve_walking_direction, begin=address, end=address))
            boot.m.uc.ctl_remove_cache(address, address + 4)

            from unicorn.arm_const import UC_ARM_REG_R1
            walk_step, walk_callers = WALK_STEP_OFFSETS[digest]
            def level_walking_speed(uc, address, size, user):
                if not self.mouse_look:
                    return
                if uc.reg_read(UC_ARM_REG_LR) - boot.m.load_base not in walk_callers:
                    return
                state = self.state()
                camera = uc.reg_read(UC_ARM_REG_R0)
                if (state is None or boot.m.u32(state.player + 0x3d8) != 0 or
                        camera != boot.m.u32(state.player + 0x2b8)):
                    return
                fx, fy, fz = struct.unpack('<3i', boot.m.read(camera + 0xb8, 12))
                horizontal = math.hypot(fx, fz)
                if horizontal and fy:
                    speed = uc.reg_read(UC_ARM_REG_R1)
                    if speed & 0x80000000:
                        speed -= 0x100000000
                    speed = round(speed * math.hypot(fx, fy, fz) / horizontal)
                    uc.reg_write(UC_ARM_REG_R1, speed & 0xffffffff)
            address = boot.m.load_base + walk_step
            self.hooks.append(boot.m.uc.hook_add(
                UC_HOOK_CODE, level_walking_speed, begin=address, end=address))
            boot.m.uc.ctl_remove_cache(address, address + 4)

            def key_sampled(uc, address, size, user):
                key = uc.reg_read(UC_ARM_REG_R1) & 0xffff
                self.key_samples[key] = self.key_samples.get(key, 0) + 1
            address = boot.m.load_base + offsets[1]
            self.hooks.append(boot.m.uc.hook_add(
                UC_HOOK_CODE, key_sampled, begin=address, end=address))
            boot.m.uc.ctl_remove_cache(address, address + 4)

    def input_tick(self, key):
        return self.key_samples.get(key, 0)

    def menu(self) -> Menu | None:
        """Native menu rows in device coordinates; ignore transition screens."""
        if not self.supported or not self.boot.applet:
            return None
        m = self.boot.m
        game = m.u32(self.boot.applet + 0x20)
        if game < m.heap_base or not m.contains(game, 0x214):
            return None
        # The in-level pause UI is owned by the level, independently of the
        # shell menu manager (which can still report the old loading screen).
        if m.u32(game + 0x210 + self.game_delta):
            level = m.u32(game + 0x1ec + self.game_delta)
            if level < m.heap_base or not m.contains(level, 0x24):
                return None
            address = m.u32(level + 0x18)
            if address < m.heap_base or not m.contains(address, 0x36):
                return None
            mode = m.read(address + 4, 1)[0]
            middle = self.boot.runtime.height // 2
            rows = ()
            if mode == 1:
                guard = getattr(self, 'pause_guard', None)
                if guard and not m.call(m.load_base + guard, [game]):
                    # Scripted scenes expose only Resume, rather than the
                    # full pause list. Follow the native scene restriction.
                    rows = ((1, middle if self.game_delta else middle - 10),)
                else:
                    rows = tuple((value, middle - 80 + 15 * (value - 1)) for value in range(1, 11))
            elif mode == 2:
                rows = ((0, middle), (1, middle + 25))
            elif mode in (0x13, 0x15, 0x19):  # overwrite, save on quit, load
                top = middle + (25 if mode == 0x19 else 15)
                rows = ((1, top), (2, top + 15))
            return Menu(address, 0x100 + mode, rows)
        manager = m.u32(game + 0x1c4 + self.game_delta)
        if manager < m.heap_base or not m.contains(manager, 0x44) or m.u32(manager + 4):
            return None
        kind = m.read(manager, 1)[0]
        address = m.u32(manager + 0x20)
        if address < m.heap_base or not m.contains(address, 0x2c):
            return None
        middle = self.boot.runtime.height // 2
        rows = ()
        if kind == 4:
            resume, load = bool(m.u32(address + 0x10)), bool(m.u32(address + 0x14))
            first, last = (0 if resume else 1), (6 if load else 5)
            top = middle - (40 if resume else 25)
            rows = tuple((value, top + 20 * row) for row, value in enumerate(range(first, last + 1)))
        elif kind in (1, 5, 13, 15, 16):
            top = middle + (25 if kind in (15, 16) else 15)
            rows = ((0, top), (1, top + 15))
        elif kind == 11:
            rows = ((0, middle), (1, middle + 25))
            if m.u32(game + 0x2e0 + self.game_delta):
                rows += ((2, middle + 45),)
        return Menu(address, kind, rows)

    def point_menu(self, x: float, y: float) -> bool:
        """Highlight the row under the pointer, without activating it."""
        menu = self.menu()
        if menu is None:
            return False
        m = self.boot.m
        width = self.boot.runtime.width
        # Match the row bands, leaving titles, gaps and window edges inert.
        if not 10 <= x < width - 10:
            return False
        for value, top in menu.rows:
            if top - 2 <= y < top + 15:
                if menu.kind == 4:
                    m.w16(menu.address + 0xa, value)
                elif menu.kind in (0x101, 0x113, 0x115, 0x119):
                    m.w16(menu.address + 6, value)
                elif menu.kind == 0x102:
                    m.write(menu.address + 0xe, bytes([value]))
                    m.w16(menu.address + 6, value)
                elif menu.kind == 11:
                    m.write(menu.address + 5, bytes([value]))
                    m.w16(menu.address + 6, value)
                else:
                    offset = 0x24 if menu.kind == 13 else 4
                    m.w32(menu.address + offset, int(value == 0))
                    m.w32(menu.address + offset + 4, int(value == 1))
                return True
        return False

    def menu_adjustment(self, direction: int) -> bool:
        """Wheel over the native volume row adjusts volume rather than focus."""
        from .services import AVK_LEFT, AVK_RIGHT
        menu = self.menu()
        if menu is None or menu.kind not in (11, 0x102):
            return False
        field = 5 if menu.kind == 11 else 0xe
        if self.boot.m.read(menu.address + field, 1)[0] != 0:
            return False
        key = AVK_RIGHT if direction > 0 else AVK_LEFT
        self.boot.runtime.post_key(key)
        self.boot.runtime.post_key(key, press=False)
        return True

    def close(self):
        for hook in self.hooks:
            self.boot.m.uc.hook_del(hook)
        self.hooks.clear()

    def rotate_view(self, yaw: float, pitch: float) -> bool:
        """Apply all pending relative travel now, with no keypad hold tail.

        Units are native look steps (10 degrees of turn, 3 degrees of pitch),
        including fractions; positive pitch looks down, like the DOWN key.

        The keypad's rotation methods suit its whole steps only: their sine
        table ignores turns below about half a degree, rounds larger ones, and
        yaws about the camera's tilted up axis, rolling a pitched view. Mouse
        look instead sets forward and right from the heading and pitch, then
        lets the native basis routine derive up and normalize, as those
        methods finish.
        """
        state = self.state()
        if state is None:
            return False
        m = self.boot.m
        camera = m.u32(state.player + 0x2b8)
        if camera < m.heap_base or not m.contains(camera, 0xdc):
            return False

        heading = round(-yaw * (TURN_STEP << 16))
        view_heading, view_pitch = self.view_angles(camera)
        if self.aim and self.aim[0] == camera:
            _, aim_heading, aim_pitch = self.aim
            if (abs((view_heading - aim_heading + 180) % 360 - 180) < AIM_TOLERANCE and
                    abs(view_pitch - aim_pitch) < AIM_TOLERANCE):
                view_heading, view_pitch = aim_heading, aim_pitch
        target = view_pitch
        # player+0x3d8 is a scripted pitch controller; leave it in charge.
        if pitch and m.u32(state.player + 0x3d8) == 0:
            target = max(-PITCH_LIMIT, min(PITCH_LIMIT, view_pitch + pitch * PITCH_STEP))
        if not heading and target == view_pitch:
            return True
        # Right turns decrease both the player's heading and atan2(x, z).
        view_heading = (view_heading + heading / 0x10000 + 180) % 360 - 180
        self.aim = (camera, view_heading, target)
        h, p = math.radians(view_heading), math.radians(target)

        def vector(x, y, z):
            return struct.pack('<3i', *(round(c * 0x10000) for c in (x, y, z)))
        m.write(camera + 0xb8, vector(math.sin(h) * math.cos(p), -math.sin(p),
                                      math.cos(h) * math.cos(p)))
        m.write(camera + 0xd0, vector(-math.cos(h), 0, math.sin(h)))
        m.call(m.load_base + self.camera_basis, [camera, 2])
        # The native pitch getter also caches its result at camera+0x48.
        m.call(m.load_base + self.pitch_getter, [camera])
        if heading:
            m.w32(state.player + 0x278, (m.u32(state.player + 0x278) + heading) & 0xffffffff)
        return True

    def view_angles(self, camera: int) -> tuple[float, float]:
        """Camera heading and pitch in degrees; positive pitch looks down.

        Heading matches player+0x278. Forward holds it exactly; the
        horizontal right axis, rounded each frame by the native rebuild, only
        stands in when forward points straight up or down. Pitch is the
        native getter's acos(forward.y) - 90, without its fixed-point acos
        losing half a degree near the poles.
        """
        fx, fy, fz, *_, rx, _, rz = struct.unpack('<9i', self.boot.m.read(camera + 0xb8, 36))
        if fx or fz:
            heading = math.degrees(math.atan2(fx, fz))
        else:
            heading = math.degrees(math.atan2(rz, -rx))
        pitch = -math.degrees(math.atan2(fy, math.hypot(fx, fz)))
        return heading, pitch

    def state(self) -> Gameplay | None:
        if not self.supported or not self.boot.applet:
            return None
        m = self.boot.m

        def pointer(address, size):
            value = m.u32(address)
            return value if value >= m.heap_base and m.contains(value, size) else 0

        game = pointer(self.boot.applet + 0x20, 0x214)
        delta = getattr(self, 'game_delta', 0)
        if (not game or m.read(game + 0x1b8 + delta, 1)[0] != 2 or
                m.u32(game + 0x1c0 + delta) != 0 or
                m.u32(game + 0x210 + delta) != 0):
            return None
        level = pointer(game + 0x1ec + delta, 0x24)
        player = pointer(level + 0x20, 0x430) if level else 0
        if not player:
            return None
        mode = m.read(player + 0x230, 1)[0]
        weapon, plasmid = m.u32(player + 0x21c), m.u32(player + 0x220)
        if mode not in (0, 1, 2) or weapon >= 5 or plasmid >= 3:
            return None

        def available(offset, count, flag):
            slots = []
            for slot in range(count):
                item = pointer(player + offset + slot * 4, flag + 1)
                if item and m.read(item + flag, 1)[0]:
                    slots.append(slot)
            return tuple(slots)

        can_select = m.u32(player + 0x298) == 0
        animation = pointer(player + 0x1c0, 12)
        descriptor = pointer(animation + 8, 0xb9) if animation else 0
        if descriptor and m.read(descriptor + 0xb8, 1)[0] == 3:
            can_select = False
        return Gameplay(player, mode, weapon, plasmid,
                        available(0x1f8, 5, 0x5b),
                        available(0x20c, 3, 0x56), can_select)
