"""Host audio output for ISoundPlayer.

BREW hands the emulated app sound sources as raw buffers (QCP/QCELP voices and
Standard MIDI music in this game) or EFS file names.  The host decodes them --
ffmpeg for QCP, an optional SoundFont synth for MIDI -- and plays them through
PulseAudio (paplay).  Playback is asynchronous: the sound player schedules the
guest's PLAY_CB/AEE_SOUNDPLAYER_DONE status from the clip's real duration,
which is what the game uses to sequence its audio.

Everything degrades gracefully: without ffmpeg/paplay the emulation still runs
silently, it just reports the clip length (MIDI duration is parsed in Python).
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import threading
import wave

from . import midi as midi_module

# Paplay volume scale (0..65536); BREW volume is 0..100.
PA_VOLUME_MAX = 65536

# Estimated clip length when the source cannot be decoded, so games that wait
# for AEE_SOUNDPLAYER_DONE still make progress.
FALLBACK_SECONDS = 1.5

# Common SoundFont locations, used only when BIO3DPC_SOUNDFONT is not set.
SOUNDFONT_GLOBS = (
    "/usr/share/soundfonts/*.sf2",
    "/usr/share/sounds/sf2/*.sf2",
    "/usr/local/share/soundfonts/*.sf2",
    "~/.local/share/soundfonts/*.sf2",
)


class Clip:
    """A decoded sound ready to play (``path`` is None for silent clips)."""

    def __init__(self, path: str | None, seconds: float, kind: str = "pcm"):
        self.path = path
        self.seconds = seconds
        self.kind = kind

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Clip({self.kind}, {self.path}, {self.seconds:.3f}s)"


def find_soundfont() -> str | None:
    """First usable SoundFont, or None (MIDI then plays silently)."""
    override = os.environ.get("BIO3DPC_SOUNDFONT")
    if override:
        return override if os.path.exists(override) else None
    import glob
    for pattern in SOUNDFONT_GLOBS:
        for path in sorted(glob.glob(os.path.expanduser(pattern))):
            return path
    return None


class AudioEngine:
    def __init__(self, log, data_dir: str | None = None, enabled: bool = True):
        self.log = log
        self.data_dir = data_dir
        self.decoder = shutil.which("ffmpeg")
        self.player = shutil.which("paplay")
        self.synth = shutil.which("fluidsynth")
        self.soundfont = find_soundfont() if self.synth else None
        requested = os.environ.get("BIO3DPC_AUDIO", "1").strip().lower()
        self.enabled = (enabled and bool(self.decoder)
                        and requested not in ("0", "off", "no", "none"))
        self.warned_player = False
        self._tempdir: str | None = None
        self.clips: dict[str, Clip] = {}
        self.lock = threading.Lock()
        self.playing: list[subprocess.Popen] = []
        if not self.enabled:
            self.log.info("audio output disabled")
        elif not self.player:
            self.log.warn("audio output disabled (paplay not found)")
        if self.enabled and not self.soundfont:
            self.log.info("no SoundFont found: MIDI plays silently "
                          "(set BIO3DPC_SOUNDFONT to a .sf2 file for music)")

    # --------------------------------------------------------------- staging
    def stage_dir(self) -> str:
        """Scratch directory for decoded clips, created on first use.

        Runtimes that never decode a sound (tests, headless boots) leave nothing
        behind.
        """
        if self._tempdir is None:
            self._tempdir = tempfile.mkdtemp(prefix="bio3dpc-audio-")
        return self._tempdir

    # ------------------------------------------------------------------ decode
    def decode_bytes(self, data: bytes, hint: str = "") -> Clip | None:
        if not data:
            return None
        key = hashlib.sha1(data).hexdigest()
        if key in self.clips:
            return self.clips[key]
        clip: Clip | None = None
        if data[:4] == b"MThd":
            # MIDI needs no external decoder for its duration.
            clip = self._decode_midi(data, key, hint)
        elif self.enabled:
            source = os.path.join(self.stage_dir(), f"{key[:12]}_in.bin")
            target = os.path.join(self.stage_dir(), f"{key[:12]}.wav")
            try:
                with open(source, "wb") as handle:
                    handle.write(data)
            except OSError as error:
                self.log.warn(f"audio: cannot stage buffer: {error}")
                return None
            clip = self._decode_file(source, target, hint)
        self.clips[key] = clip  # cache misses too (None) to avoid retry storms
        return clip

    def decode_path(self, path: str, hint: str = "") -> Clip | None:
        if not path or not os.path.exists(path):
            return None
        key = "file:" + path
        if key in self.clips:
            return self.clips[key]
        try:
            with open(path, "rb") as handle:
                head = handle.read(4)
                data = handle.read(2 << 20)
        except OSError:
            return None
        clip = None
        if head == b"MThd":
            clip = self._decode_midi(head + data, hashlib.sha1(path.encode()).hexdigest(),
                                     hint or os.path.basename(path))
        elif self.enabled:
            target = os.path.join(
                self.stage_dir(),
                hashlib.sha1(path.encode()).hexdigest()[:12] + ".wav")
            clip = self._decode_file(path, target, hint)
        self.clips[key] = clip
        return clip

    def _decode_midi(self, data: bytes, key: str, hint: str) -> Clip | None:
        try:
            info = midi_module.parse(data)
        except midi_module.MidiError as error:
            self.log.warn(f"audio: bad MIDI source: {error}", source=hint)
            return None
        self.log.info("audio: MIDI parsed", source=hint,
                      seconds=round(info.seconds, 3), tracks=info.tracks)
        if not self.enabled or not self.synth or not self.soundfont or not self.player:
            return Clip(None, info.seconds, kind="midi")
        source = os.path.join(self.stage_dir(), f"{key[:12]}.mid")
        target = os.path.join(self.stage_dir(), f"{key[:12]}_midi.wav")
        try:
            with open(source, "wb") as handle:
                handle.write(data)
            result = subprocess.run(
                [self.synth, "-ni", "-F", target, "-r", "44100", "-g", "0.6",
                 self.soundfont, source],
                capture_output=True, timeout=120)
        except (OSError, subprocess.SubprocessError) as error:
            self.log.warn(f"audio: fluidsynth failed: {error}")
            return Clip(None, info.seconds, kind="midi")
        if result.returncode != 0 or not os.path.exists(target):
            self.log.warn("audio: fluidsynth produced nothing",
                          stderr=result.stderr.decode("utf-8", "replace")[:200])
            return Clip(None, info.seconds, kind="midi")
        return Clip(target, info.seconds, kind="midi")

    def _decode_file(self, source: str, target: str, hint: str) -> Clip | None:
        command = [self.decoder, "-hide_banner", "-loglevel", "error", "-y",
                   "-i", source, "-ar", "44100", "-ac", "2",
                   "-f", "wav", "-acodec", "pcm_s16le", target]
        try:
            result = subprocess.run(command, capture_output=True, timeout=30)
        except (OSError, subprocess.SubprocessError) as error:
            self.log.warn(f"audio: ffmpeg failed: {error}", source=hint)
            return None
        if result.returncode != 0 or not os.path.exists(target):
            self.log.warn("audio: undecodable source", source=hint,
                          stderr=result.stderr.decode("utf-8", "replace")[:200])
            return None
        try:
            with wave.open(target, "rb") as handle:
                seconds = handle.getnframes() / max(1, handle.getframerate())
        except (wave.Error, OSError):
            seconds = FALLBACK_SECONDS
        self.log.info("audio: decoded", source=hint or os.path.basename(source),
                      seconds=round(seconds, 3))
        return Clip(target, seconds)

    # -------------------------------------------------------------------- play
    def play(self, clip: Clip, volume: int = 100) -> subprocess.Popen | None:
        """Start host playback; returns the handle (None for silent clips)."""
        if clip is None or clip.path is None:
            return None
        if not self.enabled or not self.player:
            return None
        command = [self.player]
        if "paplay" in os.path.basename(self.player):
            level = max(0, min(PA_VOLUME_MAX, int(volume / 100.0 * PA_VOLUME_MAX)))
            command.append(f"--volume={level}")
        command.append(clip.path)
        try:
            process = subprocess.Popen(command, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL)
        except OSError as error:
            self.log.warn(f"audio: cannot start player: {error}")
            return None
        with self.lock:
            self._reap()
            self.playing.append(process)
        return process

    def stop(self, process: subprocess.Popen | None) -> None:
        if process is None:
            return
        with self.lock:
            if process in self.playing:
                self.playing.remove(process)
        try:
            process.terminate()
        except OSError:
            pass

    def stop_all(self) -> None:
        with self.lock:
            processes, self.playing = self.playing, []
        for process in processes:
            try:
                process.terminate()
            except OSError:
                pass

    def _reap(self) -> None:
        alive = []
        for process in self.playing:
            code = process.poll()
            if code is None:
                alive.append(process)
            elif code != 0 and not self.warned_player:
                self.warned_player = True
                self.log.warn(f"audio: paplay exited with {code} "
                              "- no sound on this machine?")
        self.playing = alive

    def shutdown(self) -> None:
        self.stop_all()
        if self._tempdir is not None:
            shutil.rmtree(self._tempdir, ignore_errors=True)
            self._tempdir = None
