"""AEEHelperFuncs table tests: field layout, trap wiring, real helper calls."""
from __future__ import annotations

import io

import pytest

from bio3dpc.helper import HELPER_FIELDS, HelperTable
from bio3dpc.log import RunLog
from bio3dpc.machine import ArmMachine
from bio3dpc.native_helpers import ROUTINES as NATIVE_ROUTINES

BX_LR_STUB = b"\x1e\xff\x2f\xe1"


@pytest.fixture(scope="module")
def helper_env():
    """One machine with a fully built helper table, shared by the module."""
    machine = ArmMachine(log=RunLog(level="error", stream=io.StringIO()))
    helper = HelperTable(machine, RunLog(level="error", stream=io.StringIO()))
    table = helper.build()
    yield machine, helper, table
    machine.close()


def slot(machine: ArmMachine, table: int, name: str) -> int:
    """Address of the trap slot for one helper-table field."""
    return machine.u32(table + 4 * HELPER_FIELDS.index(name))


# --------------------------------------------------------------------- layout
def test_helper_fields_contract():
    assert len(HELPER_FIELDS) == 117
    assert HELPER_FIELDS[47] == "sysfree"
    assert HELPER_FIELDS[48] == "GetAppInstance"
    assert len(set(HELPER_FIELDS)) == len(HELPER_FIELDS)
    assert all(isinstance(name, str) and name for name in HELPER_FIELDS)


def test_helper_table_build_binds_every_field(helper_env):
    machine, helper, table = helper_env

    assert table != 0
    assert table % 16 == 0
    assert machine.contains(table, 4 * len(HELPER_FIELDS))

    slots = [machine.u32(table + 4 * index)
             for index in range(len(HELPER_FIELDS))]
    assert all(slots)
    assert len(set(slots)) == len(HELPER_FIELDS)

    trap_lo = machine.base + machine.TRAP_OFFSET
    native_lo = machine.base + machine.NATIVE_OFFSET
    for name, entry in zip(HELPER_FIELDS, slots):
        if name in NATIVE_ROUTINES:
            assert native_lo <= entry < native_lo + machine.NATIVE_SIZE
        else:
            assert trap_lo <= entry < machine.trap_end
            assert machine.read(entry, 4) == BX_LR_STUB


# ---------------------------------------------------------------------- calls
def test_helper_malloc_and_strlen_through_table(helper_env):
    machine, helper, table = helper_env

    pointer = machine.call(slot(machine, table, "malloc"), [64], timeout=5.0)
    assert pointer != 0
    assert pointer % 16 == 0
    assert machine.heap_base <= pointer < machine.heap_end
    assert machine.read(pointer, 64) == bytes(64)

    text = machine.cstring("hello world")
    assert machine.call(slot(machine, table, "strlen"), [text], timeout=5.0) == 11


def test_helper_get_app_instance_returns_bound_applet(helper_env):
    machine, helper, table = helper_env
    trap = slot(machine, table, "GetAppInstance")

    helper.applet = 0x0109005B
    assert machine.call(trap, [], timeout=5.0) == 0x0109005B

    helper.applet = 0x12345678
    assert machine.call(trap, [], timeout=5.0) == 0x12345678


def test_helper_memmove_keeps_memmove_semantics(helper_env):
    """The host-side copy must overlap-safe like memmove, not memcpy."""
    machine, helper, table = helper_env
    trap = slot(machine, table, "memmove")

    src = machine.alloc(32)
    dst = machine.alloc(32)
    machine.write(src, bytes(range(32)))
    assert machine.call(trap, [dst, src, 32], timeout=5.0) == dst
    assert machine.read(dst, 32) == bytes(range(32))

    machine.write(src, b"abcdefgh")
    machine.call(trap, [src + 2, src, 6], timeout=5.0)
    assert machine.read(src, 8) == b"ababcdef"


def test_helper_memset_fills_shared_and_device_memory(helper_env):
    machine, helper, table = helper_env
    trap = slot(machine, table, "memset")

    dst = machine.alloc(16)
    machine.write(dst, b"\x11" * 16)
    assert machine.call(trap, [dst, 0x22, 16], timeout=5.0) == dst
    assert machine.read(dst, 16) == b"\x22" * 16

    # Device RAM has no host mapping: the helper must fall back to Unicorn.
    assert machine.device_end > 0
    machine.call(trap, [0x1000, 0x33, 8], timeout=5.0)
    assert machine.read(0x1000, 8) == b"\x33" * 8


def test_helper_memmove_between_shared_and_device_memory(helper_env):
    """Mixed copies fall back to Unicorn for the end without a host mapping."""
    machine, helper, table = helper_env
    trap = slot(machine, table, "memmove")

    shared = machine.alloc(16)
    machine.write(shared, b"shared!!")
    machine.call(trap, [shared + 8, shared, 8], timeout=5.0)   # overlapping
    assert machine.read(shared, 16) == b"shared!!shared!!"

    machine.call(trap, [0x3000, shared, 8], timeout=5.0)       # shared -> device
    assert machine.read(0x3000, 8) == b"shared!!"

    machine.write(0x3000, b"device!!")
    machine.call(trap, [shared, 0x3000, 8], timeout=5.0)       # device -> shared
    assert machine.read(shared, 8) == b"device!!"
