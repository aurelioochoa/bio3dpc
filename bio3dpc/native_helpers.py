"""Hot AEEHelperFuncs entries as ARM code executed within Unicorn.

A helper call that lands in a trap slot costs 2-10 us of Python per call
(Unicorn's hook callback, argument reads, the handler, the result write).  The
game calls a few trivial helpers far more often than that budget allows: while
a level loads it makes ~225k ``strcmp`` calls (most differ at the first byte)
and ~110k one-byte ``memmove`` calls, and in first-person play it makes ~5k
``GetAppInstance`` calls and ~5k small ``memmove``/``memset`` calls per second.

The routines below replace those table entries with ARM code placed outside
the trap region, so these calls never leave the emulator.  They return what
the Python handlers in ``helper.py`` return, byte for byte:

* ``strcmp`` compares unsigned bytes, returns -1/0/1 and reads NULL as "";
* ``strlen`` returns 0 for NULL;
* ``memmove`` is overlap-safe and returns the destination;
* ``memset`` stores the low byte of its value and returns the destination;
* ``GetAppInstance`` returns the applet word ``HelperTable.applet`` keeps.

Copies and fills too large to beat the host's libc tail-branch to the original
trap slot with their arguments untouched: ``memmove`` above ``MOVE_LIMIT``
bytes (above ``BYTE_LIMIT`` when it must copy byte by byte: unaligned or
overlapping backwards), ``memset`` above ``FILL_LIMIT``.  The guest stores go
through Unicorn, so writes into the module image invalidate translated code as
any guest store does.  Calls that stay native are not counted in
``ArmMachine.calls``.

The machine code is stored pre-assembled, next to its disassembly; the test
suite checks both agree (capstone) and compares every routine against the
Python handler on random inputs.
"""
from __future__ import annotations

import struct

# Native copies beat a trap up to roughly these sizes (aligned 16-byte blocks,
# unaligned byte loops) on Unicorn 2.1; larger ones go to the host.
MOVE_LIMIT = 512
BYTE_LIMIT = 128
FILL_LIMIT = 512

# Placeholder for the tail-branch to the routine's trap slot, patched at
# install time.
_HOST_BRANCH = 0xEA000000

