"""Native helper routines must match the Python handlers they replace."""
from __future__ import annotations

import io
import random
import struct

import pytest
from capstone import CS_ARCH_ARM, CS_MODE_ARM, Cs

from bio3dpc import native_helpers
from bio3dpc.helper import HELPER_FIELDS, HelperTable
from bio3dpc.log import RunLog
from bio3dpc.machine import ArmMachine

ARENA = 4096


def environment(native):
    machine = ArmMachine(log=RunLog(level="error", stream=io.StringIO()))
    helper = HelperTable(machine, RunLog(level="error", stream=io.StringIO()))
    table = helper.build(native=native)
    return machine, helper, table


@pytest.fixture(scope="module")
def pair():
    """(reference, native): Python handlers only, and the shipped table."""
    reference, native = environment(False), environment(True)
    for machine, helper, _ in (reference, native):
        helper.applet = 0x0109005B
        machine.arena = machine.alloc(ARENA)
    yield reference, native
    for machine, _, _ in (reference, native):
        machine.close()


def entry(machine, table, name):
    return machine.u32(table + 4 * HELPER_FIELDS.index(name))


def test_listing_matches_machine_code():
    disassembler = Cs(CS_ARCH_ARM, CS_MODE_ARM)
    for name, code in native_helpers.ROUTINES.items():
        for index, (word, text) in enumerate(code):
            if text in ('.word 0', 'b <trap slot>'):
                continue
            instruction = next(disassembler.disasm(struct.pack('<I', word), 4 * index))
            assert f'{instruction.mnemonic} {instruction.op_str}'.strip() == text, (name, index)


def test_limits_match_the_encoded_comparisons():
    memmove = dict((text, word) for word, text in native_helpers.ROUTINES['memmove'])
    memset = dict((text, word) for word, text in native_helpers.ROUTINES['memset'])
    assert f'cmp r2, #{native_helpers.MOVE_LIMIT:#x}' in memmove
    assert f'cmp r2, #{native_helpers.BYTE_LIMIT:#x}' in memmove
    assert f'cmp r2, #{native_helpers.FILL_LIMIT:#x}' in memset


def test_routines_match_python_handlers_on_random_inputs(pair):
    rng = random.Random(1)
    sizes = (0, 1, 2, 3, 4, 5, 7, 15, 16, 17, 31, 64, 100, 127, 128, 129,
             255, 256, 300, 511, 512, 513, 1024)
    for case in range(3000):
        # Skewed bytes give equal prefixes, high bytes and early terminators.
        data = bytes(rng.choice((0, 1, 65, 128, 255)) if rng.random() < .3
                     else rng.randrange(256) for _ in range(ARENA))
        name = rng.choice(('memmove', 'memset', 'strcmp', 'strlen', 'GetAppInstance'))
        a, b, size = rng.randrange(1500), rng.randrange(1500), rng.choice(sizes)
        value = rng.randrange(-512, 1024)
        results = []
        for machine, _, table in pair:
            machine.write(machine.arena, data)
            first, second = machine.arena + a, machine.arena + b
            args = {'memmove': [first, second, size],
                    'memset': [first, value, size],
                    'strcmp': [first if a % 7 else 0, second if b % 5 else 0],
                    'strlen': [first if a % 9 else 0],
                    'GetAppInstance': []}[name]
            result = machine.call(entry(machine, table, name), args, timeout=5.0)
            results.append((result - machine.arena if name in ('memmove', 'memset') else result,
                            machine.read(machine.arena, ARENA)))
        assert results[0] == results[1], (case, name, a, b, size)


def test_large_copies_and_fills_use_the_host(pair):
    machine, _, table = pair[1]
    source, target = machine.arena, machine.arena + 2048
    for name, args, host in (
            ('memmove', [target, source, native_helpers.MOVE_LIMIT + 1], True),
            ('memmove', [target, source, native_helpers.MOVE_LIMIT], False),
            ('memmove', [target + 1, source, native_helpers.BYTE_LIMIT + 1], True),
            ('memmove', [target + 1, source, native_helpers.BYTE_LIMIT], False),
            ('memmove', [source + 1, source, native_helpers.BYTE_LIMIT + 1], True),   # backward
            ('memset', [target, 0x5A, native_helpers.FILL_LIMIT + 1], True),
            ('memset', [target, 0x5A, native_helpers.FILL_LIMIT], False)):
        before = machine.calls['helper::' + name]
        assert machine.call(entry(machine, table, name), args, timeout=5.0) == args[0]
        assert machine.calls['helper::' + name] - before == int(host), args


def test_native_copy_into_code_invalidates_translations(pair):
    """Guest stores replace already-translated code, like the game's own patches."""
    machine, _, table = pair[1]
    code = machine.load_base
    machine.code_start, machine.code_end = code, code + 0x100
    try:
        machine.write(code, struct.pack('<2I', 0xE3A00001, 0xE12FFF1E))    # mov r0, #1; bx lr
        assert machine.call(code, [], timeout=5.0) == 1
        patch = machine.arena
        machine.write(patch, struct.pack('<I', 0xE3A00002))                 # mov r0, #2
        machine.call(entry(machine, table, 'memmove'), [code, patch, 4], timeout=5.0)
        assert machine.call(code, [], timeout=5.0) == 2
    finally:
        machine.code_start = machine.code_end = 0


def test_get_app_instance_follows_applet_before_and_after_build():
    machine = ArmMachine(log=RunLog(level="error", stream=io.StringIO()))
    try:
        helper = HelperTable(machine, RunLog(level="error", stream=io.StringIO()))
        helper.applet = 0x1234
        table = helper.build()
        function = entry(machine, table, 'GetAppInstance')
        assert machine.call(function, [], timeout=5.0) == 0x1234
        helper.applet = 0xCAFE0
        assert machine.call(function, [], timeout=5.0) == 0xCAFE0
        assert machine.calls['helper::GetAppInstance'] == 0
    finally:
        machine.close()
