"""Minimal Standard MIDI File reader (pure Python).

``ISoundPlayer`` needs to know how long a clip runs so the emulated device can
deliver ``AEE_SOUNDPLAYER_DONE`` at the right moment (the game uses that to
sequence its intro).  ffmpeg cannot decode MIDI, so the duration is computed
here from the tempo map, which is all this port needs from a MIDI file.
"""
from __future__ import annotations

# Default MIDI tempo: 120 bpm.
DEFAULT_TEMPO_US = 500_000


class MidiError(ValueError):
    pass


def _vlq(data: bytes, pos: int) -> tuple[int, int]:
    """Read a variable-length quantity; returns (value, next position)."""
    value = 0
    for _ in range(4):
        if pos >= len(data):
            raise MidiError("truncated variable-length quantity")
        byte = data[pos]
        pos += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, pos
    raise MidiError("variable-length quantity too long")


def _track_length(data: bytes, start: int, end: int) -> tuple[int, list[tuple[int, int]]]:
    """Total ticks of one MTrk chunk plus its (tick, tempo) meta events."""
    pos = start
    tick = 0
    running = 0
    tempos: list[tuple[int, int]] = []
    while pos < end:
        delta, pos = _vlq(data, pos)
        tick += delta
        if pos >= end:
            break
        status = data[pos]
        if status < 0x80:
            if not running:
                raise MidiError("running status without a status byte")
            status = running  # data byte already at `pos`
        else:
            pos += 1
            if status < 0xF0:
                running = status
        if status == 0xFF:
            if pos >= end:
                break
            meta_type = data[pos]
            pos += 1
            length, pos = _vlq(data, pos)
            if meta_type == 0x51 and length == 3 and pos + 3 <= end:
                tempo = (data[pos] << 16) | (data[pos + 1] << 8) | data[pos + 2]
                if tempo > 0:
                    tempos.append((tick, tempo))
            if meta_type == 0x2F:  # end of track
                return tick, tempos
            pos += length
        elif status in (0xF0, 0xF7):
            length, pos = _vlq(data, pos)
            pos += length
        else:
            high = status & 0xF0
            pos += 1 if high in (0xC0, 0xD0) else 2
    return tick, tempos


class MidiInfo:
    """What the port needs to know about a MIDI source."""

    def __init__(self, fmt: int, tracks: int, division: int,
                 ticks: int, seconds: float):
        self.format = fmt
        self.tracks = tracks
        self.division = division
        self.ticks = ticks
        self.seconds = seconds

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (f"MidiInfo(format={self.format}, tracks={self.tracks}, "
                f"ticks={self.ticks}, seconds={self.seconds:.3f})")


def parse(data: bytes) -> MidiInfo:
    """Parse a Standard MIDI File and compute its playing time."""
    if len(data) < 14 or data[:4] != b"MThd":
        raise MidiError("not a Standard MIDI File")
    header_length = int.from_bytes(data[4:8], "big")
    if header_length < 6 or 8 + header_length > len(data):
        raise MidiError("bad MThd chunk length")
    fmt = int.from_bytes(data[8:10], "big")
    count = int.from_bytes(data[10:12], "big")
    division = int.from_bytes(data[12:14], "big")

    # Collect each MTrk chunk inside the header's declared length.
    pos = 8 + header_length
    tracks: list[tuple[int, int]] = []
    while pos + 8 <= len(data):
        kind = data[pos:pos + 4]
        length = int.from_bytes(data[pos + 4:pos + 8], "big")
        start, end = pos + 8, pos + 8 + length
        if end > len(data):
            break
        if kind == b"MTrk":
            tracks.append((start, end))
        pos = end

    if not tracks:
        raise MidiError("no MTrk chunks")

    total_ticks = 0
    # (tick, precedence, tempo): the default tempo sorts before any real event
    # at the same tick, so a file that sets the tempo at tick 0 wins.
    tempos: list[tuple[int, int, int]] = [(0, 0, DEFAULT_TEMPO_US)]
    for start, end in tracks:
        ticks, track_tempos = _track_length(data, start, end)
        total_ticks = max(total_ticks, ticks)
        for order, (tick, tempo) in enumerate(track_tempos, start=1):
            tempos.append((tick, order, tempo))

    if division & 0x8000:
        # SMPTE: high byte is negative frames/second, low byte ticks/frame.
        frames = 256 - (division >> 8)
        ticks_per_frame = division & 0xFF
        ticks_per_second = frames * max(1, ticks_per_frame)
        seconds = total_ticks / ticks_per_second
    else:
        ticks_per_quarter = max(1, division & 0x7FFF)
        tempos.sort()
        seconds = 0.0
        previous_tick = 0
        tempo = DEFAULT_TEMPO_US
        for tick, _order, value in tempos:
            tick = min(tick, total_ticks)
            if tick > previous_tick:
                seconds += ((tick - previous_tick) * tempo) / 1e6 / ticks_per_quarter
                previous_tick = tick
            tempo = value
        if total_ticks > previous_tick:
            seconds += ((total_ticks - previous_tick) * tempo) / 1e6 / ticks_per_quarter

    return MidiInfo(fmt, count, division, total_ticks, seconds)
