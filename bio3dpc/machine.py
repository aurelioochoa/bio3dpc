"""ARM32 guest machine: shared host/guest memory, Unicorn execution, traps.

Design (same family as re4mepc): one 64 MiB region is mmap'd below 4 GiB and
mapped into Unicorn with ``mem_map_ptr``.  Host code therefore reads and writes
guest memory directly through ctypes - no marshalling per access, and later the
software framebuffer can be handed to SDL2 without a copy.

Guest imports (BREW helper functions, interface vtable methods) are dispatched
through a trap region: every imported function gets a 4-byte slot holding
``bx lr``.  A UC_HOOK_CODE hook over the region intercepts execution, calls the
Python handler, writes the return registers, and lets ``bx lr`` return to the
guest caller.
"""
from __future__ import annotations

import ctypes as C
import struct
import time
from collections import Counter, deque

from unicorn import (
    Uc,
    UC_ARCH_ARM,
    UC_MODE_ARM,
    UC_HOOK_CODE,
    UC_HOOK_MEM_INVALID,
    UC_HOOK_INSN_INVALID,
    UcError,
)
from unicorn.arm_const import (
    UC_ARM_REG_R0,
    UC_ARM_REG_R1,
    UC_ARM_REG_R2,
    UC_ARM_REG_R3,
    UC_ARM_REG_SP,
    UC_ARM_REG_LR,
    UC_ARM_REG_PC,
    UC_ARM_REG_C1_C0_2,
    UC_ARM_REG_FPEXC,
)

REGS = (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)

_BX_LR = b"\x1e\xff\x2f\xe1"  # ARM: bx lr

# --- host-side memory primitives -------------------------------------------
# The guest region is mmap'd and shared with Unicorn, so fixed-size fields can
# be read and written straight through the host buffer.  Unicorn's own access
# entry points (``uc_mem_read``/``uc_reg_read``) cost ~1 us per call, which
# dwarfs the work a trap handler does; these are the same operations at
# ~0.1-0.7 us.
_UNPACK_U16 = struct.Struct("<H").unpack_from
_UNPACK_U32 = struct.Struct("<I").unpack_from
_PACK_U16 = struct.Struct("<H").pack_into
_PACK_U32 = struct.Struct("<I").pack_into

_LIBC = C.CDLL(None)
_STRNLEN = _LIBC.strnlen
_STRNLEN.argtypes = (C.c_void_p, C.c_size_t)
_STRNLEN.restype = C.c_size_t
_MUNMAP = _LIBC.munmap
_MUNMAP.argtypes = (C.c_void_p, C.c_size_t)
_MUNMAP.restype = C.c_int
_STRING_AT = C.string_at
_MEM_MOVE = C.memmove


class GuestError(RuntimeError):
    """Raised when guest execution faults or a service handler fails."""


class GuestTrapUnsupported(GuestError):
    """A trapped import has no implementation bound."""


class GuestTimeout(GuestError):
    """Guest execution stopped before the function returned."""


