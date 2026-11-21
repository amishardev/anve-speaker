"""The speaker side: the echo canceller that sits between microphone and speaker, the
playback path with its jitter buffer and instant flush, and the device's earcons."""
import asyncio
import logging
import time

import numpy as np

from .audio import find_node

log = logging.getLogger("anve.audio")


class EchoCancel:
    """PipeWire's echo-cancel module (WebRTC AEC), loaded for as long as the device runs.

    It sits between the real microphone and the real speaker and adds two virtual nodes:
        anve_ec_sink     play here: the audio goes to the speaker AND is the echo reference
        anve_ec_source   record here: the microphone with the speaker's echo removed
    The microphone and the speaker are separate devices on separate clocks (here USB and
    Bluetooth). PipeWire resamples both onto one graph clock, and WebRTC's AEC3 estimates
    and tracks the remaining delay between reference and echo by itself.

    The module is loaded by a `pw-cli -m load-module` child, with the two devices resolved
    by name at that moment. When a device disappears or comes back, it is reloaded.
    `on_state(mic_ok, speaker_ok)` reports what is present."""

    SINK, SOURCE = "anve_ec_sink", "anve_ec_source"

    def __init__(self, mic_match: str, speaker_match: str, *, noise_suppression: bool = True,
                 on_state=None, on_loaded=None):
        self.mic_match, self.speaker_match = mic_match, speaker_match
        self.ns, self.on_state, self.on_loaded = noise_suppression, on_state, on_loaded
        self.mic = self.speaker = None
        self.ready = asyncio.Event()
        self._proc = None
        self._closed = False

    def _args(self) -> str:
        ns = "true" if self.ns else "false"
        return ("{ library.name = aec/libspa-aec-webrtc monitor.mode = false "
                "aec.args = { webrtc.gain_control = false webrtc.high_pass_filter = true "
                f"webrtc.noise_suppression = {ns} webrtc.voice_detection = false "
                "webrtc.transient_suppression = false } "
                "audio.rate = 48000 audio.channels = 1 audio.position = [ MONO ] "
                f'capture.props = {{ node.name = "anve_ec_capture" target.object = "{self.mic}" }} '
                f'source.props = {{ node.name = "{self.SOURCE}" '
                'node.description = "ANVE mic, echo-cancelled" } '
                f'sink.props = {{ node.name = "{self.SINK}" '
                'node.description = "ANVE speaker, echo reference" } '
                f'playback.props = {{ node.name = "anve_ec_playback" '
                f'target.object = "{self.speaker}" }} }}')

    async def run(self):
        last = None
        while not self._closed:
            mic = await asyncio.to_thread(find_node, "Audio/Source", self.mic_match)
            spk = await asyncio.to_thread(find_node, "Audio/Sink", self.speaker_match)
            now = (mic, spk)
            alive = self._proc is not None and self._proc.returncode is None
            if now != last or (mic and spk and not alive):
                last = now
                await self._unload()
                if self.on_state:
                    await self.on_state(bool(mic), bool(spk))
                if mic and spk:
                    self.mic, self.speaker = mic, spk
                    self._proc = await asyncio.create_subprocess_exec(
                        "pw-cli", "-m", "load-module", "libpipewire-module-echo-cancel",
                        self._args(), stdin=asyncio.subprocess.DEVNULL,
                        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                        start_new_session=True)
                    await asyncio.sleep(1.5)
                    if self._proc.returncode is None:
                        log.info("echo canceller loaded: mic %s -> speaker %s (noise "
                                 "suppression %s)", mic, spk, "on" if self.ns else "off")
                        self.ready.set()
                        if self.on_loaded:
                            await self.on_loaded()
                    else:
                        log.error("the echo-cancel module did not load")
                        last = None
                else:
                    log.warning("audio devices missing: microphone %s, speaker %s",
                                "ok" if mic else f"matching '{self.mic_match}' not found",
                                "ok" if spk else f"matching '{self.speaker_match}' not found")
            await asyncio.sleep(3)

    async def _unload(self):
        self.ready.clear()
        p, self._proc = self._proc, None
        if p and p.returncode is None:
            p.terminate()
            try:
                await asyncio.wait_for(p.wait(), 2)
            except asyncio.TimeoutError:
                p.kill()
        # a module left behind by an earlier run of this program
        k = await asyncio.create_subprocess_exec(
            "pkill", "-f", "pw-cli -m load-module libpipewire-module-echo-cancel",
            stderr=asyncio.subprocess.DEVNULL)
        await k.wait()
        await asyncio.sleep(0.3)

    async def close(self):
        self._closed = True
        await self._unload()


