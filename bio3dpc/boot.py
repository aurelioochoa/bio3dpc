"""Boot sequence: load the module image and drive the BREW lifecycle.

The host plays the phone's BREW shell:

    AEEMod_Load            ->  IModule*        (shim + AEEStaticMod_New)
    IModule::CreateInstance(0x0109005B) ->  IApplet*
    IApplet::HandleEvent(EVT_APP_START, &AEEAppStart)
    pump host timers set via IShell::SetTimer until the app quits
"""
from __future__ import annotations

import time

from .helper import HELPER_VERSION, HelperTable
from .log import RunLog
from .machine import ArmMachine, GuestError
from .modfile import ModFile
from .profiles import identify
from .services import (
    AEECLSID_DISPLAY,
    BrewRuntime,
    EVT_APP_START,
    SCREEN_HEIGHT,
    SCREEN_WIDTH,
    SUCCESS,
    dispatch_applet,
    encode_app_start,
)

# The BioShock 3D applet class, read from the app record in 278595.mif.
# Other builds use different class IDs; known module hashes select a profile.
DEFAULT_APP_CLSID = 0x0109005B


class BootError(RuntimeError):
    pass


class Boot:
    """One emulated session: machine + helper HLE + BREW services."""

    def __init__(self, mod_path, log: RunLog | None = None,
                 data_dir: str | None = None, app_clsid: int | None = None,
                 trace_path=None, level: str = "info", width: int | None = None,
                 height: int | None = None, save_dir: str | None = None):
        self.mod_path = str(mod_path)
        self.data_dir = data_dir
        self.profile = identify(mod_path)
        self.app_clsid = app_clsid if app_clsid is not None else (
            self.profile.app_clsid if self.profile else DEFAULT_APP_CLSID)
        self.width = width if width is not None else (self.profile.width if self.profile else SCREEN_WIDTH)
        self.height = height if height is not None else (self.profile.height if self.profile else SCREEN_HEIGHT)
        if not (1 <= self.width <= 2048 and 1 <= self.height <= 2048):
            raise ValueError('screen dimensions must be between 1 and 2048')
        self.log = log or RunLog(level=level, trace_path=trace_path)
        self.m = ArmMachine(log=self.log)
        self.helper = HelperTable(self.m, self.log)
        self.runtime = BrewRuntime(self.m, self.log, self.helper,
                                   data_dir=self.data_dir,
                                   mod_path=self.mod_path, width=self.width,
                                   height=self.height, save_dir=save_dir)
        self.module = 0
        self.applet = 0
        self.started = False

    # ------------------------------------------------------------------ step 1
    def load_module(self) -> int:
        m, log = self.m, self.log
        table = self.helper.build()
        self.helper_table = table
        mod = ModFile(self.mod_path)
        mod.check_position_independent()
        base = mod.load_into(m)
        self.mod = mod
        m.w32(base - 8, HELPER_VERSION)
        m.w32(base - 4, table)
        ppmod = m.alloc(4)
        t0 = time.monotonic()
        rc = m.call(base, [self.runtime.shell, 0, ppmod], timeout=60.0)
        self.module = m.u32(ppmod)
        log.info("AEEMod_Load", rc=hex(rc), module=hex(self.module),
                 seconds=round(time.monotonic() - t0, 3))
        if rc != SUCCESS or not self.module:
            raise BootError(f"AEEMod_Load failed rc={rc:#x}")
        return self.module

    # ------------------------------------------------------------------ step 2
    def create_applet(self, clsid: int | None = None) -> int:
        m, log = self.m, self.log
        if not self.module:
            self.load_module()
        clsid = clsid or self.app_clsid
        vtbl = m.u32(self.module)
        create = m.u32(vtbl + 2 * 4)   # IModule: AddRef, Release, CreateInstance
        ppobj = m.alloc(4)
        rc = m.call(create, [self.module, self.runtime.shell, clsid, ppobj],
                    timeout=60.0)
        self.applet = m.u32(ppobj)
        log.info("IModule::CreateInstance", cls=hex(clsid), rc=hex(rc),
                 applet=hex(self.applet))
        if rc != SUCCESS or not self.applet:
            raise BootError(f"CreateInstance({clsid:#x}) failed rc={rc:#x}")
        self.runtime.applet = self.applet
        self.runtime.app_clsid = clsid
        self.helper.applet = self.applet
        return self.applet

    # ------------------------------------------------------------------ step 3
    def app_start(self) -> None:
        m, log = self.m, self.log
        display = self.runtime.roles.get("display", 0)
        args = m.alloc(8)
        m.write_string(args, "bio3d")
        start = m.alloc(32)
        m.write(start, encode_app_start(self.app_clsid, display,
                                        self.width, self.height, args))
        rc = dispatch_applet(m, self.applet, EVT_APP_START, 0, start)
        log.info("EVT_APP_START", rc=hex(rc & 0xFFFFFFFF))
        self.started = True
        log.trace("lifecycle", "EVT_APP_START", args=(self.applet, start),
                  ret=rc)

    # ------------------------------------------------------------------- drive
    def run(self, seconds: float = 10.0, frames: int | None = None) -> str:
        if not self.started:
            self.load_module()
            self.create_applet()
            self.app_start()
        if frames:
            seconds = frames / 60.0
        reason = self.runtime.pump(max_seconds=seconds)
        # The play frontend drives the guest in short slices (Frontend._tick
        # calls run(seconds=0.02) every frame): a "timeout" is just the slice
        # deadline expiring, not the session ending, and logging it once per
        # slice would flood the log with ~30 identical lines per second.
        # Keep those at debug; the info line is reserved for a real stop.
        log = self.log.debug if reason == "timeout" else self.log.info
        log("session finished", reason=reason,
            calls=len(self.runtime.stats.calls))
        return reason

    def shutdown(self) -> None:
        """Best-effort EVT_APP_STOP before the machine goes away."""
        try:
            if self.started and self.applet:
                dispatch_applet(self.m, self.applet, 1)  # EVT_APP_STOP
        except GuestError as error:
            self.log.warn(f"EVT_APP_STOP raised: {error}")
        try:
            self.runtime.shutdown()
        except Exception as error:  # noqa: BLE001 - never mask a clean exit
            self.log.warn(f"runtime shutdown failed: {error}")
        self.m.close()
        self.log.close()
