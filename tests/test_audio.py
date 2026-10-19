"""Silent MIDI still supplies timing, without starting external programs."""
import io
import struct

import pytest

from bio3dpc.audio import AudioEngine
from bio3dpc.log import RunLog


@pytest.mark.parametrize('file_source', [False, True])
def test_disabled_midi_keeps_header_and_duration_without_synth(monkeypatch, tmp_path,
                                                              file_source):
    monkeypatch.setenv('BIO3DPC_AUDIO', '0')
    engine = AudioEngine(RunLog(level='error', stream=io.StringIO()))
    # Even when every external dependency exists, silence must skip synthesis.
    engine.synth = engine.player = engine.soundfont = 'available'
    def no_process(*args, **kwargs):
        pytest.fail('silent playback launched a subprocess')
    monkeypatch.setattr('bio3dpc.audio.subprocess.run', no_process)
    events = b'\x00\x90\x3c\x40\x83\x60\xff\x2f\x00'
    data = (b'MThd' + struct.pack('>IHHH', 6, 0, 1, 480)
            + b'MTrk' + struct.pack('>I', len(events)) + events)
    if file_source:
        path = tmp_path / 'music.mid'
        path.write_bytes(data)
        clip = engine.decode_path(str(path))
    else:
        clip = engine.decode_bytes(data)
    assert clip is not None and clip.kind == 'midi'
    assert clip.path is None and clip.seconds == pytest.approx(0.5)
    assert engine._tempdir is None