class Playback:
    """24 kHz mono PCM16 to a PipeWire sink, with a small jitter buffer and an instant
    flush.

    Audio from the server arrives in bursts, a little faster than real time. It is queued
    here; writing to the sink starts once `jitter_ms` is buffered (or after a short
    wait), so a late packet does not cause a gap. `flush()` drops the queue and kills the
    `pw-play` child, which discards what PipeWire still held. What a Bluetooth speaker
    has already received (about 0.2 s) cannot be recalled."""

    RATE = 24000
    BPS = RATE * 2

    def __init__(self, sink: str, *, jitter_ms: int = 80, tail_ms: int = 300):
        self.sink = sink
        self.jitter_bytes = int(self.BPS * jitter_ms / 1000)
        self.tail_s = tail_ms / 1000          # output delay after the last sample is written
        self._q: asyncio.Queue = asyncio.Queue()
        self._queued = 0
        self._proc = None
        self._task = None
        self._play_until = 0.0
        self._last_write = 0.0
        self._gen = 0
        self.level = 0.0                      # 0..1, of what is being played now

    def start(self):
        self._task = asyncio.create_task(self._writer())

    def remaining(self) -> float:
        """Seconds until everything accepted so far has left the speaker."""
        now = time.monotonic()
        left = self._queued / self.BPS + max(0.0, self._play_until - now)
        if left > 0:
            return left + self.tail_s
        return max(0.0, self._play_until + self.tail_s - now)

    @property
    def active(self) -> bool:
        return self.remaining() > 0

    def play(self, pcm: bytes):
        if pcm:
            self._queued += len(pcm)
            self._q.put_nowait((self._gen, pcm))

    def flush(self):
        """Stop sound now (barge-in, `stop_audio`)."""
        self._gen += 1
        self._queued = 0
        while not self._q.empty():
            self._q.get_nowait()
        self._play_until = 0.0
        self.level = 0.0
        p, self._proc = self._proc, None
        if p and p.returncode is None:
            p.kill()

    async def _spawn(self):
        self._proc = await asyncio.create_subprocess_exec(
            "pw-play", "--raw", "--target", self.sink, "--rate", str(self.RATE),
            "--channels", "1", "--format", "s16", "--latency", "40ms",
            # dont-reconnect: never let PipeWire move this stream straight onto the real
            # speaker, where the echo canceller would not see it as its reference
            "-P", '{"node.name":"anve-playback","media.role":"Communication",'
                  '"node.dont-reconnect":true}', "-",
            stdin=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)

    async def _writer(self):
        while True:
            gen, pcm = await self._q.get()
            if gen != self._gen:
                continue
            n = len(pcm)
            now = time.monotonic()
            if now - self._last_write > 1.0:
                # starting after a pause: wait for a little audio to build up, and give a
                # sink that went to sleep (Bluetooth does) some silence to wake up on
                t0 = now
                while self._queued < self.jitter_bytes and time.monotonic() - t0 < 0.15:
                    await asyncio.sleep(0.01)
                if gen != self._gen:
                    continue
                pcm = bytes(int(self.BPS * 0.12) // 2 * 2) + pcm
            try:
                if self._proc is None or self._proc.returncode is not None:
                    await self._spawn()
                self._proc.stdin.write(pcm)
                await self._proc.stdin.drain()
            except Exception:                                # noqa: BLE001
                self._proc = None
                self._queued = max(0, self._queued - n)
                await asyncio.sleep(0.2)
                continue
            if gen != self._gen:
                continue
            self._queued = max(0, self._queued - n)
            now = time.monotonic()
            self._last_write = now
            self._play_until = max(now, self._play_until) + len(pcm) / self.BPS
            a = np.frombuffer(pcm[-1920:], dtype=np.int16).astype(np.float32) / 32768.0
            self.level = float(min(1.0, np.sqrt(np.mean(a * a)) * 4)) if a.size else 0.0
            # do not run far ahead of real time, so a flush has little left to discard
            ahead = self._play_until - now
            if ahead > 0.25:
                await asyncio.sleep(ahead - 0.25)

    async def close(self):
        if self._task:
            self._task.cancel()
        self.flush()


def tone(freqs, *, each: float = 0.075, ring: float = 0.26, gain: float = 0.16,
         rate: int = 24000) -> bytes:
    """The device's earcons: soft bell-like notes that overlap as they ring out.

    Each note starts `each` seconds after the one before (0 = a rest) and rings for
    `ring` seconds: a quick 8 ms attack, then an exponential fade, with two quiet
    overtones so it sounds like a small glass bell and not a test tone."""
    total = int(rate * (each * (len(freqs) - 1) + ring)) + 1
    out = np.zeros(total, dtype=np.float64)
    n = int(rate * ring)
    t = np.arange(n) / rate
    env = np.minimum(t / 0.008, 1.0) * np.exp(-t * 13.0) * np.minimum((ring - t) / 0.02, 1.0)
    for i, f in enumerate(freqs):
        if not f:
            continue
        note = (np.sin(2 * np.pi * f * t) + 0.30 * np.sin(2 * np.pi * 2 * f * t) * np.exp(-t * 9)
                + 0.10 * np.sin(2 * np.pi * 3 * f * t) * np.exp(-t * 18)) * env
        a = int(rate * each * i)
        out[a:a + n] += note
    out *= gain / max(1.0, float(np.max(np.abs(out))))
    return (out * 32767).astype(np.int16).tobytes()


#: wake: a rising fifth, "I'm listening"; end: the same falling, "never mind";
#: chime: a small rising arpeggio for news; alarm: insistent on purpose
EARCONS = {"wake": (880.0, 1318.5), "end": (1318.5, 880.0), "chime": (1046.5, 1318.5, 1568.0),
           # start: a rising major chord, played once when the speaker is ready after power-on
           "start": (523.25, 659.25, 783.99, 1046.5),
           "alarm": (1760, 0, 1760, 0, 1760, 0, 0, 0, 1760, 0, 1760, 0, 1760, 0, 0, 0, 1760, 0, 1760, 0, 1760)}
