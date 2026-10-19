"""Unicorn ARM32 machine tests: trap dispatch, AAPCS arguments, faults, heap.

Hand-encoded ARM (A32) words only -- keystone is not installed.  Snippets that
call a trap save LR first (``push {lr}``) because a ``bl`` overwrites the LR
that ``ArmMachine.call()`` uses as its sentinel; the trap's ``bx lr`` then
returns into the snippet, which restores LR and exits.

``ArmMachine.write()`` memmoves straight into the shared mapping instead of
going through ``uc.mem_write()``, so Unicorn never invalidates translated
blocks: rewriting an address that has already executed would silently keep
running the old code.  Each snippet is therefore written to a fresh address
by ``emit()`` and never patched in place; see the xfail test at the bottom
for the underlying limitation.
"""
from __future__ import annotations

import io
import struct

import pytest

from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1

from bio3dpc.log import RunLog
from bio3dpc.machine import ArmMachine, GuestError, GuestTimeout

# ------------------------------------------------------------------ encodings
PUSH_LR = 0xE92D4000      # push {lr}
POP_PC = 0xE8BD8000       # pop {pc}
BX_LR = 0xE12FFF1E        # bx lr
MOV_R0_5 = 0xE3A00005     # mov r0, #5
MOV_R1_7 = 0xE3A01007     # mov r1, #7
MOV_R0_42 = 0xE3A0002A    # mov r0, #0x2a
MOV_R0_11 = 0xE3A0000B    # mov r0, #11
MOV_R0_9 = 0xE3A00009     # mov r0, #9
MOV_R1_22 = 0xE3A01016    # mov r1, #22
MOV_R2_33 = 0xE3A02021    # mov r2, #33
MOV_R3_44 = 0xE3A0302C    # mov r3, #44
MOV_R4_LR = 0xE1A0400E    # mov r4, lr
BX_R4 = 0xE12FFF14        # bx r4
LDR_R0_0 = 0xE5900000     # ldr r0, [r0]

BX_LR_STUB = b"\x1e\xff\x2f\xe1"  # binding a trap writes this 4-byte stub

SNIPPET_GAP = 0x100       # keep snippets far apart; nothing is ever rewritten


@pytest.fixture(scope="module")
def machine():
    machine = ArmMachine(log=RunLog(level="error", stream=io.StringIO()))
    yield machine
    machine.close()


def emit(machine: ArmMachine, words: list[int]) -> int:
    """Write a snippet at an address this machine has never executed."""
    address = getattr(machine, "_bio3dpc_test_code", machine.load_base + 0x100)
    for index, word in enumerate(words):
        machine.w32(address + 4 * index, word)
    machine._bio3dpc_test_code = address + SNIPPET_GAP
    return address


def bl_word(code: int, index: int, target: int) -> int:
    """Encode ``bl target`` at ``code + 4*index``: target = pc + 8 + imm24*4."""
    offset = (target - (code + 4 * index + 8)) >> 2
    return 0xEB000000 | (offset & 0xFFFFFF)


@pytest.fixture()
def fresh():
    machine = ArmMachine(log=RunLog(level="error", stream=io.StringIO()))
    yield machine
    machine.close()


# ---------------------------------------------------------------------- traps
def test_trap_dispatch_round_trip(machine):
    seen = {}

    def adder(m, a, b):
        seen["args"] = (a, b)
        return 0xDEADBEEF

    trap = machine.bind("test_adder", adder, nargs=2, result="u32")
    assert machine.read(trap, 4) == BX_LR_STUB

    # push {lr}; mov r0, #5; mov r1, #7; bl trap; pop {pc}
    code = emit(machine, [PUSH_LR, MOV_R0_5, MOV_R1_7, 0, POP_PC])
    machine.w32(code + 12, bl_word(code, 3, trap))

    before = machine.calls["test_adder"]
    assert machine.call(code, timeout=5.0) == 0xDEADBEEF
    assert seen["args"] == (5, 7)
    assert machine.calls["test_adder"] == before + 1
    assert machine.last_call == "test_adder"
    assert machine.recent_calls[-1] == "test_adder(0x5, 0x7)"


def test_trap_reads_stack_arguments(machine):
    seen = {}

    def six(m, *args):
        seen["args"] = args
        seen["again"] = m.args(6)
        return 0

    trap = machine.bind("test_six", six, nargs=6)

    # The caller puts args 5-6 on the stack; the snippet forwards them
    # untouched (bl changes no stack memory) and returns via r4.
    code = emit(machine, [MOV_R0_11, MOV_R1_22, MOV_R2_33, MOV_R3_44,
                          MOV_R4_LR, 0, BX_R4])
    machine.w32(code + 20, bl_word(code, 5, trap))

    result = machine.call(code, [11, 22, 33, 44, 55, 66], timeout=5.0)
    assert result == 0
    assert seen["args"] == (11, 22, 33, 44, 55, 66)
    assert seen["again"] == [11, 22, 33, 44, 55, 66]


def test_void_result_leaves_r0_untouched(machine):
    trap = machine.bind("test_void", lambda m, a: 999, nargs=1, result="void")

    code = emit(machine, [PUSH_LR, MOV_R0_42, 0, POP_PC])
    machine.w32(code + 8, bl_word(code, 2, trap))

    assert machine.call(code, timeout=5.0) == 0x2A