class ArmMachine:
    SIZE = 64 * 1024 * 1024

    # BREW phones run the app at low absolute addresses (a BREW module is
    # loaded near 0x00000000 and the app's own code/data/heap live in the low
    # megabytes).  Some shipped builds contain paths that read "pointers"
    # which are really garbage bytes (e.g. the ASCII of a string literal);
    # on a phone those reads land in the app's own low address space and the
    # code tolerates the result.  Mirror that by mapping zero-filled low RAM
    # instead of faulting on every low address.
    DEVICE_RAM_SIZE = 64 * 1024 * 1024

    # --- layout inside the shared region -----------------------------------
    LOAD_OFFSET = 0x00100000          # module file byte 0 (mapped file base)
    STACK_TOP_OFFSET = 8 * 1024 * 1024
    TRAP_OFFSET = 16 * 1024 * 1024    # import trampoline slots
    TRAP_SIZE = 1024 * 1024           # 256 Ki slots
    RETURN_OFFSET = TRAP_OFFSET + TRAP_SIZE + 0x1000
    # Helper routines run as guest code (native_helpers.py); outside the trap
    # region, whose code hook would treat them as traps.
    NATIVE_OFFSET = RETURN_OFFSET + 0x1000
    NATIVE_SIZE = 0x1000
    HEAP_OFFSET = 32 * 1024 * 1024
    HEAP_RESERVE = 1024 * 1024        # keep the final MiB out of the heap

    def __init__(self, log=None):
        self.log = log
        libc = C.CDLL(None)
        mmap = libc.mmap
        mmap.argtypes = (C.c_void_p, C.c_size_t, C.c_int, C.c_int, C.c_int, C.c_long)
        mmap.restype = C.c_void_p
        # MAP_PRIVATE|MAP_ANONYMOUS|MAP_32BIT so the region is below 4 GiB.
        base = mmap(None, self.SIZE, 3, 0x22 | 0x40, -1, 0)
        if base is None or base == C.c_void_p(-1).value or base + self.SIZE > 0x1_0000_0000:
            raise RuntimeError("cannot allocate 32-bit shared guest memory (Linux x86_64 required)")
        self.base = base
        self.uc = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        self.uc.mem_map_ptr(base, self.SIZE, 7, base)
        # Zero-filled "device RAM" at the low addresses the guest may compute
        # by accident (see DEVICE_RAM_SIZE).  Unicorn leaves mem_map memory
        # zeroed, which matches a phone whose app image does not cover them.
        self.device_end = min(self.DEVICE_RAM_SIZE, self.base) & ~0xFFF
        if self.device_end:
            self.uc.mem_map(0, self.device_end, 7)
        # Allow coprocessor/VFP state access; harmless on plain ARMv5 cores and
        # required if guest code ever touches cp10/cp11.
        self.uc.reg_write(UC_ARM_REG_C1_C0_2, 0xF << 20)
        self.uc.reg_write(UC_ARM_REG_FPEXC, 0x40000000)
        self.uc.reg_write(UC_ARM_REG_SP, self.stack_top)

        self.trap_next = base + self.TRAP_OFFSET
        self.trap_end = self.trap_next + self.TRAP_SIZE
        self.return_address = base + self.RETURN_OFFSET
        self.heap = base + self.HEAP_OFFSET
        self.heap_end = base + self.SIZE - self.HEAP_RESERVE

        # Host view of the shared region: `struct` reads/writes of scalars go
        # through this buffer (see the module-level primitives above).
        self._shared = (C.c_char * self.SIZE).from_address(base)
        self._shared_end = base + self.SIZE

        # Raw register access.  ``Uc.reg_read``/``reg_write`` allocate a ctypes
        # value and look up the register class on every call (~1-2 us); these
        # keep one buffer per register and call the binding's C entry point
        # directly, which is what the trap dispatcher needs on every guest call.
        self._gpr_regs = (C.c_int * 4)(*REGS)
        self._gpr_bufs = [C.c_uint32() for _ in REGS]
        self._gpr_ptrs = (C.c_void_p * 4)(
            *[C.cast(C.byref(buf), C.c_void_p) for buf in self._gpr_bufs])
        self._reg_buf = C.c_uint32()
        self._reg_buf_ref = C.byref(self._reg_buf)
        self._uc_reg_read_batch = getattr(self.uc, "_do_reg_read_batch", None)
        self._uc_reg_write = getattr(self.uc, "_do_reg_write", None)
        if not callable(self._uc_reg_read_batch):
            self._uc_reg_read_batch = None
        if not callable(self._uc_reg_write):
            self._uc_reg_write = None
        self._reg_read_cache: dict[int, tuple] = {}

        self.allocations: dict[int, int] = {}
        self.free_blocks: list[tuple[int, int]] = []
        self.traps: dict[int, tuple[str, object, int, str, bool]] = {}
        # Guest code range (set by the module loader): host writes inside it
        # must invalidate Unicorn's translated blocks.
        self.code_start = 0
        self.code_end = 0
        self.calls: Counter[str] = Counter()
        self.last_call = ""
        # Last 64 guest->host calls as raw (name, args) pairs.  Kept
        # unformatted because formatting hex strings for every trap on the hot
        # path costs more than the trap itself; ``recent_calls`` renders them
        # on access (fault reports, debug tools).
        self._recent: deque[tuple[str, list[int]]] = deque(maxlen=64)
        self.pending_error: BaseException | None = None
        self._input_buffers = None

        self._hook_handles = [
            self.uc.hook_add(UC_HOOK_CODE, self._trap,
                             begin=self.trap_next, end=self.trap_end),
            self.uc.hook_add(UC_HOOK_MEM_INVALID, self._invalid_memory),
            self.uc.hook_add(UC_HOOK_INSN_INVALID, self._invalid_insn),
        ]

        self._call_depth = 0
        self.after_call = []
        self._trace_hook = None
        self._trace_count = 0
        self._trace_limit = 0

    def close(self) -> None:
        """Unmap the guest region; the machine is unusable afterwards.

        The shared window is a process-wide, finite resource: every machine
        reserves up to ~128 MiB below 4 GiB (the shared region plus Unicorn's
        low "device RAM"), and neither is freed implicitly - Unicorn only
        borrows the shared pointer (``mem_map_ptr``).  A process that creates
        one machine per case (the test suite) fills the window after ~15.
        """
        uc, self.uc = self.uc, None
        if uc is None:
            return
        # Drop everything that keeps Unicorn alive or references the mapping;
        # the hook callbacks close a reference cycle through this machine.
        self._uc_reg_read_batch = None
        self._uc_reg_write = None
        self.after_call.clear()
        for handle in self._hook_handles:
            uc.hook_del(handle)
        self._hook_handles = []
        del uc          # last reference: Uc.__del__ releases its own mappings
        self._shared = None
        _MUNMAP(C.c_void_p(self.base), C.c_size_t(self.SIZE))
        self.base = 0
        self.device_end = 0

    # ------------------------------------------------------------------ layout
    @property
    def load_base(self) -> int:
        return self.base + self.LOAD_OFFSET

    @property
    def stack_top(self) -> int:
        return self.base + self.STACK_TOP_OFFSET

    @property
    def heap_base(self) -> int:
        return self.base + self.HEAP_OFFSET

    def contains(self, pointer: int, size: int = 1) -> bool:
        return self._in_device_ram(pointer, size) or self._in_shared(pointer, size)

    def _in_shared(self, pointer: int, size: int = 1) -> bool:
        return self.base <= pointer and pointer + size <= self.base + self.SIZE

    def _in_device_ram(self, pointer: int, size: int = 1) -> bool:
        return 0 <= pointer and pointer + size <= self.device_end

    def readable_limit(self, pointer: int) -> int:
        """Bytes readable from ``pointer`` before the end of its region."""
        if self._in_shared(pointer):
            return self.base + self.SIZE - pointer
        if self._in_device_ram(pointer):
            return self.device_end - pointer
        return 0

    def check(self, pointer: int, size: int = 1) -> int:
        if not self.contains(pointer, size):
            raise GuestError(f"invalid guest pointer 0x{pointer:x} + {size}")
        return pointer

    # ------------------------------------------------------------ memory access
    def read(self, pointer: int, size: int) -> bytes:
        if self._in_shared(pointer, size):
            return _STRING_AT(pointer, size)
        if self._in_device_ram(pointer, size):
            return bytes(self.uc.mem_read(pointer, size))
        raise GuestError(f"invalid guest pointer 0x{pointer:x} + {size}")

    def unpack(self, layout: struct.Struct, pointer: int):
        """Unpack a small fixed-size guest struct in one memory access."""
        offset = pointer - self.base
        if 0 <= offset <= self.SIZE - layout.size:
            return layout.unpack_from(self._shared, offset)
        return layout.unpack(self.read(pointer, layout.size))

    def write(self, pointer: int, data: bytes) -> None:
        length = len(data)
        if self._in_shared(pointer, length):
            # The region is the host buffer Unicorn itself executes from, so a
            # host memmove is the same store Unicorn's memory layer performs -
            # without its ~1 us call overhead per (usually tiny) write.
            _MEM_MOVE(pointer, data, length)
            self.invalidate_code(pointer, length)
            return
        if self._in_device_ram(pointer, length):
            # Device RAM is Unicorn-owned memory; it has no host mapping.
            self.uc.mem_write(pointer, data)
            return
        raise GuestError(f"invalid guest pointer 0x{pointer:x} + {length}")

    def move(self, dest: int, src: int, size: int) -> bool:
        """Host-side ``memmove`` when both ends are in the shared mapping.

        Guest ``memcpy``/``memmove`` calls arrive as traps; copying through
        Python bytes would read the payload into a fresh object and copy it a
        second time.  Returns False when Unicorn has to do the work (device
        RAM), so the caller can fall back to read+write.
        """
        if size <= 0:
            return True
        if self._in_shared(dest, size) and self._in_shared(src, size):
            _MEM_MOVE(dest, src, size)
            self.invalidate_code(dest, size)
            return True
        return False

    def fill(self, dest: int, byte: int, size: int) -> bool:
        """Host-side ``memset`` inside the shared mapping (False for device RAM)."""
        if size <= 0:
            return True
        if self._in_shared(dest, size):
            C.memset(dest, byte, size)
            self.invalidate_code(dest, size)
            return True
        return False

    def invalidate_code(self, pointer: int, size: int) -> None:
        """Drop translated blocks overlapping a host write into the image.

        The game patches its own code/data while running; a host-side write
        bypasses the guest write path Unicorn uses for self-modifying code, so
        the affected translation blocks must be dropped explicitly.
        """
        if size <= 0:
            return
        low, high = pointer, pointer + size
        # When a module image is known, only its range can hold guest code;
        # without one (tests, ad-hoc snippets) the written range itself is the
        # only candidate.
        if self.code_start and self.code_end:
            low = max(low, self.code_start)
            high = min(high, self.code_end)
        if low < high:
            self.uc.ctl_remove_cache(low, high)

    def u32(self, pointer: int) -> int:
        offset = pointer - self.base
        if 0 <= offset <= self.SIZE - 4:
            return _UNPACK_U32(self._shared, offset)[0]
        return struct.unpack("<I", self.read(pointer, 4))[0]

    def w32(self, pointer: int, value: int) -> None:
        offset = pointer - self.base
        if 0 <= offset <= self.SIZE - 4:
            _PACK_U32(self._shared, offset, value & 0xFFFFFFFF)
            self.invalidate_code(pointer, 4)
            return
        self.write(pointer, struct.pack("<I", value & 0xFFFFFFFF))

    def u16(self, pointer: int) -> int:
        offset = pointer - self.base
        if 0 <= offset <= self.SIZE - 2:
            return _UNPACK_U16(self._shared, offset)[0]
        return struct.unpack("<H", self.read(pointer, 2))[0]

    def w16(self, pointer: int, value: int) -> None:
        offset = pointer - self.base
        if 0 <= offset <= self.SIZE - 2:
            _PACK_U16(self._shared, offset, value & 0xFFFF)
            self.invalidate_code(pointer, 2)
            return
        self.write(pointer, struct.pack("<H", value & 0xFFFF))

    def cstring_bytes(self, pointer: int, limit: int = 65536) -> bytes:
        """Read a NUL-terminated byte string with a sanity limit."""
        if pointer == 0:
            return b""
        if self._in_shared(pointer):
            maxlen = min(limit, self._shared_end - pointer)
            return _STRING_AT(pointer, _STRNLEN(pointer, maxlen))
        self.check(pointer, 1)
        # Device RAM (not host-addressable): read in chunks via Unicorn.
        out = bytearray()
        position = pointer
        while len(out) < limit:
            available = self.readable_limit(position)
            if available <= 0:
                break
            chunk = self.read(position, min(4096, available, limit - len(out)))
            index = chunk.find(b"\0")
            if index >= 0:
                out += chunk[:index]
                return bytes(out)
            out += chunk
            position += len(chunk)
        return bytes(out)

    def string(self, pointer: int, limit: int = 65536) -> str:
        return self.cstring_bytes(pointer, limit).decode("utf-8", "replace")

    def write_string(self, pointer: int, value: str | bytes) -> None:
        data = value.encode() if isinstance(value, str) else value
        self.write(pointer, data + b"\0")

    # ---------------------------------------------------------------- allocator
    def alloc(self, size: int) -> int:
        """Zero-filled guest heap allocation (BREW MALLOC semantics)."""
        if size <= 0:
            return 0
        size = max(16, (size + 15) & ~15)
        for i, (pointer, available) in enumerate(self.free_blocks):
            if available >= size:
                self.free_blocks.pop(i)
                self.allocations[pointer] = available
                C.memset(pointer, 0, available)
                return pointer
        pointer = self.heap
        if pointer + size > self.heap_end:
            raise GuestError("guest heap exhausted")
        self.heap += size
        self.allocations[pointer] = size
        C.memset(pointer, 0, size)
        return pointer

    def free(self, pointer: int) -> None:
        if not pointer:
            return
        size = self.allocations.pop(pointer, None)
        if size is None:
            raise GuestError(f"free of unknown guest allocation 0x{pointer:x}")
        self.free_blocks.append((pointer, size))
        C.memset(pointer, 0, min(size, 64))

    def cstring(self, value: str | bytes) -> int:
        data = value.encode() if isinstance(value, str) else bytes(value)
        pointer = self.alloc(len(data) + 1)
        self.write(pointer, data + b"\0")
        return pointer

    # -------------------------------------------------------------------- traps
    def bind(self, name: str, handler, nargs: int = 4, result: str = "u32",
             variadic: bool = False) -> int:
        """Create a trap slot for one guest-callable function; return its address."""
        pointer = self.trap_next
        self.trap_next += 4
        if self.trap_next > self.trap_end:
            raise RuntimeError("trap region exhausted")
        self.write(pointer, _BX_LR)
        self.traps[pointer] = (name, handler, nargs, result, variadic)
        return pointer

    def bind_default(self, name_prefix: str, handler) -> int:
        return self.bind(f"{name_prefix}", handler)

    def args(self, count: int) -> list[int]:
        """Read AAPCS arguments: r0-r3 then stack words."""
        if self._input_buffers is not None:
            values = list(self._input_buffers[: min(count, 4)])
        elif self._uc_reg_read_batch is not None:
            values = list(self.read_registers())
        else:
            values = [self.uc.reg_read(reg) for reg in REGS[: min(count, 4)]]
        if count > 4:
            sp = self.uc.reg_read(UC_ARM_REG_SP)
            extra = self.read(sp, 4 * (count - 4)) if count > 4 else b""
            values += list(struct.unpack(f"<{count - 4}I", extra))
        return values[:count]

    def read_registers(self) -> tuple[int, int, int, int]:
        """r0-r3 in one C call (much cheaper than four ``reg_read``s)."""
        bufs = self._gpr_bufs
        self._uc_reg_read_batch(self._gpr_regs, self._gpr_ptrs, 4)
        return (bufs[0].value, bufs[1].value, bufs[2].value, bufs[3].value)

    def set_register(self, register: int, value: int) -> None:
        """``uc.reg_write`` without the binding's per-call value allocation."""
        if self._uc_reg_write is None:
            self.uc.reg_write(register, value & 0xFFFFFFFF)
            return
        self._reg_buf.value = value & 0xFFFFFFFF
        self._uc_reg_write(register, self._reg_buf_ref)

    def get_register(self, register: int) -> int:
        if self._uc_reg_read_batch is None:
            return self.uc.reg_read(register)
        entry = self._reg_read_cache.get(register)
        if entry is None:
            buf = C.c_uint32()
            entry = ((C.c_int * 1)(register),
                     (C.c_void_p * 1)(C.cast(C.byref(buf), C.c_void_p)),
                     buf)
            self._reg_read_cache[register] = entry
        regs, ptrs, buf = entry
        self._uc_reg_read_batch(regs, ptrs, 1)
        return buf.value & 0xFFFFFFFF

    @property
    def recent_calls(self) -> list[str]:
        """The last guest->host calls, formatted for reports and tools."""
        return [f"{name}(" + ", ".join(hex(a) for a in args) + ")"
                for name, args in self._recent]

    def _trap(self, uc, address, size, user_data):
        try:
            name, handler, nargs, result, variadic = self.traps[address]
            self.last_call = name
            self.calls[name] += 1
            if variadic:
                raw = self.args(12)
                self._recent.append((name, raw))
                value = handler(self, *raw)
                self._write_result(result, value)
            else:
                args = self.args(nargs) if nargs else []
                self._recent.append((name, args))
                value = handler(self, *args)
                self._write_result(result, value)
        except BaseException as error:  # noqa: BLE001 - reported after emu stops
            self.pending_error = error
            uc.emu_stop()
            return

    def _write_result(self, result: str, value) -> None:
        if value is None:
            return
        if result == "u32":
            self.set_register(UC_ARM_REG_R0, value)
        elif result == "u64":
            self.set_register(UC_ARM_REG_R0, value)
            self.set_register(UC_ARM_REG_R1, int(value) >> 32)
        elif result == "f64":
            raw = struct.pack("<d", float(value))
            low, high = struct.unpack("<II", raw)
            self.set_register(UC_ARM_REG_R0, low)
            self.set_register(UC_ARM_REG_R1, high)
        elif result == "void":
            pass
        else:
            raise RuntimeError(f"unknown result kind {result!r}")

    # ------------------------------------------------------------------- errors
    def _invalid_memory(self, uc, access, address, size, value, user_data):
        registers = self.register_dump()
        self.pending_error = GuestError(
            f"invalid guest memory access={access} address=0x{address:x} "
            f"size={size} last_call={self.last_call}\n{registers}")
        uc.emu_stop()

    def _invalid_insn(self, uc, *args):
        pc = uc.reg_read(UC_ARM_REG_PC)
        self.pending_error = GuestError(
            f"invalid instruction PC=0x{pc:x} last_call={self.last_call}\n"
            + self.register_dump())
        uc.emu_stop()

    def register_dump(self) -> str:
        names = ["r0", "r1", "r2", "r3", "r4", "r5", "r6", "r7", "r8", "r9",
                 "r10", "r11", "r12", "sp", "lr", "pc"]
        from unicorn.arm_const import (UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6,
                                       UC_ARM_REG_R7, UC_ARM_REG_R8, UC_ARM_REG_R9,
                                       UC_ARM_REG_R10, UC_ARM_REG_R11, UC_ARM_REG_R12)
        regs = [UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3,
                UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7,
                UC_ARM_REG_R8, UC_ARM_REG_R9, UC_ARM_REG_R10, UC_ARM_REG_R11,
                UC_ARM_REG_R12, UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC]
        values = self.uc.reg_read_batch(regs)
        return "  " + " ".join(f"{n}=0x{v:08x}" for n, v in zip(names, values))

    # --------------------------------------------------------------------- call
    def call(self, address: int, args=(), timeout: float | None = 30.0,
             result: str = "u32"):
        """Call a guest function and return after it branches to the sentinel.

        Supports nesting (a trap handler calling back into guest code): each
        depth gets its own stack frame region below the previous one.

        The ``timeout`` is only handed to the outermost ``emu_start``: passing
        one to a nested run makes Unicorn tear the *outer* emulation loop down
        when the nested run returns (the outer loop quits before the trap hook
        can resume the interrupted guest code).  The outer run's timer stays
        armed while nested, so a hung nested call is still bounded.
        """
        args = list(args)
        saved_sp = self.uc.reg_read(UC_ARM_REG_SP)
        saved_lr = self.uc.reg_read(UC_ARM_REG_LR)
        saved_pending = self.pending_error
        self.pending_error = None
        self._call_depth += 1
        try:
            sp = self.stack_top - 0x1000 - 0x4000 * self._call_depth
            stack_args = args[4:]
            sp -= 4 * len(stack_args)
            sp &= ~0x7
            for i, value in enumerate(stack_args):
                self.w32(sp + 4 * i, int(value) & 0xFFFFFFFF)
            for i, value in enumerate(args[:4]):
                self.uc.reg_write(REGS[i], int(value) & 0xFFFFFFFF)
            self.uc.reg_write(UC_ARM_REG_SP, sp)
            self.uc.reg_write(UC_ARM_REG_LR, self.return_address)
            nested = self._call_depth > 1
            timeout_us = int(timeout * 1e6) if (timeout and not nested) else 0
            try:
                self.uc.emu_start(address, self.return_address,
                                  timeout=timeout_us)
            except UcError as error:
                if self.pending_error is not None:
                    raise GuestError(
                        f"guest service error in {self.last_call}: {self.pending_error}\n"
                        + self.register_dump()) from self.pending_error
                raise GuestError(
                    f"unicorn stopped: {error} PC=0x{self.uc.reg_read(UC_ARM_REG_PC):x} "
                    f"last_call={self.last_call}\n{self.register_dump()}") from error
            if self.pending_error is not None:
                raise GuestError(
                    f"guest service error in {self.last_call}: {self.pending_error}\n"
                    + self.register_dump()) from self.pending_error
            pc = self.uc.reg_read(UC_ARM_REG_PC)
            if pc != self.return_address:
                raise GuestTimeout(
                    f"guest call did not return (timeout or interrupted execution) "
                    f"address=0x{address:x} PC=0x{pc:x} "
                    f"last_call={self.last_call}\n{self.register_dump()}")
            if nested:
                # A guest call made from inside a trap handler suspends the
                # outer emulation loop, and the nested emu_start stops with
                # the PC on the return sentinel.  Put it back on the link
                # register the interrupted code had at trap entry, so the
                # outer loop resumes where the trap stub's "bx lr" would have
                # returned to.
                self.uc.reg_write(UC_ARM_REG_PC, saved_lr)
            if result == "u32":
                return self.uc.reg_read(UC_ARM_REG_R0)
            if result == "u64":
                return (self.uc.reg_read(UC_ARM_REG_R0)
                        | (self.uc.reg_read(UC_ARM_REG_R1) << 32))
            if result == "f64":
                raw = struct.pack("<II", self.uc.reg_read(UC_ARM_REG_R0),
                                  self.uc.reg_read(UC_ARM_REG_R1))
                return struct.unpack("<d", raw)[0]
            return None
        finally:
            self._call_depth -= 1
            self.uc.reg_write(UC_ARM_REG_SP, saved_sp)
            self.uc.reg_write(UC_ARM_REG_LR, saved_lr)
            self.pending_error = saved_pending
            if self._call_depth == 0:
                for callback in tuple(self.after_call):
                    callback()

    # ------------------------------------------------------------------ tracing
    def trace_instructions(self, limit: int, log) -> None:
        """Log the first ``limit`` instructions outside the trap region."""
        self._trace_limit = limit
        self._trace_count = 0

        if self._trace_hook is not None:
            self.uc.hook_del(self._trace_hook)

        def hook(uc, address, size, user_data):
            self._trace_count += 1
            if self._trace_count > limit:
                if self._trace_hook is not None:
                    uc.hook_del(self._trace_hook)
                    self._trace_hook = None
                return
            if not (self.trap_next <= address < self.trap_end):
                log.debug(f"insn {address:#010x}")

        self._trace_hook = self.uc.hook_add(
            UC_HOOK_CODE, hook, begin=self.load_base, end=self.heap_base)