# name: ((word, disassembly at offset 0), ...).  Comments name branch targets.
ROUTINES: dict[str, tuple[tuple[int, str], ...]] = {
    'GetAppInstance': (
        (0xe59f0000, 'ldr r0, [pc]'),             # the applet word follows
        (0xe12fff1e, 'bx lr'),
    ),
    'strlen': (
        (0xe1b01000, 'movs r1, r0'),
        (0x012fff1e, 'bxeq lr'),
        (0xe4d12001, 'ldrb r2, [r1], #1'),        # loop
        (0xe3520000, 'cmp r2, #0'),
        (0x1afffffc, 'bne #8'),
        (0xe0410000, 'sub r0, r1, r0'),
        (0xe2400001, 'sub r0, r0, #1'),
        (0xe12fff1e, 'bx lr'),
    ),
    'strcmp': (
        (0xe3500000, 'cmp r0, #0'),
        (0x028f0030, 'addeq r0, pc, #0x30'),      # NULL -> empty
        (0xe3510000, 'cmp r1, #0'),
        (0x028f1028, 'addeq r1, pc, #0x28'),
        (0xe4d02001, 'ldrb r2, [r0], #1'),        # loop
        (0xe4d13001, 'ldrb r3, [r1], #1'),
        (0xe1520003, 'cmp r2, r3'),
        (0x1a000003, 'bne #0x30'),
        (0xe3520000, 'cmp r2, #0'),
        (0x1afffff9, 'bne #0x10'),
        (0xe3a00000, 'mov r0, #0'),
        (0xe12fff1e, 'bx lr'),
        (0x33e00000, 'mvnlo r0, #0'),             # differ
        (0x83a00001, 'movhi r0, #1'),
        (0xe12fff1e, 'bx lr'),
        (0x00000000, '.word 0'),                  # empty
    ),
    'memmove': (
        (0xe3520c02, 'cmp r2, #0x200'),           # MOVE_LIMIT
        (0x8a00002b, 'bhi #0xb8'),
        (0xe1a0c000, 'mov ip, r0'),
        (0xe0403001, 'sub r3, r0, r1'),
        (0xe1530002, 'cmp r3, r2'),
        (0x3a00001d, 'blo #0x90'),                # destination overlaps the tail
        (0xe1803001, 'orr r3, r0, r1'),
        (0xe3130003, 'tst r3, #3'),
        (0x1a000015, 'bne #0x7c'),
        (0xe2522010, 'subs r2, r2, #0x10'),
        (0x3a000005, 'blo #0x44'),
        (0xe92d0070, 'push {r4, r5, r6}'),
        (0xe8b10078, 'ldm r1!, {r3, r4, r5, r6}'),  # blocks
        (0xe8a00078, 'stm r0!, {r3, r4, r5, r6}'),
        (0xe2522010, 'subs r2, r2, #0x10'),
        (0x2afffffb, 'bhs #0x30'),
        (0xe8bd0070, 'pop {r4, r5, r6}'),
        (0xe292200c, 'adds r2, r2, #0xc'),        # words
        (0x3a000003, 'blo #0x5c'),
        (0xe4913004, 'ldr r3, [r1], #4'),
        (0xe4803004, 'str r3, [r0], #4'),
        (0xe2522004, 'subs r2, r2, #4'),
        (0x2afffffb, 'bhs #0x4c'),
        (0xe2922004, 'adds r2, r2, #4'),          # tail bytes
        (0x0a000003, 'beq #0x74'),
        (0xe4d13001, 'ldrb r3, [r1], #1'),        # forward bytes
        (0xe4c03001, 'strb r3, [r0], #1'),
        (0xe2522001, 'subs r2, r2, #1'),
        (0x1afffffb, 'bne #0x64'),
        (0xe1a0000c, 'mov r0, ip'),               # done
        (0xe12fff1e, 'bx lr'),
        (0xe3520080, 'cmp r2, #0x80'),            # unaligned: BYTE_LIMIT
        (0x8a00000c, 'bhi #0xb8'),
        (0xe3520000, 'cmp r2, #0'),
        (0x1afffff5, 'bne #0x64'),
        (0xe12fff1e, 'bx lr'),
        (0xe3520080, 'cmp r2, #0x80'),            # backward: BYTE_LIMIT
        (0x8a000007, 'bhi #0xb8'),
        (0xe0800002, 'add r0, r0, r2'),
        (0xe0811002, 'add r1, r1, r2'),
        (0xe5713001, 'ldrb r3, [r1, #-1]!'),
        (0xe5603001, 'strb r3, [r0, #-1]!'),
        (0xe2522001, 'subs r2, r2, #1'),
        (0x1afffffb, 'bne #0xa0'),
        (0xe1a0000c, 'mov r0, ip'),
        (0xe12fff1e, 'bx lr'),
        (_HOST_BRANCH, 'b <trap slot>'),          # host
    ),
    'memset': (
        (0xe3520c02, 'cmp r2, #0x200'),           # FILL_LIMIT
        (0x8a000016, 'bhi #0x64'),
        (0xe1a0c000, 'mov ip, r0'),
        (0xe20110ff, 'and r1, r1, #0xff'),
        (0xe3520000, 'cmp r2, #0'),               # align
        (0x0a000010, 'beq #0x5c'),
        (0xe3100003, 'tst r0, #3'),
        (0x0a000002, 'beq #0x2c'),
        (0xe4c01001, 'strb r1, [r0], #1'),
        (0xe2422001, 'sub r2, r2, #1'),
        (0xeafffff8, 'b #0x10'),
        (0xe1811401, 'orr r1, r1, r1, lsl #8'),   # aligned
        (0xe1811801, 'orr r1, r1, r1, lsl #16'),
        (0xe2522004, 'subs r2, r2, #4'),
        (0x3a000002, 'blo #0x48'),
        (0xe4801004, 'str r1, [r0], #4'),         # words
        (0xe2522004, 'subs r2, r2, #4'),
        (0x2afffffc, 'bhs #0x3c'),
        (0xe2922004, 'adds r2, r2, #4'),          # tail bytes
        (0x0a000002, 'beq #0x5c'),
        (0xe4c01001, 'strb r1, [r0], #1'),
        (0xe2522001, 'subs r2, r2, #1'),
        (0x1afffffc, 'bne #0x50'),
        (0xe1a0000c, 'mov r0, ip'),               # done
        (0xe12fff1e, 'bx lr'),
        (_HOST_BRANCH, 'b <trap slot>'),          # host
    ),
}


def _branch(source: int, target: int) -> int:
    """ARM ``b target`` placed at ``source``."""
    offset = target - (source + 8)
    if offset & 3 or not -(1 << 25) <= offset < 1 << 25:
        raise RuntimeError(f'native helper branch out of range: 0x{source:x} -> 0x{target:x}')
    return _HOST_BRANCH | ((offset >> 2) & 0xFFFFFF)


def install(machine, slots: dict[str, int]) -> dict[str, int]:
    """Write the routines; return {name: entry address}.

    ``slots`` maps each name to its trap slot, the target of the routine's
    host fallback.  Returned entries go into the helper table in their place.
    """
    position = machine.base + machine.NATIVE_OFFSET
    end = position + machine.NATIVE_SIZE
    entries = {}
    for name, code in ROUTINES.items():
        words = []
        for index, (word, _) in enumerate(code):
            if word == _HOST_BRANCH:
                word = _branch(position + 4 * index, slots[name])
            words.append(word)
        if name == 'GetAppInstance':
            words.append(0)            # the applet word
        size = 4 * len(words)
        if position + size > end:
            raise RuntimeError('native helper area exhausted')
        machine.write(position, struct.pack(f'<{len(words)}I', *words))
        entries[name] = position
        position += (size + 15) & ~15
    return entries


def applet_word(entries: dict[str, int]) -> int:
    """Guest address of the value the native ``GetAppInstance`` returns."""
    return entries['GetAppInstance'] + 4 * len(ROUTINES['GetAppInstance'])