def test_u64_result_writes_r0_and_r1(machine):
    trap = machine.bind("test_u64", lambda m, a: 0x1122334455667788,
                        nargs=1, result="u64")

    code = emit(machine, [PUSH_LR, 0, POP_PC])
    machine.w32(code + 4, bl_word(code, 1, trap))

    assert machine.call(code, timeout=5.0, result="u64") == 0x1122334455667788
    assert machine.uc.reg_read(UC_ARM_REG_R0) == 0x55667788
    assert machine.uc.reg_read(UC_ARM_REG_R1) == 0x11223344


def test_nested_call_returns_to_guest_caller(machine):
    """A handler that re-enters the guest must not strand the outer call on
    the return sentinel: code after the ``bl trap`` has to keep running."""
    inner = emit(machine, [PUSH_LR, MOV_R0_5, POP_PC])

    def handler(machine_, unused):
        assert machine_.call(inner, [], timeout=5.0) == 5
        return 7

    trap = machine.bind("test_nested", handler, nargs=1)
    code = emit(machine, [PUSH_LR, MOV_R0_11, 0, MOV_R0_9, POP_PC])
    machine.w32(code + 8, bl_word(code, 2, trap))

    # 9 proves execution resumed after the trap; 7 would mean the outer
    # emu_start stopped at the sentinel.
    assert machine.call(code, [0], timeout=5.0) == 9


# --------------------------------------------------------------------- faults
def test_invalid_guest_memory_access_raises_guest_error(machine):
    # Low addresses are device RAM (see ArmMachine.DEVICE_RAM_SIZE), so use an
    # address outside both guest regions to trigger the fault.
    code = emit(machine, [LDR_R0_0, BX_LR])  # ldr r0, [r0]

    with pytest.raises(GuestError) as info:
        machine.call(code, [0xDEADBEEF], timeout=5.0)

    message = str(info.value)
    assert "invalid guest memory access" in message
    assert "address=0xdeadbeef" in message


def test_timeout_does_not_return_stale_r0_and_machine_can_resume(machine):
    loop = emit(machine, [0xEAFFFFFE])  # b .
    with pytest.raises(GuestTimeout, match="did not return"):
        machine.call(loop, [123], timeout=0.01)
    assert machine._call_depth == 0
    assert machine.call(emit(machine, [MOV_R0_42, BX_LR])) == 42


def test_nested_timeout_is_detected_before_restoring_caller_pc(machine):
    loop = emit(machine, [0xEAFFFFFE])
    trap = machine.bind("test_nested_timeout",
                        lambda m: m.call(loop, timeout=5.0), nargs=0)
    with pytest.raises(GuestError, match="did not return"):
        machine.call(trap, [123], timeout=0.01)
    assert machine._call_depth == 0
    assert machine.call(emit(machine, [MOV_R0_9, BX_LR])) == 9


def test_explicit_emu_stop_is_not_a_guest_return(machine):
    def stop(m):
        m.uc.emu_stop()
        return 123
    trap = machine.bind("test_stop", stop, nargs=0)
    with pytest.raises(GuestTimeout, match="interrupted execution"):
        machine.call(trap)


def test_low_addresses_behave_like_device_ram(machine):
    # BREW binaries read garbage "pointers" that land in the phone's low
    # address space; those must not fault on the host either.
    code = emit(machine, [LDR_R0_0, BX_LR])  # ldr r0, [r0]

    assert machine.call(code, [0x637469], timeout=5.0) == 0
    assert machine.read(0x637469, 4) == bytes(4)
    assert machine.cstring_bytes(0x637469) == b""
    machine.write(0x637469, b"\x01\x02")
    assert machine.read(0x637469, 2) == b"\x01\x02"


# ------------------------------------------------------------------ allocator
def test_alloc_returns_zeroed_aligned_distinct_blocks(fresh):
    machine = fresh
    first = machine.alloc(20)
    second = machine.alloc(1)

    assert first and second
    assert first != second
    assert first % 16 == 0 and second % 16 == 0
    assert machine.heap_base <= first < machine.heap_end
    assert machine.read(first, 32) == bytes(32)
    assert machine.read(second, 16) == bytes(16)

    assert machine.alloc(0) == 0
    assert machine.alloc(-1) == 0


def test_free_then_alloc_reuses_same_block_zeroed(fresh):
    machine = fresh
    block = machine.alloc(24)
    machine.write(block, b"\xff" * 32)
    machine.free(block)
    machine.write(block, b"\xaa" * 32)  # dirty the freed block again

    reused = machine.alloc(16)
    assert reused == block
    assert machine.read(reused, 16) == bytes(16)


# ------------------------------------------------------------- code rewriting
def test_rewritten_guest_code_is_reobserved(machine):
    code = emit(machine, [PUSH_LR, 0xE3A00001, POP_PC])  # mov r0, #1
    assert machine.call(code, timeout=5.0) == 1

    machine.w32(code + 4, 0xE3A00002)  # mov r0, #2
    assert machine.read(code + 4, 4) == struct.pack("<I", 0xE3A00002)

    assert machine.call(code, timeout=5.0) == 2
