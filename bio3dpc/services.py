"""BREW runtime services: guest objects that implement the AEE interfaces.

Every interface the module can reach is a guest-memory object whose first word
points at a vtable of trap slots (see ``machine.bind``).  Host handlers are
plain Python callables ``handler(machine, *args)``; anything not implemented is
still bound to a logging stub so the service trace shows exactly what the game
asked for instead of jumping into zeroed memory.

Method order comes from ``ifaces_gen`` (generated from the SDK headers); the
object model mirrors AEEAppGen: each object is reference counted, and the
runtime keeps strong references for the interfaces it hands out.
"""
from __future__ import annotations

import inspect
import os
import shutil
import struct
import time

from .audio import FALLBACK_SECONDS, AudioEngine
from .ifaces_gen import IFACES

# ------------------------------------------------------------------ constants
# AEEError.h
SUCCESS = 0x00000000
EFAILED = 0x00000001
ENOMEMORY = 0x00000002
ECLASSNOTSUPPORT = 0x00000003
EBADCLASS = 0x0000000A
EBADPARM = 0x0000000E
EUNSUPPORTED = 0x00000014
EALREADY = 0x0000001A
ENOSUCH = 0x00000027
EBUFFERTOOSMALL = 0x00000026

# AEEError.h FILE_ERROR block (0x100 + n)
EFILEEXISTS = 0x00000100
EFILENOEXISTS = 0x00000101
EBADSEEKPOS = 0x00000104
EFILEEOF = 0x00000105
EFILEOPEN = 0x00000107

# AEEFile.h open modes / seek types / attributes / permissions
OFM_READ = 0x0001
OFM_READWRITE = 0x0002
OFM_CREATE = 0x0004
OFM_APPEND = 0x0008
SEEK_START = 0
SEEK_END = 1
SEEK_CURRENT = 2
FA_READONLY = 0x0004
AEEFP_READ = 0x0001
AEEFP_WRITE = 0x0002
AEEFP_CREATE = 0x0004
AEEFP_DELETE = 0x0008

# AEECLSID.h / built-in classes used by the game engine.
AEECLSID_DISPLAY = 0x01001001
AEECLSID_HEAP = 0x01001002
AEECLSID_FILEMGR = 0x01001003
# Nokia 7705 requests this OEM filesystem class with the IFileMgr ABI.
# Its Test/OpenFile vtable calls are corroborated by live asset loading.
AEECLSID_NOKIA_FILEMGR = 0x0102D575
AEECLSID_MEMASTREAM = 0x0100100C   # AEECLSID_CORE+12 (IMemAStream)
AEECLSID_BITMAP = 0x01001021
AEECLSID_SOCKET = 0x0100102D
AEECLSID_WEB = 0x01005000          # AEECLSID_PROTO (IWeb)
AEECLSID_PNG = 0x01004004          # AEECLSID_VIEW+4 (legacy IImage PNG viewer)
AEECLSID_MEDIAUTIL = 0x0100550D    # AEECLSID_MULTIMEDIA+13
AEECLSID_SOUNDPLAYER = 0x01002000
AEECLSID_GRAPHICS = 0x01002001
AEECLSID_MEDIA = 0x01005500
AEECLSID_IMAGEMGR = 0x01002004

# ISoundPlayer (AEESoundPlayer.h).
SDT_NONE, SDT_FILE, SDT_BUFFER, SDT_VOICEPROMPT = 0, 1, 2, 3
SP_STATUS_CB, SP_PLAY_CB, SP_TIME_CB, SP_SOUND_CB, SP_VOLUME_CB = range(5)
(SP_UNKNOWN, SP_SUCCESS, SP_REWIND, SP_FFORWARD, SP_PAUSE, SP_RESUME,
 SP_TEMPO, SP_TUNE, SP_PAN, SP_AUDIO_SPEC, SP_TICK_UPDATE,
 SP_DATA_ACCESS_DELAY, SP_ABORTED, SP_REPEAT, SP_DONE,
 SP_FAILURE) = range(16)
AEE_MAX_VOLUME = 100

# IAStream::Read returns this when no data is available right now.
AEE_STREAM_WOULDBLOCK = 0xFFFFFFFE  # (int32)-2

# IImage SetParm selectors (AEEIImage.h).
IPARM_SIZE = 0
IPARM_OFFSET = 1
IPARM_CXFRAME = 2
IPARM_ROP = 3
IPARM_NFRAMES = 4
IPARM_RATE = 5
IPARM_OFFSCREEN = 6
IPARM_REDRAW = 7
IPARM_DISPLAY = 8
IPARM_GETROP = 9
IPARM_GETBITMAP = 10
IPARM_DECODER = 11
IPARM_SCALE = 12
IPARM_EXTFUNC = 13

# AEERasterOp (AEERasterOp.h)
(RO_OR, RO_XOR, RO_COPY, RO_NOT, RO_OLDMASK, RO_MERGENOT, RO_ANDNOT,
 RO_TRANSPARENT, RO_AND, RO_BLEND) = range(10)

# Interface IDs (AEEIDIB.h): the engine asks the device bitmap for IDIB so it
# can write pixels straight into pBmp - this is the software renderer's target.
AEEIID_IDIB = 0x01001045
AEEIID_DIB_20 = 0x0100102C

# IDIB_COLORSCHEME_* (AEEIDIB.h)
IDIB_COLORSCHEME_565 = 16

# struct IDIB (AEEIDIB.h) - public members, so the guest reads this directly.
IDIB_PALETTE_MAP = 4
IDIB_PBMP = 8
IDIB_PRGB = 12
IDIB_NC_TRANSPARENT = 16
IDIB_CX = 20
IDIB_CY = 22
IDIB_NPITCH = 24
IDIB_CNTRGB = 26
IDIB_NDEPTH = 28
IDIB_NCOLORSCHEME = 29
IDIB_SIZE = 36

# AEE.h event codes.
EVT_APP_START = 0x0000
EVT_APP_STOP = 0x0001
EVT_APP_SUSPEND = 0x0002
EVT_APP_RESUME = 0x0003
EVT_KEY = 0x0100
EVT_KEY_PRESS = 0x0101
EVT_KEY_RELEASE = 0x0102
EVT_CHAR = 0x0104

# Virtual key codes (AEEVCodes.h, AVK_*).  The device sends these as the
# wParam of EVT_KEY / EVT_KEY_PRESS / EVT_KEY_RELEASE.
AVK_0, AVK_1, AVK_2, AVK_3, AVK_4, AVK_5, AVK_6, AVK_7, AVK_8, AVK_9 = range(
    0xE021, 0xE02B)
AVK_STAR, AVK_POUND = 0xE02B, 0xE02C
AVK_POWER, AVK_END, AVK_SEND, AVK_CLR = 0xE02D, 0xE02E, 0xE02F, 0xE030
AVK_UP, AVK_DOWN, AVK_LEFT, AVK_RIGHT, AVK_SELECT = 0xE031, 0xE032, 0xE033, \
    0xE034, 0xE035
AVK_SOFT1, AVK_SOFT2, AVK_SOFT3, AVK_SOFT4 = 0xE036, 0xE037, 0xE038, 0xE039
AVK_FUNCTION1, AVK_FUNCTION2, AVK_FUNCTION3 = 0xE03A, 0xE03B, 0xE03C

# The display this port targets (LG VX8350 build).
SCREEN_WIDTH = 176
SCREEN_HEIGHT = 220
COLOR_DEPTH = 16

# struct AEERect (AEE.h): two signed origins, two unsigned sizes.
_AEERECT = struct.Struct("<hhHH")

# RGB565 -> RGB888 planes (see BrewRuntime._rgb565_to_rgb888).
def _rgb565_planes() -> tuple[bytes, bytes, bytes, bytes]:
    """Byte-position translation tables that expand 565 to 888 without a
    per-pixel loop: each output channel is a function of one input byte (or,
    for green, the sum of one function of each input byte)."""
    high_r = bytearray(256)
    high_g = bytearray(256)
    low_g = bytearray(256)
    low_b = bytearray(256)
    for value in range(256):
        r5 = value >> 3                       # high byte holds bits 11-15
        high_r[value] = (r5 << 3) | (r5 >> 2)
        g3 = value & 7                        # green 6 bits: h[2:0] << 3 | l[7:5]
        high_g[value] = (g3 << 5) + (g3 >> 1)
        low_g[value] = (value >> 5) << 2
        b5 = value & 0x1F                     # low byte holds bits 0-4
        low_b[value] = (b5 << 3) | (b5 >> 2)
    return bytes(high_r), bytes(high_g), bytes(low_g), bytes(low_b)


_RGB565_R, _RGB565_GH, _RGB565_GL, _RGB565_B = _rgb565_planes()


def encode_device_info(cx=SCREEN_WIDTH, cy=SCREEN_HEIGHT, depth=COLOR_DEPTH,
                       ram=0x00800000, platform=0) -> bytes:
    """AEEDeviceInfo (AEEShell.h) - 64 bytes, field order is the ABI."""
    data = bytearray(64)
    struct.pack_into("<HHHHH H H", data, 0,
                     cx, cy,               # cxScreen, cyScreen
                     cx, cy,               # cxAltScreen, cyAltScreen
                     0x0B,                 # cxScrollBar
                     0x00FF,               # wEncoding (AEE_ENC_UNICODE)
                     1000)                 # wMenuTextScroll (ms)
    struct.pack_into("<H", data, 14, depth)            # nColorDepth
    struct.pack_into("<I", data, 16, 0)                # unused2
    struct.pack_into("<H", data, 20, 500)              # wMenuImageDelay (ms)
    struct.pack_into("<I", data, 24, ram)              # dwRAM
    struct.pack_into("<I", data, 28, (1 << 0))         # bAltDisplay etc. bitfield
    struct.pack_into("<I", data, 32, 0)                # dwPromptProps
    struct.pack_into("<HH", data, 36, 0x7F00, 0x7F00)  # wKeyCloseApp / AllApps
    struct.pack_into("<I", data, 40, 0x0409)           # dwLang (en-US)
    struct.pack_into("<H", data, 44, 64)               # wStructSize (>=48 -> full)
    struct.pack_into("<I", data, 48, 0xFFFFFFFF)       # dwNetLinger
    struct.pack_into("<I", data, 52, 0)                # dwSleepDefer
    struct.pack_into("<H", data, 56, 260)              # wMaxPath
    struct.pack_into("<I", data, 60, platform)         # dwPlatformId
    return bytes(data)


def encode_app_start(cls_app: int, display: int, width=SCREEN_WIDTH,
                     height=SCREEN_HEIGHT, args=0) -> bytes:
    """AEEAppStart { int error; AEECLSID clsApp; IDisplay*; AEERect; char* }."""
    return struct.pack("<IIIhhHHI", 0, cls_app, display, 0, 0, width, height, args)


class MethodStats:
    """Per-method call counters; the trace doubles as a TODO list."""

    def __init__(self):
        self.calls: dict[str, int] = {}
        self.logged: set[str] = set()

    def hit(self, key: str) -> int:
        count = self.calls.get(key, 0) + 1
        self.calls[key] = count
        return count


