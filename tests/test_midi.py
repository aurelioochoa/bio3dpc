"""Tests for the pure-Python Standard MIDI File reader."""
from __future__ import annotations

import struct

import pytest

from bio3dpc import midi


def vlq(value: int) -> bytes:
    out = bytearray([value & 0x7F])
    value >>= 7
    while value:
        out.insert(0, 0x80 | (value & 0x7F))
        value >>= 7
    return bytes(out)


def event(delta: int, payload: bytes) -> bytes:
    """One MIDI event: variable-length delta followed by status + data."""
    return vlq(delta) + payload


def track(events: bytes) -> bytes:
    return b"MTrk" + struct.pack(">I", len(events)) + events


def smf(division: int, tracks: list[bytes], fmt: int = 0) -> bytes:
    header = struct.pack(">HHH", fmt, len(tracks), division)
    return b"MThd" + struct.pack(">I", 6) + header + b"".join(tracks)


NOTE_ON = b"\x90\x3c\x40"
NOTE_OFF = b"\x80\x3c\x40"
END_OF_TRACK = b"\xff\x2f\x00"


def tempo_event(us_per_quarter: int) -> bytes:
    return b"\xff\x51\x03" + struct.pack(">I", us_per_quarter)[1:]


def test_default_tempo_duration() -> None:
    # 960 ticks/quarter; note held 1920 ticks -> two quarters -> 1.0 s at 120 bpm.
    events = (event(0, NOTE_ON) + event(1920, NOTE_OFF)
              + event(0, END_OF_TRACK))
    info = midi.parse(smf(960, [track(events)]))
    assert info.ticks == 1920
    assert info.seconds == pytest.approx(1.0)


def test_tempo_change_is_applied() -> None:
    # 60 bpm (1 s per quarter) from the start: 960 ticks -> 1.0 s.
    events = (event(0, tempo_event(1_000_000)) + event(960, NOTE_ON)
              + event(0, END_OF_TRACK))
    info = midi.parse(smf(960, [track(events)]))
    assert info.seconds == pytest.approx(1.0)


def test_tempo_change_mid_track() -> None:
    # 480 of 960 ticks at 120 bpm (0.25 s), then 480 ticks at 60 bpm (0.5 s).
    events = (event(0, tempo_event(500_000)) + event(480, tempo_event(1_000_000))
              + event(480, NOTE_ON) + event(0, END_OF_TRACK))
    info = midi.parse(smf(960, [track(events)]))
    assert info.seconds == pytest.approx(0.75)


def test_longest_track_wins() -> None:
    short = event(0, NOTE_ON) + event(480, END_OF_TRACK)
    long_track = event(0, NOTE_ON) + event(1920, END_OF_TRACK)
    info = midi.parse(smf(960, [track(short), track(long_track)], fmt=1))
    assert info.tracks == 2
    assert info.seconds == pytest.approx(1.0)


def test_tempo_at_tick_zero_beats_the_default_tempo() -> None:
    """A tempo track that sets the beat at tick 0 must override 120 bpm.

    This is what the game's music does (461538 us per quarter = 130 bpm); the
    default tempo sorts at the same tick, so the real event has to win.
    """
    conductor = event(0, tempo_event(461_538)) + event(0, END_OF_TRACK)
    voice = event(0, NOTE_ON) + event(960, END_OF_TRACK)
    info = midi.parse(smf(960, [track(conductor), track(voice)], fmt=1))
    assert info.seconds == pytest.approx(0.461538, abs=1e-6)


def test_running_status() -> None:
    # The second note repeats the 0x90 status byte (running status).
    events = (event(0, NOTE_ON) + event(960, b"\x3c\x40")
              + event(960, NOTE_OFF) + event(0, END_OF_TRACK))
    info = midi.parse(smf(960, [track(events)]))
    assert info.seconds == pytest.approx(1.0)


def test_program_change_has_one_data_byte() -> None:
    # 0xC0 (program change) takes a single data byte; treating it as two
    # would desynchronise the whole track.
    events = (event(0, b"\xc0\x05") + event(960, NOTE_ON)
              + event(960, NOTE_OFF) + event(0, END_OF_TRACK))
    info = midi.parse(smf(960, [track(events)]))
    assert info.seconds == pytest.approx(1.0)


def test_smpte_division() -> None:
    # 25 frames/s, 4 ticks per frame -> 100 ticks per second.
    division = ((256 - 25) << 8) | 4
    events = event(0, NOTE_ON) + event(200, NOTE_OFF) + event(0, END_OF_TRACK)
    info = midi.parse(smf(division, [track(events)]))
    assert info.seconds == pytest.approx(2.0)


def test_rejects_non_midi() -> None:
    with pytest.raises(midi.MidiError):
        midi.parse(b"RIFF" + bytes(32))


def test_rejects_truncated_file() -> None:
    with pytest.raises(midi.MidiError):
        midi.parse(b"MThd")
