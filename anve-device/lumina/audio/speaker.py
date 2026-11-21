"""What the speaker is playing: the assistant's own voice, and music.

The voice: the assistant measures the reply it is playing and sends that
(`AssistantLEDs.level(out=...)`). The microphone is never used for it.

Music (optional, `music_audio = "loopback"`): PipeWire or PulseAudio can hand a copy of
what goes to the speaker ("monitor"). `Loopback` records that copy at a low rate while
music plays and turns it into bass / mids / treble / beats. It only listens to the output;
if the recorder is missing or fails, music simply shows without reacting to the sound.
"""
from __future__ import annotations

import shutil
import subprocess
import threading
import time

from .audio_analyser import Bands
from .microphone import Level

RATE, BLOCK = 16000, 1024
QUICK_EXIT_S, RETRY_S = 5.0, 15.0     # a recorder that ends this soon is not restarted for RETRY_S


def speaker() -> Level:
    return Level(attack=0.65, release=0.16, gate=0.03)


def recorder_command(target: str = "") -> list | None:
    """`target`: the output whose copy is recorded ("" = the default one). On the ANVE
    speaker that is its echo canceller's input, `anve_ec_sink`: music and the assistant's
    voice both go through it, whichever loudspeaker is connected behind it."""
    if shutil.which("pw-record"):
        # --raw: plain samples on the pipe (without it a file header comes first)
        return ["pw-record", "--raw", *(["--target", target] if target else []), "--rate", str(RATE),
                "--channels", "1", "--format", "s16",
                "-P", '{ "stream.capture.sink": true, "node.name": "lumina-loopback" }', "-"]
    if shutil.which("parec"):
        return ["parec", "--rate", str(RATE), "--channels", "1", "--format", "s16le", "-d",
                f"{target}.monitor" if target else "@DEFAULT_MONITOR@"]
    return None


class Loopback:
    """Runs only while asked to (`want(True)` while music plays)."""

    def __init__(self, publish, command: list | None = None, clock=time.monotonic):
        self._publish, self._cmd, self._clock = publish, command, clock
        self._proc = None
        self._thread = None
        self._started = 0.0
        self._retry_at = 0.0
        self.target = ""
        self.starts = 0
        self.problem = ""

    def want(self, on: bool):
        # want() is called on every frame: a recorder that cannot run must not be started
        # 30 times a second, so after a quick failure it waits RETRY_S
        if on and self._proc is None and self._clock() >= self._retry_at:
            cmd = self._cmd or recorder_command(self.target)
            if not cmd:
                self.problem = "no recorder (pw-record or parec) found"
                self._retry_at = self._clock() + RETRY_S
                return
            try:
                self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            except OSError as e:
                self.problem, self._proc = str(e), None
                self._retry_at = self._clock() + RETRY_S
                return
            self._started = self._clock()
            self.starts += 1
            self.problem = ""
            self._thread = threading.Thread(target=self._run, args=(self._proc,), name="lumina-loopback", daemon=True)
            self._thread.start()
        elif not on and self._proc is not None:
            proc, self._proc = self._proc, None
            try:
                proc.terminate()
            except OSError:
                pass

    def _run(self, proc):
        bands = Bands(RATE)
        try:
            while True:
                block = proc.stdout.read(BLOCK * 2)
                if not block or len(block) < BLOCK * 2:
                    break
                got = bands.feed(block)
                if got:
                    self._publish(dict(got, type="level", source="loopback"))
        except (OSError, ValueError):
            pass
        finally:
            if self._proc is proc:
                if self._clock() - self._started < QUICK_EXIT_S:
                    self._retry_at = self._clock() + RETRY_S      # before _proc is cleared: no race
                self._proc, self.problem = None, "the recorder stopped"
            try:
                proc.wait(timeout=2)                 # collect it: no zombie per song
            except Exception:                        # noqa: BLE001
                pass

    def close(self):
        self.want(False)