class BrewRuntime:
    """Owns every guest object (shell, displays, files, ...) for one boot."""

    def __init__(self, machine, log, helper, data_dir=None, mod_path=None, *,
                 width=SCREEN_WIDTH, height=SCREEN_HEIGHT, save_dir=None):
        if not (0 < width <= 0x7FFF and 0 < height <= 0x7FFF):
            raise ValueError("screen dimensions must be between 1 and 32767")
        self.m = machine
        self.log = log
        self.helper = helper
        self.data_dir = str(data_dir) if data_dir else None
        self.mod_path = str(mod_path) if mod_path else None
        self.width, self.height = width, height
        self.save_dir = os.path.abspath(save_dir) if save_dir else None
        self.stats = MethodStats()
        self.objects: dict[int, str] = {}          # guest address -> label
        self.roles: dict[str, int] = {}            # role -> guest address
        self.refcounts: dict[int, int] = {}
        self._object_vtables: dict[int, int] = {}
        self._object_slots: dict[int, list[int]] = {}
        self._object_cleanup: dict[int, object] = {}
        self._released_objects: set[int] = set()
        self.m.after_call.append(self._collect_released)
        self.applet = 0
        self.app_clsid = 0
        self.mif_path: str | None = None
        self._enumerated = False
        self.shell = self._make_shell()
        # BREW timers: keyed by (pfn, pUser); one-shot, re-armed by the guest.
        self.timers: dict[tuple[int, int], dict] = {}
        self.pending_events: list[tuple[int, int, int, int] | tuple[int, int, int, int, bool]] = []
        self.deferred: list[tuple[int, list]] = []
        self.colors: dict[int, int] = {}
        self.frames_presented = 0
        # Guest programme ticks: every guest timer callback that has fired.
        # The build is tick-locked (its 2 ms one-shot timer is the only clock
        # it has), so anything that wants the game's own notion of time - the
        # frontend's mouse look, for one - measures it in these.
        self.guest_ticks = 0
        self.on_tick = None
        self.input_clock = None
        # A tap must survive a keypad sample. The frontend can provide the
        # native key-query clock; other clients fall back to timer updates.
        self.key_down_ticks: dict[int, object] = {}
        self.key_down_timer_ticks: dict[int, int] = {}
        self.on_present = None
        self.start_time = time.monotonic()
        self.quit = False
        # IFileMgr / IFile state
        self.files: dict[int, dict] = {}           # IFile object -> handle state
        self.last_file_error = SUCCESS
        self.enum_entries: list[tuple[str, int, int]] = []
        self.mapped_files: dict[tuple[int, int, int], int] = {}
        # IMemAStream / IImage(PNG) state
        self.memstreams: dict[int, dict] = {}
        self.images: dict[int, dict] = {}
        self.image_bitmaps: dict[int, int] = {}    # IImage -> IBitmap for GETBITMAP
        # ISoundPlayer state
        self.sound_players: dict[int, dict] = {}
        # Host audio plus callbacks the runtime owes the guest later (sound
        # playback completion, ...).
        self.audio = AudioEngine(log, data_dir=self.data_dir)
        self.scheduled: list[dict] = []

    # ------------------------------------------------------------- object model
    @staticmethod
    def _arg_count(handler) -> int:
        """How many guest arguments a handler wants (all of r0-r3, then stack)."""
        parameters = list(inspect.signature(handler).parameters.values())
        if any(p.kind is inspect.Parameter.VAR_POSITIONAL for p in parameters):
            return 4
        total = 0
        for parameter in parameters[1:]:  # skip `machine`
            if parameter.kind in (inspect.Parameter.POSITIONAL_ONLY,
                                  inspect.Parameter.POSITIONAL_OR_KEYWORD,
                                  inspect.Parameter.KEYWORD_ONLY):
                total += 1
        return min(max(total, 0), 12)

    def make_object(self, label: str, iface: str, methods: dict | None = None,
                    extra_bytes: int = 0) -> int:
        """Allocate a guest object whose vtable implements ``iface``."""
        m = self.m
        method_names = IFACES[iface]
        vtable = m.alloc(4 * len(method_names))
        # object layout: [pvt][application fields...]
        address = m.alloc(4 + extra_bytes)
        m.w32(address, vtable)
        methods = dict(methods or {})
        if iface.startswith("I"):
            methods.setdefault("AddRef", self._addref_maker(address))
            methods.setdefault("Release", self._release_maker(address))
        slots = []
        for index, name in enumerate(method_names):
            handler = methods.get(name)
            if handler is None:
                handler = self._default_method(label, name)
            slot = m.bind(f"{label}::{name}", handler,
                          nargs=self._arg_count(handler))
            slots.append(slot)
            m.w32(vtable + 4 * index, slot)
        self._object_vtables[address] = vtable
        self._object_slots[address] = slots
        self.objects[address] = label
        self.refcounts[address] = 1
        self.log.debug(f"object {label} ({iface})", addr=hex(address),
                       vtbl=hex(vtable))
        return address

    def _addref_maker(self, address):
        def handler(machine, this):
            self.refcounts[address] = self.refcounts.get(address, 0) + 1
            return self.refcounts[address]
        return handler

    def _release_maker(self, address):
        def handler(machine, this):
            return self._release_object(address)
        return handler

    def _release_object(self, address):
        """Release a caller reference; runtime-owned singletons stay alive."""
        if address not in self.objects:
            return 0
        if self.refcounts.get(address) == 0:
            return 0
        pinned = address == self.shell or any(
            self.roles.get(role) == address for role in
            ("display", "device_bitmap", "graphics", "heap", "filemgr"))
        count = max(int(pinned), self.refcounts.get(address, 1) - 1)
        self.refcounts[address] = count
        if count == 0:
            # The native loader may close the same borrowed IFile twice in
            # one callback. Keep its interface valid until that callback
            # unwinds; reclaim it before the next host-to-guest call.
            if self.m._call_depth:
                self._released_objects.add(address)
            else:
                self._destroy_object(address)
        return count

    def _collect_released(self):
        released, self._released_objects = self._released_objects, set()
        for address in released:
            if self.refcounts.get(address) == 0:
                self._destroy_object(address)

    def _destroy_object(self, address):
        cleanup = self._object_cleanup.pop(address, None)
        if cleanup is not None:
            cleanup()
        # A destination is borrowed; remove it before its bitmap can be reused.
        for role, target in list(self.roles.items()):
            if target == address:
                self.roles.pop(role)
        for owner, bitmap in list(self.image_bitmaps.items()):
            if bitmap == address:
                self.image_bitmaps.pop(owner)
        self.objects.pop(address, None)
        self.refcounts.pop(address, None)
        for slot in self._object_slots.pop(address, []):
            self.m.traps.pop(slot, None)
        self.m.free(self._object_vtables.pop(address))
        self.m.free(address)

    def _default_method(self, label, name):
        key = f"{label}::{name}"

        def handler(machine, *args):
            count = self.stats.hit(key)
            if key not in self.stats.logged:
                self.stats.logged.add(key)
                self.log.debug(f"{key} -> stub(0)", nargs=len(args),
                               a0=hex(args[0]) if args else "-",
                               a1=hex(args[1]) if len(args) > 1 else "-")
            self.log.trace("stub", key, args=args[:4])
            return SUCCESS
        return handler

    def trace_call(self, name, args, ret=None):
        self.log.trace("service", name, args=args, ret=ret)

    # ------------------------------------------------------------------- shell
    def _make_shell(self) -> int:
        m = self.m
        methods = {
            "CreateInstance": self._shell_create_instance,
            "GetDeviceInfo": self._shell_get_device_info,
            "QueryClass": self._shell_query_class,
            "SetTimer": self._shell_set_timer,
            "CancelTimer": self._shell_cancel_timer,
            "GetTimerExpiration": self._shell_timer_expiration,
            "ActiveApplet": lambda machine, this: self.applet or self.app_clsid,
            "EnumAppletInit": lambda machine, this: None,
            "EnumNextApplet": self._shell_enum_next_applet,
            "StartApplet": lambda machine, this, cls, flags, args: SUCCESS,
            "CloseApplet": self._shell_close_applet,
            "CanStartApplet": lambda machine, this, cls: 1,
            "ForceExit": self._shell_force_exit,
            "SendEvent": self._shell_send_event,
            "LoadResData": self._shell_load_res_data,
            "LoadResDataEx": self._shell_load_res_data,
            "LoadResString": self._shell_load_res_string,
            "LoadResObject": self._shell_load_res_object,
            "FreeResData": lambda machine, this, pointer: SUCCESS,
            "IsValidResource": lambda machine, this, res, resid, rtype, cls: 0,
            "Beep": lambda machine, this, kind, loud: 1,
            "MessageBox": self._shell_message_box,
            "MessageBoxText": self._shell_message_box_text,
            "GetProperty": self._shell_get_property,
            "SetProperty": self._shell_set_property,
            "CheckPrivLevel": lambda machine, this, cls, query: 1,
            "Reset": lambda machine, this, rtype: SUCCESS,
            "GetHandler": lambda machine, this, cls, text: 0,
            "RegisterHandler": lambda machine, this, base, text, handler: SUCCESS,
            "RegisterNotify": lambda machine, this, cls, ctype, mask: SUCCESS,
            "Resume": lambda machine, this, callback: None,
            "AppIsInGroup": lambda machine, this, app, group: 0,
            "GetClassItemID": lambda machine, this, cls: 0,
            "GetItemStyle": lambda machine, this, t, normal, sel: None,
            "GetPrefs": lambda machine, this, cls, ver, cfg, size: EFAILED,
            "SetPrefs": lambda machine, this, cls, ver, cfg, size: SUCCESS,
            "Prompt": lambda machine, this, info: 0,
            "SetAlarm": lambda machine, this, cls, code, mins: SUCCESS,
            "CancelAlarm": lambda machine, this, cls, code: SUCCESS,
            "AlarmsActive": lambda machine, this: 0,
            "GetPosition": lambda machine, this, pres, pfn, user: EUNSUPPORTED,
            "DetectType": lambda machine, this, buf, size, name, mime: EUNSUPPORTED,
            "GetDeviceInfoEx": lambda machine, this, item, buf, size: EUNSUPPORTED,
            "CreateDialog": lambda machine, this, res, wid, info: EUNSUPPORTED,
            "GetActiveDialog": lambda machine, this: 0,
            "EndDialog": lambda machine, this: EUNSUPPORTED,
            "RegisterSystemCallback": lambda machine, this, cb, scb: None,
            "RegisterEvent": lambda machine, this, text, count: 0,
            "Notify": lambda machine, this, cls, mask, data: SUCCESS,
            "Obsolete": lambda machine, this: 0,
            "NotifyEnable": lambda machine, this, enabled: None,
        }
        return self.make_object("IShell", "IShell", methods, extra_bytes=32)

    def _ms(self) -> int:
        return int((time.monotonic() - self.start_time) * 1000) & 0xFFFFFFFF

    def _shell_create_instance(self, machine, this, cls, ppobj):
        self.trace_call("IShell::CreateInstance", (this, cls, ppobj))
        factory = {
            AEECLSID_DISPLAY: self._new_display,
            AEECLSID_GRAPHICS: self._new_graphics,
            AEECLSID_HEAP: self._new_heap,
            AEECLSID_FILEMGR: self._new_filemgr,
            AEECLSID_NOKIA_FILEMGR: self._new_filemgr,
            AEECLSID_MEMASTREAM: self._new_memstream,
            AEECLSID_PNG: self._new_png_viewer,
            AEECLSID_SOUNDPLAYER: self._new_sound_player,
        }.get(cls)
        if factory is not None:
            obj = factory()
            m = self.m
            m.w32(ppobj, obj)
            # Factories return an initial caller reference. Cached interfaces
            # instead retain their initial runtime reference.
            if obj in self.roles.values():
                self.refcounts[obj] += 1
            self.log.trace("service", "IShell::CreateInstance",
                           args=(this, cls, ppobj), ret=SUCCESS, cls=hex(cls))
            return SUCCESS
        self.stats.hit(f"CreateInstance:{cls:#010x}")
        self.log.warn("CreateInstance: unsupported class",
                      cls=hex(cls), at=hex(this))
        m = self.m
        if ppobj:
            m.w32(ppobj, 0)
        return ECLASSNOTSUPPORT

    def _shell_get_device_info(self, machine, this, pdi):
        self.trace_call("IShell::GetDeviceInfo", (this, pdi))
        if pdi:
            machine.write(pdi, encode_device_info(self.width, self.height))
        return None

    def _shell_query_class(self, machine, this, cls, pai):
        self.trace_call("IShell::QueryClass", (this, cls, pai))
        if cls == self.app_clsid and pai:
            # AEEAppInfo: cls, pszMIF, wIDBase, wAppType, wHostLo, wHostHi,
            #             wIDPrivSet, wFlags
            mif = machine.cstring(self.mif_path or "")
            machine.write(pai, struct.pack("<IIHHHHHH", cls, mif, 0, 0, 0, 0, 0, 0))
            return 1
        return 0

    def _shell_enum_next_applet(self, machine, this, pai):
        if self._enumerated:
            return 0
        self._enumerated = True
        if pai:
            mif = machine.cstring(self.mif_path or "")
            machine.write(pai, struct.pack(
                "<IIHHHHHH", self.app_clsid, mif, 0, 0, 0, 0, 0, 0))
        return self.app_clsid

    def _shell_set_timer(self, machine, this, msec, pfn, puser):
        """int IShell_SetTimer(IShell*, int32 dwMsecs, PFNTIMER pfn, void*).

        One-shot: "Timers do not repeat. The user must reset the timer if they
        desire a repeating timer."  Setting the same callback/data pair again
        overrides the pending timer instead of creating a second one.
        """
        if not pfn:
            return EBADPARM
        delay = max(0, msec) / 1000.0
        self.timers[(pfn, puser)] = {
            "pfn": pfn, "user": puser, "msecs": max(0, msec),
            "due": time.monotonic() + delay,
        }
        self.trace_call("IShell::SetTimer", (this, msec, pfn, puser),
                        ret=SUCCESS)
        return SUCCESS

    def _shell_cancel_timer(self, machine, this, pfn, puser):
        timer = self.timers.pop((pfn, puser), None)
        self.trace_call("IShell::CancelTimer", (this, pfn, puser),
                        ret=1 if timer else 0)
        return 1 if timer else 0

    def _shell_timer_expiration(self, machine, this, pfn, puser):
        timer = self.timers.get((pfn, puser))
        if not timer:
            return 0
        return max(0, int((timer["due"] - time.monotonic()) * 1000))

    def _shell_force_exit(self, machine, this):
        self.log.info("IShell::ForceExit: the game asked to quit")
        self.quit = True
        return 1

    def _shell_close_applet(self, machine, this, return_to_idle):
        self.log.info("IShell::CloseApplet", to_idle=return_to_idle)
        self.quit = True
        return SUCCESS

    def _shell_send_event(self, machine, this, flags, cls, evt, wparam, dwparam):
        self.trace_call("IShell::SendEvent", (this, flags, cls, evt, wparam, dwparam))
        # Events are queued for the pump; the target applet sees them later.
        self.pending_events = getattr(self, "pending_events", [])
        self.pending_events.append((cls, evt, wparam, dwparam))
        return 1

    def _shell_load_res_data(self, machine, this, res, resid, rtype, *rest):
        self.log.warn("IShell::LoadResData unimplemented", res=hex(res or 0),
                      resid=resid, rtype=rtype)
        return 0

    def _shell_load_res_string(self, machine, this, base, resid, buff, size):
        self.log.warn("IShell::LoadResString unimplemented", base=hex(base or 0),
                      resid=resid)
        if buff:
            machine.w16(buff, 0)
        return EFAILED

    def _shell_load_res_object(self, machine, this, res, resid, cls):
        self.log.warn("IShell::LoadResObject unimplemented", resid=resid,
                      cls=hex(cls))
        return 0

    def _shell_message_box(self, machine, this, res, title, text):
        self.log.info("[guest MessageBox]", res=hex(res or 0), title=title,
                      text=text)
        return 1

    def _shell_message_box_text(self, machine, this, title, text):
        self.log.info("[guest MessageBoxText]",
                      title=machine.string(title or 0) if title else "",
                      text=machine.string(text or 0) if text else "")
        return 1

    def _shell_get_property(self, machine, this, cls, prop, value):
        self.log.warn("IShell::GetProperty stub", cls=hex(cls), prop=hex(prop))
        return EUNSUPPORTED

    def _shell_set_property(self, machine, this, cls, prop, value):
        self.log.warn("IShell::SetProperty stub", cls=hex(cls), prop=hex(prop))
        return EUNSUPPORTED

    # ---------------------------------------------------------------- factories
    def _new_display(self) -> int:
        if "display" in self.roles:
            return self.roles["display"]
        methods = {
            "GetDeviceBitmap": self._display_get_device_bitmap,
            "SetDestination": self._display_set_destination,
            "GetDestination": self._display_get_destination,
            "Update": self._display_update,
            "Backlight": self._display_backlight,
            "SetColor": self._display_set_color,
        }
        obj = self.make_object("IDisplay", "IDisplay", methods, extra_bytes=32)
        self.roles["display"] = obj
        return obj

    def _display_get_device_bitmap(self, machine, this, ppbitmap):
        """int IDisplay_GetDeviceBitmap(IDisplay*, IBitmap **ppIBitmap)."""
        bitmap = self.roles.get("device_bitmap")
        if not bitmap:
            bitmap = self._new_bitmap(self.width, self.height, COLOR_DEPTH,
                                      label="IDisplay(device bitmap)")
            self.roles["device_bitmap"] = bitmap
            self.roles.setdefault("destination", bitmap)
        if ppbitmap:
            machine.w32(ppbitmap, bitmap)
        self.trace_call("IDisplay::GetDeviceBitmap", (this, ppbitmap), ret=bitmap)
        return SUCCESS

    def _display_set_destination(self, machine, this, bitmap):
        self.roles["destination"] = bitmap or self.roles.get("device_bitmap", 0)
        self.trace_call("IDisplay::SetDestination", (this, bitmap))
        return SUCCESS

    def _display_get_destination(self, machine, this):
        return self.roles.get("destination", 0)

    def _display_update(self, machine, this):
        # The game finished drawing a frame: hand it to the host for present.
        self.frames_presented += 1
        if self.on_present is not None:
            self.on_present(self)
        return SUCCESS

    def _display_backlight(self, machine, this, on):
        self.log.debug("IDisplay::Backlight", on=on)
        return None

    def _display_set_color(self, machine, this, item, rgb):
        old = self.colors.get(item, 0)
        self.colors[item] = rgb
        return old

    def _new_graphics(self) -> int:
        if "graphics" in self.roles:
            return self.roles["graphics"]
        obj = self.make_object("IGraphics", "IGraphics", {}, extra_bytes=16)
        self.roles["graphics"] = obj
        return obj

    def _new_heap(self) -> int:
        if "heap" in self.roles:
            return self.roles["heap"]
        methods = {
            "Malloc": lambda machine, this, size: self.helper.h_malloc(machine, size),
            "Realloc": lambda machine, this, pointer, size: self.helper.h_realloc(machine, pointer, size),
            "Free": lambda machine, this, pointer: self.helper.h_free(machine, pointer),
            "StrDup": self._heap_str_dup,
            "CheckAvail": lambda machine, this, size: 1,
            "GetMemStats": self._heap_get_mem_stats,
            "GetModuleMemStats": self._heap_get_module_stats,
        }
        obj = self.make_object("IHeap", "IHeap", methods, extra_bytes=16)
        self.roles["heap"] = obj
        return obj

    def _heap_str_dup(self, machine, this, pstr):
        if not pstr:
            return 0
        text = machine.read(pstr, 2)
        # AECHAR string: keep copying until the NUL code unit.
        chunks = [text]
        offset = 2
        while text != b"\0\0" and offset < 4096:
            text = machine.read(pstr + offset, 2)
            chunks.append(text)
            offset += 2
        blob = b"".join(chunks)
        pointer = machine.alloc(len(blob))
        machine.write(pointer, blob)
        return pointer

    def _heap_get_mem_stats(self, machine, this):
        """uint32 IHeap_GetMemStats(IHeap*) - bytes currently in use."""
        return sum(machine.allocations.values()) & 0xFFFFFFFF

    def _heap_get_module_stats(self, machine, this, cls, pmax, pcur):
        if pmax:
            machine.w32(pmax, 0x00800000)
        if pcur:
            machine.w32(pcur, self._heap_get_mem_stats(machine, this))
        return SUCCESS

    # ---------------------------------------------------------------- IFileMgr
    # BREW paths ("bio1.dat", "fs:/~/bio1.dat", "fs:/shared/x") resolve onto
    # the host through the user-supplied data directory; the module's own
    # directory is the fallback root.  Nothing is bundled: a missing file just
    # fails the way it would on a phone that never had it installed.
    def _path_roots(self) -> list[str]:
        roots = []
        if self.data_dir:
            roots.append(self.data_dir)
        if self.mod_path:
            roots.append(os.path.dirname(os.path.abspath(self.mod_path)))
        return roots

    def map_guest_path(self, name: str, *, writable=False) -> str:
        """Resolve a BREW path to a host path (existing file preferred)."""
        raw = (name or "").replace("\\", "/")
        for prefix in ("fs:/~/", "fs:/shared/", "fs:/sys/", "fs:/card0/",
                       "fs:/mod/", "fs:/mif/", "fs:/", "~/", "/"):
            if raw.startswith(prefix):
                raw = raw[len(prefix):]
                break
        parts = [p for p in raw.split("/") if p not in ("", ".", "..")]
        rel = os.path.join(*parts) if parts else ""
        tail = parts[-1] if parts else ""
        roots = self._path_roots()
        if self.save_dir:
            overlay = os.path.join(self.save_dir, rel or tail)
            if writable:
                return overlay
            roots.insert(0, self.save_dir)
        for root in roots:
            for candidate in (rel, tail):
                if candidate:
                    host = os.path.join(root, candidate)
                    if os.path.exists(host):
                        return host
        root = roots[0] if roots else "."
        return os.path.join(root, rel or tail)

    def _file_info_bytes(self, name: str, size: int, attrib: int = 0) -> bytes:
        """struct AEEFileInfo {char attrib; uint32 date; uint32 size; char[64]}."""
        return struct.pack("<B3xII64s", attrib & 0xFF, 0, size & 0xFFFFFFFF,
                           name.encode()[:63])

    def _new_filemgr(self) -> int:
        if "filemgr" in self.roles:
            return self.roles["filemgr"]
        methods = {
            "OpenFile": self._filemgr_open_file,
            "GetInfo": self._filemgr_get_info,
            "Remove": self._filemgr_remove,
            "MkDir": self._filemgr_mkdir,
            "RmDir": self._filemgr_rmdir,
            "Test": self._filemgr_test,
            "GetFreeSpace": self._filemgr_get_free_space,
            "GetLastError": lambda machine, this: self.last_file_error,
            "EnumInit": self._filemgr_enum_init,
            "EnumNext": self._filemgr_enum_next,
            "Rename": self._filemgr_rename,
            "EnumNextEx": self._filemgr_enum_next_ex,
            "SetDescription": lambda machine, this, name, desc: EUNSUPPORTED,
            "GetInfoEx": lambda machine, this, name, info: EUNSUPPORTED,
            "Use": lambda machine, this, name, use: SUCCESS,
            "GetFileUseInfo": lambda machine, this, info: EUNSUPPORTED,
            "ResolvePath": self._filemgr_resolve_path,
            "CheckPathAccess": self._filemgr_check_path_access,
            "GetFreeSpaceEx": self._filemgr_get_free_space_ex,
        }
        obj = self.make_object("IFileMgr", "IFileMgr", methods, extra_bytes=32)
        self.roles["filemgr"] = obj
        return obj

    def _filemgr_open_file(self, machine, this, name_ptr, mode):
        name = machine.string(name_ptr) if name_ptr else ""
        create = bool(mode & OFM_CREATE)
        append = bool(mode & OFM_APPEND)
        write = bool(mode & (OFM_READWRITE | OFM_APPEND | OFM_CREATE))
        host = self.map_guest_path(name)
        if not create and not os.path.exists(host):
            self.last_file_error = EFILENOEXISTS
            self.log.info("IFileMgr::OpenFile missing", name=name, host=host)
            return 0
        try:
            if write and self.save_dir:
                source, host = host, self.map_guest_path(name, writable=True)
                os.makedirs(os.path.dirname(host), exist_ok=True)
                if not create and not os.path.exists(host):
                    shutil.copy2(source, host)
            if create:
                if os.path.exists(self.map_guest_path(name)):
                    self.last_file_error = EFILEEXISTS
                    return 0
                os.makedirs(os.path.dirname(host), exist_ok=True)
                handle = open(host, "w+b")
            else:
                handle = open(host, "r+b" if write else "rb")
        except OSError as error:
            self.last_file_error = EFAILED
            self.log.warn(f"IFileMgr::OpenFile {name!r} failed: {error}")
            return 0
        if append:
            handle.seek(0, os.SEEK_END)
        self.last_file_error = SUCCESS
        obj = self._new_file(handle, name, host)
        self.log.info("IFileMgr::OpenFile", name=name, mode=hex(mode),
                      loaded_from=host)
        return obj

    def _filemgr_get_info(self, machine, this, name_ptr, info_ptr):
        name = machine.string(name_ptr) if name_ptr else ""
        host = self.map_guest_path(name)
        if not os.path.isfile(host):
            self.last_file_error = EFILENOEXISTS
            return EFAILED
        if info_ptr:
            machine.write(info_ptr, self._file_info_bytes(
                os.path.basename(name or host), os.path.getsize(host)))
        return SUCCESS

    def _filemgr_test(self, machine, this, name_ptr):
        name = machine.string(name_ptr) if name_ptr else ""
        host = self.map_guest_path(name)
        exists = os.path.exists(host)
        self.last_file_error = SUCCESS if exists else EFILENOEXISTS
        self.log.debug("IFileMgr::Test", name=name, exists=exists)
        return SUCCESS if exists else EFAILED

    def _filemgr_remove(self, machine, this, name_ptr):
        name = machine.string(name_ptr) if name_ptr else ""
        host = self.map_guest_path(name, writable=True)
        try:
            os.remove(host)
            self.last_file_error = SUCCESS
            return SUCCESS
        except OSError:
            self.last_file_error = EFILENOEXISTS
            return EFAILED

    def _filemgr_mkdir(self, machine, this, name_ptr):
        name = machine.string(name_ptr) if name_ptr else ""
        host = self.map_guest_path(name, writable=True)
        try:
            os.makedirs(host, exist_ok=True)
            return SUCCESS
        except OSError:
            return EFAILED

    def _filemgr_rmdir(self, machine, this, name_ptr):
        name = machine.string(name_ptr) if name_ptr else ""
        host = self.map_guest_path(name, writable=True)
        try:
            os.rmdir(host)
            return SUCCESS
        except OSError:
            return EFAILED

    def _filemgr_rename(self, machine, this, src_ptr, dst_ptr):
        src = self.map_guest_path(machine.string(src_ptr) if src_ptr else "",
                                  writable=True)
        dst = self.map_guest_path(machine.string(dst_ptr) if dst_ptr else "",
                                  writable=True)
        try:
            if self.save_dir:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
            os.rename(src, dst)
            return SUCCESS
        except OSError:
            return EFAILED

    def _filemgr_get_free_space(self, machine, this, pdw_total):
        if pdw_total:
            machine.w32(pdw_total, 0x10000000)
        return 0x0FFFFFFF

    def _filemgr_get_free_space_ex(self, machine, this, path_ptr, pdw_total,
                                   pdw_free):
        if pdw_total:
            machine.w32(pdw_total, 0x10000000)
        if pdw_free:
            machine.w32(pdw_free, 0x0FFFFFFF)
        return SUCCESS

    def _filemgr_enum_init(self, machine, this, dir_ptr, dirs_only):
        name = machine.string(dir_ptr) if dir_ptr else ""
        host = self.map_guest_path(name)
        entries: list[tuple[str, int, int]] = []
        try:
            for entry in sorted(os.listdir(host)):
                full = os.path.join(host, entry)
                is_dir = os.path.isdir(full)
                if bool(dirs_only) != is_dir:
                    continue
                attrib = FA_READONLY if not os.access(full, os.W_OK) else 0
                size = 0 if is_dir else os.path.getsize(full)
                entries.append((entry, size, attrib))
        except OSError:
            self.last_file_error = EFILENOEXISTS
            return EFAILED
        self.enum_entries = entries
        return SUCCESS

    def _filemgr_enum_next(self, machine, this, info_ptr):
        if not self.enum_entries:
            return 0
        name, size, attrib = self.enum_entries.pop(0)
        if info_ptr:
            machine.write(info_ptr, self._file_info_bytes(name, size, attrib))
        return 1

    def _filemgr_enum_next_ex(self, machine, this, info_ptr):
        return 0

    def _filemgr_resolve_path(self, machine, this, in_ptr, out_ptr, len_ptr):
        raw = machine.string(in_ptr) if in_ptr else ""
        resolved = raw if raw.startswith("fs:/") else "fs:/~/" + raw.lstrip("/")
        blob = resolved.encode() + b"\0"
        if not out_ptr:
            if len_ptr:
                machine.w32(len_ptr, len(blob))
            return SUCCESS
        capacity = machine.u32(len_ptr) if len_ptr else len(blob)
        if capacity < len(blob):
            if len_ptr:
                machine.w32(len_ptr, len(blob))
            return EBUFFERTOOSMALL
        machine.write(out_ptr, blob)
        if len_ptr:
            machine.w32(len_ptr, len(blob))
        return SUCCESS

    def _filemgr_check_path_access(self, machine, this, path_ptr, want, actual_ptr):
        if actual_ptr:
            machine.w32(actual_ptr, AEEFP_READ | AEEFP_WRITE | AEEFP_CREATE |
                        AEEFP_DELETE)
        return SUCCESS

    # ------------------------------------------------------------------- IFile
    def _new_file(self, handle, name: str, host_path: str) -> int:
        methods = {
            "Readable": lambda machine, this, pfn, user: None,
            "Read": self._file_read,
            "Cancel": lambda machine, this, pfn, user: None,
            "Write": self._file_write,
            "GetInfo": self._file_get_info,
            "Seek": self._file_seek,
            "Truncate": self._file_truncate,
            "GetInfoEx": lambda machine, this, info: EUNSUPPORTED,
            "SetCacheSize": lambda machine, this, size: size,
            "Map": self._file_map,
            "Release": self._file_release,
        }
        obj = self.make_object("IFile", "IFile", methods, extra_bytes=16)
        self.files[obj] = {"handle": handle, "name": name, "host": host_path}
        self._object_cleanup[obj] = lambda: self._close_file(obj)
        return obj

    def _file_read(self, machine, this, buffer, count):
        state = self.files.get(this)
        if state is None or not count:
            return 0
        data = state["handle"].read(count)
        if data:
            machine.write(buffer, data)
        return len(data)

    def _file_write(self, machine, this, buffer, count):
        state = self.files.get(this)
        if state is None or not count:
            return 0
        state["handle"].write(machine.read(buffer, count))
        return count

    def _file_get_info(self, machine, this, info_ptr):
        state = self.files.get(this)
        if state is None:
            return EFAILED
        handle = state["handle"]
        size = os.fstat(handle.fileno()).st_size
        if info_ptr:
            machine.write(info_ptr, self._file_info_bytes(
                os.path.basename(state["name"] or state["host"]), size))
        return SUCCESS

    def _file_seek(self, machine, this, seek_type, position):
        state = self.files.get(this)
        if state is None:
            return EFAILED
        handle = state["handle"]
        if position >= 0x80000000:
            position -= 0x100000000
        if seek_type == SEEK_CURRENT and position == 0:
            return handle.tell() & 0xFFFFFFFF
        whence = {SEEK_START: os.SEEK_SET, SEEK_END: os.SEEK_END,
                  SEEK_CURRENT: os.SEEK_CUR}.get(seek_type)
        if whence is None:
            self.last_file_error = EBADSEEKPOS
            return EFAILED
        try:
            handle.seek(position, whence)
        except (OSError, ValueError):
            self.last_file_error = EBADSEEKPOS
            return EFAILED
        return SUCCESS

    def _file_truncate(self, machine, this, position):
        state = self.files.get(this)
        if state is None:
            return EFAILED
        state["handle"].truncate(position)
        return SUCCESS

    def _file_map(self, machine, this, start, size, protections, flags, offset):
        """IFILE_Map for const-file style access: copy-on-map into guest RAM."""
        state = self.files.get(this)
        if state is None or not size:
            return 0
        key = (this, offset, size)
        cached = self.mapped_files.get(key)
        if cached:
            return cached
        handle = state["handle"]
        handle.seek(offset)
        data = handle.read(size)
        pointer = machine.alloc(size)
        machine.write(pointer, data)
        self.mapped_files[key] = pointer
        self.log.debug("IFile::Map", size=size, offset=offset, at=hex(pointer))
        return pointer

    def _file_release(self, machine, this):
        return self._release_object(this)

    def _close_file(self, this):
        state = self.files.pop(this, None)
        if state is not None:
            try:
                state["handle"].close()
            except OSError:
                pass
        for key, pointer in list(self.mapped_files.items()):
            if key[0] == this:
                self.mapped_files.pop(key)
                self.m.free(pointer)

    # ----------------------------------------------------------------- bitmaps
    # Bitmap objects follow `struct IDIB` (AEEIDIB.h) field for field, so the
    # engine can QueryInterface(IBitmap, AEEIID_IDIB) and then read pBmp/cx/cy/
    # nPitch/nDepth itself - that is how this software renderer gets at pixels.
    def _new_bitmap(self, width=0, height=0, depth=COLOR_DEPTH,
                    label="IBitmap") -> int:
        m = self.m
        pitch = (((width * depth) + 15) // 16) * 2  # 16-bit aligned rows
        pixels = m.alloc(pitch * height) if width and height else 0
        methods = {
            "QueryInterface": self._bitmap_query_interface,
            "RGBToNative": self._bitmap_rgb_to_native,
            "NativeToRGB": self._bitmap_native_to_rgb,
            "DrawPixel": self._bitmap_draw_pixel,
            "GetPixel": self._bitmap_get_pixel,
            "SetPixels": self._bitmap_set_pixels,
            "DrawHScanline": self._bitmap_draw_hscanline,
            "FillRect": self._bitmap_fill_rect,
            "GetInfo": self._bitmap_get_info,
            "CreateCompatibleBitmap": self._bitmap_create_compatible,
            "SetTransparencyColor": self._bitmap_set_transparency,
        }
        obj = self.make_object(label, "IBitmap", methods, extra_bytes=IDIB_SIZE)
        self._object_cleanup[obj] = lambda: m.free(pixels)
        m.w32(obj + IDIB_PBMP, pixels)
        m.w16(obj + IDIB_CX, width)
        m.w16(obj + IDIB_CY, height)
        m.w16(obj + IDIB_NPITCH, pitch)
        m.write(obj + IDIB_NDEPTH, bytes((depth, IDIB_COLORSCHEME_565)))
        self.log.debug("bitmap created", w=width, h=height, depth=depth,
                       pitch=pitch, pixels=hex(pixels))
        return obj

    # ----------------------------------------------------------- bitmap fields
    # The fields the drawing paths need (pixels, size, pitch, depth) sit in one
    # 21-byte window of the IDIB header; read them in a single access.
    _IDIB_GEOMETRY = struct.Struct("<I8xHHH2xB")

    def bitmap_geometry(self, this) -> tuple[int, int, int, int, int]:
        """(width, height, pitch, depth, pixels) of a guest bitmap object."""
        pixels, width, height, pitch, depth = self.m.unpack(
            self._IDIB_GEOMETRY, this + IDIB_PBMP)
        if pitch & 0x8000:
            pitch -= 0x10000
        return width, height, pitch, depth, pixels

    def _bitmap_query_interface(self, machine, this, cls, ppo):
        if cls in (AEECLSID_BITMAP, AEEIID_IDIB, AEEIID_DIB_20):
            if ppo:
                machine.w32(ppo, this)
            self.refcounts[this] = self.refcounts.get(this, 0) + 1
            return SUCCESS
        if ppo:
            machine.w32(ppo, 0)
        self.log.debug("IBitmap::QueryInterface unknown class", cls=hex(cls))
        return ECLASSNOTSUPPORT

    def _bitmap_get_info(self, machine, this, pinfo, size):
        """int IBitmap_GetInfo(IBitmap*, AEEBitmapInfo *pinfo, int nSize)."""
        width, height, _pitch, depth, _pixels = self.bitmap_geometry(this)
        if pinfo and size >= 12:
            machine.w32(pinfo + 0, width)
            machine.w32(pinfo + 4, height)
            machine.w32(pinfo + 8, depth)
        return SUCCESS

    def _bitmap_create_compatible(self, machine, this, ppbitmap, width, height):
        _cx, _cy, _pitch, depth, _pixels = self.bitmap_geometry(this)
        bitmap = self._new_bitmap(width, height, depth or COLOR_DEPTH,
                                  label="IBitmap(compatible)")
        if ppbitmap:
            machine.w32(ppbitmap, bitmap)
        return SUCCESS

    def _bitmap_set_transparency(self, machine, this, color):
        machine.w32(this + IDIB_NC_TRANSPARENT, color & 0xFFFFFFFF)
        return SUCCESS

    def _bitmap_rgb_to_native(self, machine, this, rgb):
        _cx, _cy, _pitch, depth, _pixels = self.bitmap_geometry(this)
        r = (rgb >> 16) & 0xFF
        g = (rgb >> 8) & 0xFF
        b = rgb & 0xFF
        if depth == 16:
            return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
        if depth == 24 or depth == 32:
            return rgb & 0xFFFFFF
        return rgb

    def _bitmap_native_to_rgb(self, machine, this, color):
        _cx, _cy, _pitch, depth, _pixels = self.bitmap_geometry(this)
        if depth == 16:
            r = (color >> 11) & 0x1F
            g = (color >> 5) & 0x3F
            b = color & 0x1F
            return ((r << 3) | (r >> 2)) << 16 | ((g << 2) | (g >> 4)) << 8 | ((b << 3) | (b >> 2))
        return color & 0xFFFFFF

    def _bitmap_pixels_at(self, machine, this, x, y, depth):
        width, height, pitch, _d, pixels = self.bitmap_geometry(this)
        if not pixels or x < 0 or y < 0 or x >= width or y >= height:
            return 0
        if depth == 16:
            return pixels + y * pitch + x * 2
        if depth == 32:
            return pixels + y * pitch + x * 4
        if depth == 8:
            return pixels + y * pitch + x
        return 0

    def _bitmap_draw_pixel(self, machine, this, x, y, color, rop):
        _cx, _cy, _pitch, depth, _pixels = self.bitmap_geometry(this)
        offset = self._bitmap_pixels_at(machine, this, x, y, depth)
        if offset:
            if depth == 16:
                machine.w16(offset, color & 0xFFFF)
            elif depth == 32:
                machine.w32(offset, color)
            elif depth == 8:
                machine.write(offset, bytes((color & 0xFF,)))
        return SUCCESS

    def _bitmap_get_pixel(self, machine, this, x, y, pcolor):
        _cx, _cy, _pitch, depth, _pixels = self.bitmap_geometry(this)
        offset = self._bitmap_pixels_at(machine, this, x, y, depth)
        if offset:
            if depth == 16:
                value = machine.u16(offset)
            elif depth == 32:
                value = machine.u32(offset)
            else:
                value = machine.read(offset, 1)[0]
            if pcolor:
                machine.w32(pcolor, value)
            return SUCCESS
        return EFAILED

    def _bitmap_set_pixels(self, machine, this, count, points, color, rop):
        _cx, _cy, _pitch, depth, _pixels = self.bitmap_geometry(this)
        for index in range(min(count, 4096)):
            x = machine.u16(points + index * 4)
            y = machine.u16(points + index * 4 + 2)
            if x & 0x8000:
                x -= 0x10000
            if y & 0x8000:
                y -= 0x10000
            self._bitmap_draw_pixel(machine, this, x, y, color, rop)
        return SUCCESS

    def _bitmap_span_bytes(self, count: int, color: int, depth: int) -> bytes:
        """A run of `count` pixels of one solid color, native depth."""
        if count <= 0:
            return b""
        if depth == 32:
            return struct.pack("<I", color & 0xFFFFFFFF) * count
        if depth == 16:
            return struct.pack("<H", color & 0xFFFF) * count
        return bytes((color & 0xFF,)) * count

    def _bitmap_fill_span(self, machine, this, x, y, count, color) -> None:
        """Fill `count` pixels from (x, y) with one write (clipped)."""
        width, height, pitch, depth, pixels = self.bitmap_geometry(this)
        if not pixels or depth not in (8, 16, 32):
            return
        if y < 0 or y >= height or count <= 0:
            return
        x0, x1 = max(0, x), min(width, x + count)
        if x0 >= x1:
            return
        step = depth // 8
        machine.write(pixels + y * pitch + x0 * step,
                      self._bitmap_span_bytes(x1 - x0, color, depth))

    def _bitmap_draw_hscanline(self, machine, this, y, xmin, xmax, color, rop):
        self._bitmap_fill_span(machine, this, xmin, y, xmax - xmin + 1, color)
        return SUCCESS

    def _bitmap_fill_rect(self, machine, this, prect, color, rop):
        """int IBitmap_FillRect(IBitmap*, const AEERect *prc, NativeColor, rop)."""
        if not prect:
            return SUCCESS
        x, y, width, height = machine.unpack(_AEERECT, prect)
        if x & 0x8000:
            x -= 0x10000
        if y & 0x8000:
            y -= 0x10000
        if width <= 0 or height <= 0:
            return SUCCESS
        bmp_width, bmp_height, pitch, depth, pixels = self.bitmap_geometry(this)
        if not pixels or depth not in (8, 16, 32) or y >= bmp_height or x >= bmp_width:
            return SUCCESS
        x0, x1 = max(0, x), min(bmp_width, x + width)
        if x0 >= x1:
            return SUCCESS
        y0, y1 = max(0, y), min(bmp_height, y + height)
        step = depth // 8
        span = self._bitmap_span_bytes(x1 - x0, color, depth)
        if x0 == 0 and x1 == bmp_width and pitch == bmp_width * step:
            # Whole-width rows are contiguous: one write for the whole rect.
            machine.write(pixels + y0 * pitch, span * (y1 - y0))
            return SUCCESS
        for cy in range(y0, y1):
            machine.write(pixels + cy * pitch + x0 * step, span)
        return SUCCESS

    # -------------------------------------------------------------- IMemAStream
    # AEECLSID_MEMASTREAM: a chunk of memory read through the IAStream
    # interface.  The stream takes ownership of the buffer: it is freed when
    # the object is released or replaced by another Set() (AEE.h documents
    # this contract), hence the custom Release.
    def _new_memstream(self) -> int:
        methods = {
            "Readable": self._memstream_readable,
            "Read": self._memstream_read,
            "Cancel": self._memstream_cancel,
            "Set": self._memstream_set,
            "SetEx": self._memstream_set_ex,
            "Release": self._memstream_release,
        }
        obj = self.make_object("IMemAStream", "IMemAStream", methods,
                               extra_bytes=40)
        self.memstreams[obj] = {
            "buffer": 0, "size": 0, "offset": 0, "position": 0,
            "sysmem": False, "notify": 0, "notify_user": 0,
            "free_notify": 0, "free_user": 0,
        }
        def cleanup():
            self._memstream_release_data(self.m, obj)
            self.memstreams.pop(obj, None)
        self._object_cleanup[obj] = cleanup
        return obj

    def _memstream_state(self, this) -> dict:
        state = self.memstreams.get(this)
        if state is None:
            state = self.memstreams.setdefault(this, {
                "buffer": 0, "size": 0, "offset": 0, "position": 0,
                "sysmem": False, "notify": 0, "notify_user": 0,
                "free_notify": 0, "free_user": 0})
        return state

    def _memstream_release_data(self, machine, this) -> None:
        state = self._memstream_state(this)
        if state["buffer"]:
            pointer, skip = state["buffer"], False
            state["buffer"] = 0
            if state["free_notify"]:
                skip = True
                try:
                    machine.call(state["free_notify"], [state["free_user"]],
                                 timeout=5.0)
                except Exception as error:  # noqa: BLE001
                    self.log.warn(f"IMemAStream free callback failed: {error}")
            if not skip and pointer in machine.allocations:
                machine.free(pointer)

    def _memstream_release(self, machine, this):
        return self._release_object(this)

    def _memstream_set(self, machine, this, pbuf, size, offset, sysmem):
        """void IMEMASTREAM_Set(po, byte*, uint32, uint32, boolean)."""
        state = self._memstream_state(this)
        self._memstream_release_data(machine, this)
        state.update(buffer=pbuf, size=size & 0xFFFFFFFF, offset=offset & 0xFFFFFFFF,
                     position=min(offset, size) & 0xFFFFFFFF,
                     sysmem=bool(sysmem), free_notify=0, free_user=0)
        self.trace_call("IMemAStream::Set", (this, pbuf, size, offset, sysmem))
        return None

    def _memstream_set_ex(self, machine, this, pbuf, size, offset, free_cb,
                          free_user):
        state = self._memstream_state(this)
        self._memstream_release_data(machine, this)
        state.update(buffer=pbuf, size=size & 0xFFFFFFFF, offset=offset & 0xFFFFFFFF,
                     position=min(offset, size) & 0xFFFFFFFF, sysmem=False,
                     free_notify=free_cb, free_user=free_user)
        self.trace_call("IMemAStream::SetEx",
                        (this, pbuf, size, offset, free_cb, free_user))
        return None

    def _memstream_read(self, machine, this, pdest, nwant):
        state = self._memstream_state(this)
        available = state["size"] - state["position"]
        want = nwant & 0x7FFFFFFF
        if available <= 0 or want == 0:
            return 0
        take = min(available, want)
        machine.write(pdest, machine.read(state["buffer"] + state["position"], take))
        state["position"] += take
        self.trace_call("IMemAStream::Read", (this, pdest, nwant), ret=take)
        return take

    def _memstream_readable(self, machine, this, pfn, puser):
        state = self._memstream_state(this)
        state["notify"] = pfn
        state["notify_user"] = puser
        self.trace_call("IMemAStream::Readable", (this, pfn, puser))
        # A memory stream is readable right now; deliver the notification from
        # the pump so the guest is not re-entered from inside this call.
        if pfn and state["position"] < state["size"]:
            self.deferred.append((pfn, [puser]))
        return None

    def _memstream_cancel(self, machine, this, pfn, puser):
        state = self._memstream_state(this)
        if not pfn or state["notify"] == pfn:
            state["notify"] = 0
            state["notify_user"] = 0
        self.trace_call("IMemAStream::Cancel", (this, pfn, puser))
        return None

    def read_stream_all(self, machine, stream, limit: int = 1 << 22) -> bytes:
        """Drain an IAStream/IFile through its Read method."""
        data = bytearray()
        if not stream:
            return bytes(data)
        vtable = machine.u32(stream)
        read_fn = machine.u32(vtable + 3 * 4)  # IBase + Readable + Read
        buffer = machine.alloc(8192)
        try:
            while len(data) < limit:
                want = min(8192, limit - len(data))
                got = machine.call(read_fn, [stream, buffer, want], timeout=10.0)
                if got in (0, AEE_STREAM_WOULDBLOCK) or got >= 0x80000000:
                    break
                data += machine.read(buffer, min(got, want))
        finally:
            machine.free(buffer)
        return bytes(data)

    # ------------------------------------------------------------ IImage (PNG)
    # AEECLSID_PNG is the legacy IImage "viewer" class: the app sets a stream
    # and the viewer decodes the image, reports its geometry through GetInfo
    # and blits it with Draw/DrawFrame.
    def _new_png_viewer(self) -> int:
        methods = {
            "Draw": self._image_draw,
            "DrawFrame": self._image_draw_frame,
            "GetInfo": self._image_get_info,
            "SetParm": self._image_set_parm,
            "Start": self._image_start,
            "Stop": self._image_stop,
            "SetStream": self._image_set_stream,
            "HandleEvent": self._image_handle_event,
            "Notify": self._image_notify,
        }
        obj = self.make_object("IImage(PNG)", "IImage", methods, extra_bytes=48)
        self.images[obj] = self._image_defaults()
        def cleanup():
            self.images.pop(obj, None)
            bitmap = self.image_bitmaps.pop(obj, 0)
            if bitmap:
                self._release_object(bitmap)
        self._object_cleanup[obj] = cleanup
        return obj

    @staticmethod
    def _image_defaults() -> dict:
        return {
            "stream": 0, "data": b"", "image": None, "display": 0,
            "offscreen": 0, "rop": None, "scale": (0, 0), "size": (0, 0),
            "offset": (0, 0), "rate": 0, "cx_frame": 0, "nframes": 0,
            "notify": 0, "user": 0,
        }

    def _image_state(self, this) -> dict:
        return self.images.setdefault(this, self._image_defaults())

    def _image_set_stream(self, machine, this, stream):
        from . import png

        state = self._image_state(this)
        state["stream"] = stream
        data = self.read_stream_all(machine, stream)
        state["data"] = data
        try:
            state["image"] = png.decode(data)
        except (png.PngError, ValueError) as error:
            state["image"] = None
            self.log.warn("IImage(PNG): decode failed", error=str(error),
                          size=len(data), head=data[:8].hex())
            return None
        image = state["image"]
        # Default blend mode follows the image's transparency (AEERasterOp.h).
        if state["rop"] is None:
            state["rop"] = RO_TRANSPARENT if image.has_alpha else RO_COPY
        self.trace_call("IImage(PNG)::SetStream", (this, stream),
                        ret=image.width * 65536 + image.height)
        state["notified"] = False
        if state["notify"]:
            self._image_notify_guest(machine, this)
        return None

    def _image_get_info(self, machine, this, pinfo):
        state = self._image_state(this)
        image = state["image"]
        if not pinfo:
            return None
        if image is None:
            machine.write(pinfo, bytes(12))
            return None
        colors = len(image.palette) if image.palette else 0
        frame = state["cx_frame"] or image.width
        # AEEImageInfo: {uint16 cx; uint16 cy; uint16 nColors; boolean bAnimated;
        # uint16 cxFrame} - one pad byte after bAnimated for the 2-byte align.
        machine.write(pinfo, struct.pack(
            "<HHHBxH", image.width & 0xFFFF, image.height & 0xFFFF,
            min(colors, 0xFFFF) & 0xFFFF, 0, frame & 0xFFFF))
        self.trace_call("IImage(PNG)::GetInfo", (this, pinfo),
                        ret=image.width * 65536 + image.height)
        return None

    def _image_set_parm(self, machine, this, nparm, p1, p2):
        state = self._image_state(this)
        if nparm == IPARM_DISPLAY:
            state["display"] = p1
        elif nparm == IPARM_OFFSCREEN:
            state["offscreen"] = p1
        elif nparm == IPARM_ROP:
            state["rop"] = p1
        elif nparm == IPARM_SCALE:
            state["scale"] = (p1, p2)
        elif nparm == IPARM_SIZE:
            state["size"] = (p1, p2)
        elif nparm == IPARM_OFFSET:
            state["offset"] = (p1, p2)
        elif nparm == IPARM_CXFRAME:
            state["cx_frame"] = p1
        elif nparm == IPARM_NFRAMES:
            state["nframes"] = p1
        elif nparm == IPARM_RATE:
            state["rate"] = p1
        elif nparm == IPARM_GETROP:
            if p1:
                machine.w32(p1, state["rop"] if state["rop"] is not None else RO_COPY)
            if p2:
                machine.w32(p2, SUCCESS)
        elif nparm == IPARM_GETBITMAP:
            bitmap = self._image_bitmap(machine, this)
            if p1:
                machine.w32(p1, bitmap)
            if p2:
                machine.w32(p2, SUCCESS if bitmap else EFAILED)
        self.trace_call("IImage(PNG)::SetParm", (this, nparm, p1, p2))
        return None

    def _image_start(self, machine, this, x, y):
        state = self._image_state(this)
        state["offset"] = (x, y)
        return None

    def _image_stop(self, machine, this):
        return None

    def _image_handle_event(self, machine, this, evt, wparam, dwparam):
        return 0

    def _image_notify(self, machine, this, pfn, puser):
        state = self._image_state(this)
        state["notify"] = pfn
        state["user"] = puser
        if pfn and state["image"] is not None:
            self._image_notify_guest(machine, this)
        return None

    def _image_notify_guest(self, machine, this) -> None:
        """PFNIMAGEINFO(void *pUser, IImage *po, AEEImageInfo *pi, int nErr)."""
        state = self.images.get(this)
        if not state or not state.get("notify") or not state.get("image"):
            return
        pfn, user = state["notify"], state["user"]
        info = machine.alloc(16)
        self._image_get_info(machine, this, info)
        try:
            machine.call(pfn, [user, this, info, 0], timeout=10.0)
        except Exception as error:  # noqa: BLE001
            self.log.warn(f"IImage notify callback failed: {error}")
        finally:
            machine.free(info)

    def _image_bitmap(self, machine, this) -> int:
        """An IBitmap holding the decoded pixels (IPARM_GETBITMAP)."""
        cached = self.image_bitmaps.get(this)
        if cached:
            return cached
        state = self._image_state(this)
        image = state["image"]
        if image is None:
            return 0
        bitmap = self._new_bitmap(image.width, image.height, COLOR_DEPTH,
                                  label="IImage(PNG) bitmap")
        self._blit_rgba(machine, bitmap, 0, 0, image, RO_COPY, (0, 0))
        self.image_bitmaps[this] = bitmap
        return bitmap

    def _image_draw(self, machine, this, x, y):
        self._image_blit(machine, this, x, y)
        return None

    def _image_draw_frame(self, machine, this, frame, x, y):
        self._image_blit(machine, this, x, y, frame=frame)
        return None

    def _image_blit(self, machine, this, x, y, frame=0):
        """Blit the source rect (IPARM_OFFSET + IPARM_SIZE) to (x, y).

        AEEIImage.h: IPARM_OFFSET selects where inside the loaded image the
        drawing starts, IPARM_SIZE the size of that region and IPARM_SCALE the
        destination size (IPARM_SIZE defaults to the whole image).
        """
        state = self._image_state(this)
        image = state["image"]
        if image is None:
            return
        target = state["offscreen"] or self.roles.get("destination") or \
            self.roles.get("device_bitmap", 0)
        if not target:
            return
        offset_x, offset_y = state["offset"]
        size_x, size_y = state["size"]
        src_w = size_x if size_x > 0 else image.width
        src_h = size_y if size_y > 0 else image.height
        if frame and state["cx_frame"]:
            offset_x += frame * state["cx_frame"]
            src_w = min(src_w, state["cx_frame"])
        scale = state["scale"]
        out_w, out_h = scale if (scale[0] > 0 and scale[1] > 0) else (src_w, src_h)
        rop = state["rop"] if state["rop"] is not None else (
            RO_TRANSPARENT if image.has_alpha else RO_COPY)
        self._blit_rgba(machine, target, x, y, image, rop, (out_w, out_h),
                        src=(offset_x, offset_y, src_w, src_h))
        self.trace_call("IImage(PNG)::DrawFrame" if frame else "IImage(PNG)::Draw",
                        (this, frame, x, y) if frame else (this, x, y),
                        ret=src_w * 65536 + src_h)

    @staticmethod
    def _rgba_to_native(rgba, depth: int) -> int:
        r, g, b = rgba
        if depth == 32:
            return 0xFF000000 | (r << 16) | (g << 8) | b
        if depth == 24:
            return (r << 16) | (g << 8) | b
        if depth == 8:
            return ((r >> 5) << 5) | ((g >> 5) << 2) | (b >> 6)
        return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)  # RGB565

    # Native-pixel tables are memoised per image.  A screen can blit hundreds
    # of distinct sub-rects of one sheet, and the tables are usually small (a
    # 32x32 RGB565 cell is 2 KiB), so the budget is on payload bytes plus a
    # generous entry count instead of a small entry count; clearing the whole
    # cache keeps the code simple and happens rarely.
    NATIVE_CACHE_BYTES = 4 * 1024 * 1024
    NATIVE_CACHE_ENTRIES = 4096

    def _native_runs(self, image, depth, key, sx0, sy0, src_w, src_h, holes
                     ) -> list:
        """Per-row [(start, native pixels)] for one source rect of an image.

        ``holes`` follows the ROP_TRANSPARENT convention: alpha-0 pixels are
        absent from the table (the destination keeps whatever is there), so a
        row can hold several runs.  Without it every pixel is content and each
        row holds exactly one run.  Results are memoised on the image: the
        game blits the same PNG art over and over, and translating every pixel
        is what makes those calls expensive once the destination write itself
        is a single memmove.
        """
        cache = image.native_cache
        table = cache.get(key)
        if table is not None:
            return table
        rgba = image.rgba
        image_width = image.width
        table = []
        entry_bytes = 0
        for sy in range(sy0, sy0 + src_h):
            base = (sy * image_width + sx0) * 4
            runs = []
            buf = bytearray()
            start = 0
            for i in range(src_w):
                oi = base + (i << 2)
                if holes and rgba[oi + 3] == 0:
                    if buf:
                        runs.append((start, bytes(buf)))
                        entry_bytes += len(buf)
                        buf = bytearray()
                    start = i + 1
                    continue
                r = rgba[oi]
                g = rgba[oi + 1]
                b = rgba[oi + 2]
                if not buf:
                    start = i
                if depth == 16:
                    value = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
                    buf.append(value & 0xFF)
                    buf.append(value >> 8)
                elif depth == 32:
                    buf.append(b)
                    buf.append(g)
                    buf.append(r)
                    buf.append(0xFF)
                else:
                    buf.append(((r >> 5) << 5) | ((g >> 5) << 2) | (b >> 6))
            if buf:
                runs.append((start, bytes(buf)))
                entry_bytes += len(buf)
            table.append(runs)
        if (len(cache) >= self.NATIVE_CACHE_ENTRIES
                or image.native_bytes + entry_bytes > self.NATIVE_CACHE_BYTES):
            cache.clear()
            image.native_bytes = 0
        cache[key] = table
        image.native_bytes += entry_bytes
        return table

    def _blit_rgba(self, machine, bitmap, x, y, image, rop, scale,
                   src=None) -> None:
        """Blit a decoded RGBA image (or a sub-rect of it) into a guest bitmap.

        ``src`` is (x, y, width, height) inside the image, defaulting to the
        whole image; ``scale`` is the destination size (0, 0 = 1:1).
        Transparent (alpha == 0) source pixels leave the destination alone, so
        only runs of opaque pixels are written back - never the gaps.
        Unscaled COPY/TRANSPARENT blits go through the memoised native table
        above; only scaled and BLEND (destination-dependent) blits convert
        pixels here.
        """
        width, height, pitch, depth, pixels = self.bitmap_geometry(bitmap)
        if not pixels or width <= 0 or height <= 0 or depth not in (8, 16, 32):
            return
        bytes_per = depth // 8
        if src is None:
            sx0, sy0, src_w, src_h = 0, 0, image.width, image.height
        else:
            sx0, sy0, src_w, src_h = (int(v) for v in src)
        sx0 = max(0, min(sx0, image.width))
        sy0 = max(0, min(sy0, image.height))
        src_w = max(1, min(src_w, image.width - sx0))
        src_h = max(1, min(src_h, image.height - sy0))
        if scale and scale[0] > 0 and scale[1] > 0:
            out_w, out_h = int(scale[0]), int(scale[1])
        else:
            out_w, out_h = src_w, src_h
        rgba = image.rgba
        x0, x1 = max(0, x), min(width, x + out_w)
        y0, y1 = max(0, y), min(height, y + out_h)
        if x1 <= x0 or y1 <= y0:
            return
        if rop in (RO_COPY, RO_TRANSPARENT) and out_w == src_w and out_h == src_h:
            holes = rop == RO_TRANSPARENT
            table = self._native_runs(image, depth, (rop, depth, sx0, sy0,
                                                     src_w, src_h),
                                      sx0, sy0, src_w, src_h, holes)
            dst_base = pixels + y0 * pitch
            rows = table[y0 - y:y1 - y]
            if not holes and x == 0 and out_w == width \
                    and x0 == x and x1 == x + out_w \
                    and pitch == width * bytes_per:
                # Full-width rows with no padding: the rect is one byte run.
                machine.write(dst_base, b"".join(run[0][1] for run in rows))
                return
            left = x0 - x
            right = x1 - x
            dy = y0
            for runs in rows:
                dst = dst_base + (dy - y0) * pitch
                if not holes and left == 0 and right == out_w:
                    machine.write(dst + x * bytes_per, runs[0][1])
                else:
                    for start, data in runs:
                        high = start + len(data) // bytes_per
                        if high <= left:
                            continue
                        if start >= right:
                            break
                        if start < left:
                            data = data[(left - start) * bytes_per:]
                            start = left
                        if high > right:
                            data = data[:(right - start) * bytes_per]
                        machine.write(dst + (x + start) * bytes_per, data)
                dy += 1
            return
        for dy in range(y0, y1):
            src_y = sy0 + (dy - y) * src_h // out_h
            row_base = src_y * image.width * 4
            run_start = None
            run = bytearray()
            for dx in range(x0, x1):
                src_x = sx0 + (dx - x) * src_w // out_w
                o = row_base + src_x * 4
                r, g, b, a = rgba[o], rgba[o + 1], rgba[o + 2], rgba[o + 3]
                if rop == RO_TRANSPARENT and a == 0:
                    if run_start is not None:
                        machine.write(pixels + dy * pitch + run_start * bytes_per,
                                      bytes(run))
                        run_start, run = None, bytearray()
                    continue
                native = self._rgba_to_native((r, g, b), depth)
                if rop == RO_BLEND and a != 255:
                    offset = pixels + dy * pitch + dx * bytes_per
                    if depth == 16:
                        old = machine.u16(offset)
                        nr = (r >> 3) * a + ((old >> 11) & 0x1F) * (255 - a)
                        ng = (g >> 2) * a + ((old >> 5) & 0x3F) * (255 - a)
                        nb = (b >> 3) * a + (old & 0x1F) * (255 - a)
                        native = ((nr // 255) << 11) | ((ng // 255) << 5) | (nb // 255)
                    elif depth == 32:
                        old = machine.u32(offset)
                        blend = lambda s, d: (s * a + d * (255 - a)) // 255
                        native = (blend(r, (old >> 16) & 0xFF) << 16) \
                            | (blend(g, (old >> 8) & 0xFF) << 8) \
                            | blend(b, old & 0xFF)
                if depth == 8:
                    run.append(native & 0xFF)
                elif depth == 16:
                    run += struct.pack("<H", native & 0xFFFF)
                else:
                    run += struct.pack("<I", native & 0xFFFFFFFF)
                if run_start is None:
                    run_start = dx
            if run_start is not None and run:
                machine.write(pixels + dy * pitch + run_start * bytes_per, bytes(run))

    # ------------------------------------------------------------- ISoundPlayer
    # AEECLSID_SOUNDPLAYER plays MIDI/MP3/QCP audio.  There is no audio backend
    # yet: sources are accepted and remembered, playback "runs" silently and
    # the status callbacks the game registers are delivered from the pump.
    def _new_sound_player(self) -> int:
        methods = {
            "RegisterNotify": self._sound_register_notify,
            "Set": self._sound_set,
            "Play": self._sound_play,
            "Stop": self._sound_stop,
            "Rewind": self._sound_rewind,
            "FastForward": self._sound_fast_forward,
            "Pause": self._sound_pause,
            "Resume": self._sound_resume,
            "SetTempo": self._sound_set_tempo,
            "SetTune": self._sound_set_tune,
            "SetVolume": self._sound_set_volume,
            "GetVolume": self._sound_get_volume,
            "GetTotalTime": self._sound_get_total_time,
            "SetSoundDevice": self._sound_set_device,
            "SetStream": self._sound_set_stream,
            "SetInfo": self._sound_set_info,
            "GetInfo": self._sound_get_info,
        }
        obj = self.make_object("ISoundPlayer", "ISoundPlayer", methods,
                               extra_bytes=64)
        self.sound_players[obj] = {
            "notify": 0, "user": 0, "input": SDT_NONE, "data": 0, "size": 0,
            "name": "", "volume": AEE_MAX_VOLUME, "playing": False,
            "device": 0, "stream": 0, "total_time": 0, "cmd_data": 0,
            "data_bytes": b"", "proc": None, "done": None,
        }
        def cleanup():
            state = self.sound_players.pop(obj)
            if state["done"]:
                state["done"]["cancelled"] = True
            self.audio.stop(state["proc"])
            self.m.free(state["cmd_data"])
        self._object_cleanup[obj] = cleanup
        return obj

    def _sound_state(self, this) -> dict:
        return self.sound_players.setdefault(this, {
            "notify": 0, "user": 0, "input": SDT_NONE, "data": 0, "size": 0,
            "name": "", "volume": AEE_MAX_VOLUME, "playing": False,
            "device": 0, "stream": 0, "total_time": 0, "cmd_data": 0,
            "data_bytes": b"", "proc": None, "done": None})

    def _sound_sniff(self, machine, pdata: int) -> bytes:
        """Best-effort size probe for a Set(SDT_BUFFER) source.

        Set() has no size argument, but the game's sounds are RIFF (QCP) or
        SMF containers whose headers declare their length.
        """
        try:
            head = machine.read(pdata, 12)
        except Exception:  # noqa: BLE001 - unreadable buffer: no sound
            return b""
        if head[:4] == b"RIFF":
            size = struct.unpack_from("<I", head, 4)[0] + 8
        elif head[:4] == b"MThd":
            size = 14
            pos = 14
            try:
                limit = 0
                while limit < 64:
                    chunk = machine.read(pdata + pos, 8)
                    length = struct.unpack_from(">I", chunk, 4)[0]
                    if chunk[:4] not in (b"MTrk", b"MThd") and length > (1 << 20):
                        break
                    size += 8 + length
                    pos += 8 + length
                    limit += 1
                    if size > (2 << 20):
                        break
            except Exception:  # noqa: BLE001
                pass
        else:
            return b""
        if not 0 < size <= (2 << 20):
            return b""
        try:
            return machine.read(pdata, size)
        except Exception:  # noqa: BLE001
            return b""

    def _sound_clip(self, state: dict):
        """Decoded host clip for the player's current source (or None)."""
        if state.get("data_bytes"):
            return self.audio.decode_bytes(state["data_bytes"], hint=state["name"])
        if state["input"] == SDT_FILE and state["name"]:
            path = self.map_guest_path(state["name"])
            if path and os.path.exists(path):
                return self.audio.decode_path(path, hint=state["name"])
        return None

    def _sound_source_name(self, machine, input_type, pdata) -> str:
        if not pdata:
            return ""
        if input_type == SDT_FILE:
            return machine.string(pdata, 128)
        return f"<buffer 0x{pdata:x}>"

    def _sound_cmd_data(self, machine, this, **fields) -> int:
        """Guest AEESoundPlayerCmdData union for a callback (never NULL)."""
        state = self._sound_state(this)
        if not state["cmd_data"]:
            state["cmd_data"] = machine.alloc(16)
        pointer = state["cmd_data"]
        data = bytearray(16)
        if "time" in fields:
            struct.pack_into("<I", data, 0, fields["time"] & 0xFFFFFFFF)
        if "volume" in fields:
            struct.pack_into("<H", data, 0, fields["volume"] & 0xFFFF)
        machine.write(pointer, bytes(data))
        return pointer

    def _sound_notify(self, machine, this, cb_type, status, param) -> None:
        """Queue the registered status callback (delivered from the pump)."""
        state = self.sound_players.get(this)
        if not state or not state["notify"]:
            return
        self.deferred.append((state["notify"],
                              [state["user"], cb_type, status, param]))

    def _sound_register_notify(self, machine, this, pfn, user):
        state = self._sound_state(this)
        state["notify"] = pfn
        state["user"] = user
        self.trace_call("ISoundPlayer::RegisterNotify", (this, pfn, user))
        return None

    def _sound_set(self, machine, this, input_type, pdata):
        """void Set(AEESoundPlayerInput t, void *pData)."""
        state = self._sound_state(this)
        state["input"] = input_type & 0xFF
        state["data"] = pdata
        state["size"] = 0
        state["data_bytes"] = b""
        state["name"] = self._sound_source_name(machine, input_type & 0xFF, pdata)
        if state["input"] == SDT_BUFFER and pdata:
            state["data_bytes"] = self._sound_sniff(machine, pdata)
        self.log.info(f"ISoundPlayer::Set input={input_type & 0xFF}"
                      f" data=0x{pdata:x} name={state['name']!r}"
                      f" bytes={len(state['data_bytes'])}")
        self.trace_call("ISoundPlayer::Set", (this, input_type, pdata))
        return None

    def _sound_capture(self, machine, name: str, pointer: int, size: int) -> None:
        """Dev helper: BIO3DPC_SOUND_DUMP=<dir> writes every source buffer."""
        directory = os.environ.get("BIO3DPC_SOUND_DUMP")
        if not directory or not pointer or size <= 0 or size > (1 << 20):
            return
        try:
            os.makedirs(directory, exist_ok=True)
            index = getattr(self, "_sound_dump_index", 0) + 1
            self._sound_dump_index = index
            safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
            path = os.path.join(directory, f"{index:02d}_{size}_{safe or 'buffer'}.bin")
            with open(path, "wb") as handle:
                handle.write(machine.read(pointer, size))
            self.log.info(f"sound buffer dumped to {path}")
        except Exception as error:  # noqa: BLE001 - dev helper only
            self.log.warn(f"sound dump failed: {error}")

    def _sound_apply_info(self, machine, this, pinfo) -> int:
        """Shared body of SetInfo/GetInfo (AEESoundPlayerInfo *)."""
        state = self._sound_state(this)
        if not pinfo:
            # A NULL info resets the source (AEE_SUCCESS).
            state.update(input=SDT_NONE, data=0, size=0, name="", data_bytes=b"")
            return SUCCESS
        input_type = machine.read(pinfo, 1)[0]
        pdata = machine.u32(pinfo + 4)
        size = machine.u32(pinfo + 8)
        state["input"] = input_type
        state["data"] = pdata
        state["size"] = size
        state["name"] = self._sound_source_name(machine, input_type, pdata)
        # The player does not own the caller's buffer, so keep a copy: the
        # guest is free to reuse or free it once SetInfo returns.
        state["data_bytes"] = b""
        if input_type == SDT_BUFFER and pdata and 0 < size <= (2 << 20):
            try:
                state["data_bytes"] = machine.read(pdata, size)
            except Exception:  # noqa: BLE001 - unmapped buffer: play silence
                state["data_bytes"] = b""
        return SUCCESS

    def _sound_set_info(self, machine, this, pinfo):
        input_type = machine.read(pinfo, 1)[0] if pinfo else 0
        size = machine.u32(pinfo + 8) if pinfo else 0
        if pinfo and size == 0:
            self.log.warn("ISoundPlayer::SetInfo: dwSize == 0", at=hex(pinfo))
            return EBADPARM
        rc = self._sound_apply_info(machine, this, pinfo)
        state = self._sound_state(this)
        self.log.info(f"ISoundPlayer::SetInfo input={input_type}"
                      f" data=0x{state['data']:x} size={size}"
                      f" name={state['name']!r}")
        if state["data"] and state["size"]:
            self._sound_capture(machine, state["name"], state["data"],
                                state["size"])
        self.trace_call("ISoundPlayer::SetInfo", (this, pinfo), ret=rc)
        return rc

    def _sound_get_info(self, machine, this, pinfo):
        state = self._sound_state(this)
        if pinfo:
            machine.write(pinfo, struct.pack(
                "<BxxxII", state["input"] & 0xFF, state["data"] & 0xFFFFFFFF,
                state["size"] & 0xFFFFFFFF))
        self.trace_call("ISoundPlayer::GetInfo", (this, pinfo))
        return SUCCESS

    def _schedule_sound_done(self, machine, this, seconds: float) -> None:
        """Deliver PLAY_CB/AEE_SOUNDPLAYER_DONE when the clip really ends."""
        state = self._sound_state(this)
        total_ms = int(round(seconds * 1000))
        state["total_time"] = total_ms
        entry = {
            "due": time.monotonic() + max(0.05, seconds),
            "pfn": state["notify"],
            "args": [state["user"], SP_PLAY_CB, SP_DONE,
                     self._sound_cmd_data(machine, this, time=total_ms)],
            "note": "sound done",
        }

        def finished():
            state.update(playing=False, proc=None, done=None)

        entry["on_fire"] = finished
        state["done"] = entry
        self.scheduled.append(entry)
        self.log.info(f"ISoundPlayer: playback scheduled"
                      f" name={state['name']!r} seconds={seconds:.3f}")

    def _sound_play(self, machine, this):
        state = self._sound_state(this)
        if state["playing"]:
            # "If a playback is currently active, then a new play command is
            # not accepted" (AEESoundPlayer.h).
            self.log.info(f"ISoundPlayer::Play ignored (busy)"
                          f" name={state['name']!r}")
            return None
        clip = self._sound_clip(state)
        seconds = clip.seconds if clip else FALLBACK_SECONDS
        process = self.audio.play(clip, state["volume"])
        state["playing"] = True
        state["proc"] = process
        param = self._sound_cmd_data(machine, this, time=int(seconds * 1000))
        self.log.info(f"ISoundPlayer::Play name={state['name']!r}"
                      f" seconds={seconds:.3f}"
                      f" output={'on' if process else 'silent'}")
        self.trace_call("ISoundPlayer::Play", (this,))
        self._sound_notify(machine, this, SP_PLAY_CB, SP_SUCCESS, param)
        self._schedule_sound_done(machine, this, seconds)
        return None

    def _sound_stop(self, machine, this):
        state = self._sound_state(this)
        entry = state.get("done")
        if entry:
            entry["cancelled"] = True
            state["done"] = None
        self.audio.stop(state.get("proc"))
        state["proc"] = None
        was_playing = state["playing"]
        state["playing"] = False
        self.log.info(f"ISoundPlayer::Stop name={state['name']!r}"
                      f" playing={was_playing}")
        self.trace_call("ISoundPlayer::Stop", (this,))
        self._sound_notify(machine, this, SP_STATUS_CB, SP_SUCCESS, 0)
        if was_playing:
            param = self._sound_cmd_data(machine, this,
                                         time=state["total_time"])
            self._sound_notify(machine, this, SP_PLAY_CB, SP_DONE, param)
        return None

    def _sound_rewind(self, machine, this, time):
        self.trace_call("ISoundPlayer::Rewind", (this, time))
        self._sound_notify(machine, this, SP_PLAY_CB, SP_REWIND, 0)
        return None

    def _sound_fast_forward(self, machine, this, time):
        self.trace_call("ISoundPlayer::FastForward", (this, time))
        self._sound_notify(machine, this, SP_PLAY_CB, SP_FFORWARD, 0)
        return None

    def _sound_pause(self, machine, this):
        self.trace_call("ISoundPlayer::Pause", (this,))
        self._sound_notify(machine, this, SP_PLAY_CB, SP_PAUSE, 0)
        return None

    def _sound_resume(self, machine, this):
        self.trace_call("ISoundPlayer::Resume", (this,))
        self._sound_notify(machine, this, SP_PLAY_CB, SP_RESUME, 0)
        return None

    def _sound_set_tempo(self, machine, this, factor):
        self.trace_call("ISoundPlayer::SetTempo", (this, factor))
        return None

    def _sound_set_tune(self, machine, this, step):
        self.trace_call("ISoundPlayer::SetTune", (this, step))
        return None

    def _sound_set_volume(self, machine, this, volume):
        state = self._sound_state(this)
        state["volume"] = volume & 0xFFFF
        self.trace_call("ISoundPlayer::SetVolume", (this, volume))
        return None

    def _sound_get_volume(self, machine, this):
        state = self._sound_state(this)
        param = self._sound_cmd_data(machine, this, volume=state["volume"])
        self.trace_call("ISoundPlayer::GetVolume", (this,),
                        ret=state["volume"])
        self._sound_notify(machine, this, SP_VOLUME_CB, SP_SUCCESS, param)
        return None

    def _sound_get_total_time(self, machine, this):
        state = self._sound_state(this)
        clip = self._sound_clip(state)
        total = int(round(clip.seconds * 1000)) if clip else state["total_time"]
        if clip:
            state["total_time"] = total
        param = self._sound_cmd_data(machine, this, time=total)
        self.trace_call("ISoundPlayer::GetTotalTime", (this,), ret=total)
        self._sound_notify(machine, this, SP_TIME_CB, SP_SUCCESS, param)
        return None

    def _sound_set_device(self, machine, this, device, flag, mask):
        state = self._sound_state(this)
        state["device"] = device
        self.trace_call("ISoundPlayer::SetSoundDevice", (this, device, flag, mask))
        return None

    def _sound_set_stream(self, machine, this, stream):
        state = self._sound_state(this)
        state["stream"] = stream
        self.trace_call("ISoundPlayer::SetStream", (this, stream))
        return None

    # -------------------------------------------------------------------- pump
    def pump(self, max_seconds: float | None = None, idle_sleep=0.001) -> str:
        """Run guest timers/events until quit.  Returns the stop reason."""
        m = self.m
        monotonic = time.monotonic
        deadline = None if max_seconds is None else monotonic() + max_seconds
        while not self.quit:
            self._dispatch_pending_events()
            now = monotonic()
            due = [(key, timer) for key, timer in self.timers.items()
                   if timer["due"] <= now]
            for key, timer in due:
                # BREW timers are one-shot: take it out before the callback so
                # a re-arm from inside the callback schedules a fresh expiry.
                self.timers.pop(key, None)
                self.log.trace("timer", f"timer(0x{timer['pfn']:x})",
                               args=(timer["user"],))
                m.call(timer["pfn"], [timer["user"]], timeout=30.0)
                self.guest_ticks += 1
                if self.on_tick is not None:
                    self.on_tick()
                self._dispatch_pending_events()
            self._dispatch_pending_events()
            self._dispatch_deferred()
            self._dispatch_scheduled()
            now = monotonic()
            if deadline is not None and now >= deadline:
                return "timeout"
            if not due:
                # Nothing ran this round: sleep until the earliest pending
                # callback instead of polling every idle_sleep.  A re-armed
                # timer is only checked when it is due anyway, so this changes
                # nothing about when callbacks fire - it just stops the host
                # from waking up between them.
                waits = [timer["due"] for timer in self.timers.values()]
                waits += [entry["due"] for entry in self.scheduled]
                wait = min(waits) - now if waits else idle_sleep
                if deadline is not None:
                    wait = min(wait, deadline - now)
                if wait > 0:
                    time.sleep(wait)
        return "quit"

    def _dispatch_scheduled(self) -> None:
        """Fire callbacks the runtime owes the guest (sound completion, ...)."""
        now = time.monotonic()
        for entry in [e for e in self.scheduled if e["due"] <= now]:
            try:
                self.scheduled.remove(entry)
            except ValueError:  # pragma: no cover - removed concurrently
                continue
            if entry.get("cancelled") or self.quit:
                continue
            on_fire = entry.get("on_fire")
            if on_fire is not None:
                on_fire()
            pfn = entry.get("pfn")
            if pfn:
                self.deferred.append((pfn, list(entry.get("args", []))))

    def shutdown(self) -> None:
        """Release host resources even when guest teardown did not finish."""
        for address in list(self.files):
            self._close_file(address)
        for entry in self.scheduled:
            entry["cancelled"] = True
        self.scheduled.clear()
        self.pending_events.clear()
        self.key_down_ticks.clear()
        self.key_down_timer_ticks.clear()
        self.deferred.clear()
        self.audio.shutdown()

    def _dispatch_deferred(self) -> None:
        while self.deferred and not self.quit:
            pfn, args = self.deferred.pop(0)
            try:
                self.m.call(pfn, args, timeout=30.0)
            except GuestError as error:
                self.log.warn(f"deferred callback 0x{pfn:x} faulted: {error}")

    def _dispatch_pending_events(self) -> None:
        index = 0
        blocked_keys = set()
        def input_tick(key):
            return self.input_clock(key) if self.input_clock is not None else self.guest_ticks
        while index < len(self.pending_events) and not self.quit:
            event = self.pending_events[index]
            cls, evt, wparam, dwparam = event[:4]
            ensure_sample = event[4] if len(event) > 4 else True
            if ((evt in (EVT_KEY, EVT_KEY_RELEASE) and wparam in blocked_keys) or
                    (evt == EVT_KEY_RELEASE and ensure_sample and
                    self.key_down_ticks.get(wparam) == input_tick(wparam) and
                    self.guest_ticks - self.key_down_timer_ticks.get(wparam, 0) < 8 and
                    self.timers)):
                # Preserve ordering for this key, without delaying unrelated
                # movement/aim events behind a very short attack tap.
                blocked_keys.add(wparam)
                index += 1
                continue
            self.pending_events.pop(index)
            if self.applet:
                dispatch_applet(self.m, self.applet, evt, wparam, dwparam)
                if evt == EVT_KEY:
                    self.key_down_ticks[wparam] = input_tick(wparam)
                    self.key_down_timer_ticks[wparam] = self.guest_ticks
                elif evt == EVT_KEY_RELEASE:
                    self.key_down_ticks.pop(wparam, None)
                    self.key_down_timer_ticks.pop(wparam, None)

    # ------------------------------------------------------------------- input
    @staticmethod
    def _rgb565_to_rgb888(raw: bytes) -> bytes:
        """Top-down RGB565 (little-endian words) to RGB888, whole-buffer.

        Both input bytes of a pixel are needed, so the row is split into its
        even (low) and odd (high) byte positions, each value is translated
        with its channel table, and the three channel planes are written back
        into an interleaved output.  The green plane needs both halves; adding
        the two translated planes byte-wise works because each byte sum stays
        below 256, so the carry never crosses a pixel.
        """
        high = raw[1::2]
        low = raw[0::2]
        out = bytearray(len(raw) // 2 * 3)
        out[0::3] = high.translate(_RGB565_R)
        out[2::3] = low.translate(_RGB565_B)
        green = (int.from_bytes(high.translate(_RGB565_GH), "little")
                 + int.from_bytes(low.translate(_RGB565_GL), "little"))
        out[1::3] = green.to_bytes(len(high), "little")
        return bytes(out)

    def snapshot_rgb(self):
        """Copy of the device bitmap as top-down RGB888 (w, h, bytes)."""
        bitmap = self.roles.get("device_bitmap")
        if not bitmap:
            return None
        width, height, pitch, depth, pixels = self.bitmap_geometry(bitmap)
        if not pixels or width <= 0 or height <= 0:
            return None
        m = self.m
        if depth == 16:
            if pitch == width * 2:
                return (width, height,
                        self._rgb565_to_rgb888(m.read(pixels, height * pitch)))
            rows = [self._rgb565_to_rgb888(m.read(pixels + y * pitch, width * 2))
                    for y in range(height)]
            return width, height, b"".join(rows)
        if depth in (24, 32):
            step = depth // 8
            rows = []
            for y in range(height):
                row = m.read(pixels + y * pitch, width * step)
                rows.append(b"".join(row[i:i + 3] for i in range(0, len(row), step)))
            return width, height, b"".join(rows)
        # 8-bit indexed bitmaps would need their palette; the device bitmap is
        # 16-bit on this build, so leave that (rare) case to the caller.
        return None

    def post_key(self, key: int, dwparam: int = 0, press: bool = True,
                 ensure_sample: bool = True) -> None:
        """Deliver a key event to the applet like the device would.

        This build polls EVT_KEY (down) and EVT_KEY_RELEASE (up), and ignores
        EVT_KEY_PRESS. Keep both transitions even for a very short host tap.
        """
        if not self.applet:
            return
        event = EVT_KEY if press else EVT_KEY_RELEASE
        entry = (0, event, key & 0xFFFF, dwparam & 0xFFFFFFFF)
        # Mouse budgets already count native camera samples. Their release
        # must not wait for an independent key-query clock to catch up.
        self.pending_events.append(entry if ensure_sample else entry + (False,))

    def post_key_press(self, key: int, dwparam: int = 0) -> None:
        """Key-down: EVT_KEY_PRESS plus the EVT_KEY some apps act on."""
        self.pending_events.append((0, EVT_KEY_PRESS, key & 0xFFFF,
                                    dwparam & 0xFFFFFFFF))

    def post_char(self, char: int, dwparam: int = 0) -> None:
        if not self.applet:
            return
        self.pending_events.append((0, EVT_CHAR, char & 0xFFFF,
                                    dwparam & 0xFFFFFFFF))


def dispatch_applet(machine, applet, event, wparam=0, dwparam=0, timeout=30.0):
    """Call IApplet::HandleEvent through the guest vtable (slot 2)."""
    vtable = machine.u32(applet)
    handler = machine.u32(vtable + 2 * 4)
    return machine.call(handler, [applet, event, wparam, dwparam], timeout=timeout)
