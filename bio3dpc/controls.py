"""Build-guarded input adapter for the verified mobile binaries.

PC equipment selection is implemented by pressing the game's own cycle keys.
These offsets are build-specific; an image hash prevents applying them to a
different module. Relative mouse look calls the native camera rotation methods
and keeps the player's heading in sync. Mouse look suppresses the handset's
walking camera resets through hash-guarded hooks; module files and inventory
are untouched.
"""
from dataclasses import dataclass
import hashlib


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
        self.pitch_getter = 0x2d27c if digest == VX8350_SHA256 else 0x2d6b0
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

        Units match the original look steps (10 degrees of player heading,
        20 degrees of camera yaw, 6 degrees of pitch), including fractions.
        Calling the same virtual methods preserves the native camera matrix.
        """
        state = self.state()
        if state is None:
            return False
        m = self.boot.m
        camera = m.u32(state.player + 0x2b8)
        if camera < m.heap_base or not m.contains(camera, 0xdc):
            return False
        table = m.u32(camera)
        if not m.contains(table, 0x14):
            return False

        def rotate(slot, angle):
            target = (table + m.u32(table + slot)) & 0xffffffff
            m.call(target, [camera, angle & 0xffffffff])

        heading = round(-yaw * 0xa0000)
        if heading:
            # Native right-turn input decreases the player's heading but
            # passes a positive angle to the camera's rotation method.
            rotate(0xc, round(yaw * 0x140000))
            m.w32(state.player + 0x278, (m.u32(state.player + 0x278) + heading) & 0xffffffff)
        if pitch and m.u32(state.player + 0x3d8) == 0:
            current = m.call(m.load_base + self.pitch_getter, [camera])
            if current & 0x80000000:
                current -= 0x100000000
            target = max(-0x190000, min(0x190000, current + round(pitch * 0x60000)))
            if target != current:
                rotate(0x10, target - current)
        return True

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
