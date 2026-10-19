"""The shipped video and real Tk presentation remain local and bounded."""
import json
from pathlib import Path
import shutil
import subprocess
import time

import pytest

from bio3dpc.animation import LoopVideo, LampFlicker
from bio3dpc.bathysphere import ASSETS


def test_video_is_a_seamless_loop_with_visible_motion():
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('Video development verification requires FFmpeg')
    video = ASSETS / 'bathysphere-loop.mp4'
    info = json.loads(subprocess.check_output([
        'ffprobe', '-v', 'error', '-show_entries',
        'format=duration:stream=width,height,nb_frames,r_frame_rate', '-of', 'json', str(video)]))
    stream = info['streams'][0]
    assert (stream['width'], stream['height'], stream['r_frame_rate']) == (1152, 768, '60/1')
    assert float(info['format']['duration']) == 12
    raw = subprocess.check_output([
        'ffmpeg', '-v', 'error', '-i', str(video), '-vf', 'scale=96:64',
        '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1'])
    size = 96 * 64 * 3
    frames = [raw[offset:offset+size] for offset in range(0, len(raw), size)]
    assert len(frames) == 720
    difference = lambda a, b: sum(abs(x-y) for x, y in zip(a, b))/len(a)
    motion = difference(frames[0], frames[360])
    seam = difference(frames[0], frames[-1])
    assert motion > .15, f'No visible animation: {motion}'
    assert seam < motion / 2, f'Visible loop discontinuity: {seam} vs {motion}'
    # Consecutive frames should advance gently, including across the seam.
    steps=[difference(a,b) for a,b in zip(frames,frames[1:])]
    assert max(steps) < motion/2
    assert sum(step>.002 for step in steps)>len(steps)*.9


def test_lamp_flicker_is_irregular_local_and_smooth():
    import random
    lamp=LampFlicker(rng=random.Random(21),now=0)
    events=[]
    samples=[]
    previous=lamp.next_event
    for step in range(90000):
        now=step/1000
        value=lamp.sample(now)
        samples.append(value)
        assert 0 <= value <= 1
        if lamp.next_event!=previous:
            events.append(now)
            previous=lamp.next_event
    gaps=[b-a for a,b in zip(events,events[1:])]
    assert len(events)>5
    assert max(gaps)-min(gaps)>2
    assert min(samples)<.2 and max(samples)>.9
    assert max(abs(a-b) for a,b in zip(samples,samples[1:]))<.08
    assert sum(value<.6 for value in samples)/len(samples)<.12
    other=LampFlicker(rng=random.Random(22),now=0)
    assert other.next_event!=LampFlicker(rng=random.Random(21),now=0).next_event


def test_missing_decoder_or_video_is_a_static_fallback(monkeypatch, tmp_path):
    player = LoopVideo(tmp_path/'missing.mp4')
    assert not player.start()
    monkeypatch.setattr('bio3dpc.animation.shutil.which', lambda name: None)
    player = LoopVideo(ASSETS/'bathysphere-loop.mp4')
    assert not player.start()
    player.stop()
    player.stop()


def test_real_tk_animates_pauses_and_cleans_up(monkeypatch, tmp_path):
    import os
    import tkinter as tk
    from bio3dpc.launcher import Launcher
    try:
        window = tk.Tk()
    except tk.TclError:
        if os.environ.get('BIO3DPC_REQUIRE_TK'):
            raise
        pytest.skip('No display')
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path/'config'))
    app = Launcher(window)
    errors = []
    window.report_callback_exception = lambda *args: errors.append(args)
    try:
        deadline = time.monotonic() + 5
        while app.view.frames_drawn < 3 and time.monotonic() < deadline:
            window.update()
            time.sleep(.01)
        if not shutil.which('ffmpeg'):
            assert app.view.photo is not None
            return
        assert app.view.frames_drawn >= 3
        assert app.view.decoder.frames.qsize() <= 1
        process = app.view.decoder.process
        app.view.pause()
        assert process.poll() is not None and app.view.decoder.process is None
        count = app.view.frames_drawn
        window.update()
        assert app.view.frames_drawn == count
        app.view.animate.set(False)
        app.view.resume()
        assert app.view.decoder.process is None
        app.view.animate.set(True)
        assert app.view.decoder.process is not None
        assert not errors
    finally:
        app.close()
        assert app.view.decoder.process is None
