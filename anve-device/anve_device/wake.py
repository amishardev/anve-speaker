""""Hey ANVE", spotted on the device, so nothing is uploaded before the wake word.

This is the platform's own box-side spotter (server/anve/services/assistant/wakeword.py)
ported to run on the Pi: the microphone stream is cut into speech segments,
faster-whisper `tiny.en` transcribes the start of each one, and a segment is a wake when
its first words are a greeting followed by the name. The rules (GREETING, NAMES, VETO,
BARGE, the second unbiased decode) are the platform's, unchanged, because each one is
there for a measured reason (docs/wake-word.md).

What differs from the server, and why (all measured on this Pi, 16 Nov 2026):
  * the decode is bounded (temperature 0, no fallback, at most 24 new tokens). With the
    library defaults a noisy segment took 8 to 12 s, because Whisper retries at higher
    temperatures and repeats itself;
  * the second, unbiased decode (the veto) pads its clip to 10 s instead of Whisper's
    usual 30 s, which cuts its encoder time from 765 ms to 131 ms; the main decode
    keeps 30 s because the short pad misses the name far more often;
  * speech is found by the Silero VAD model, not by level alone (see SileroVAD);
  * it runs on 2 threads: 4 threads were no faster and pulled the 5 V rail under the
    Pi's limit on the charger this device was built with;
  * a segment is decoded EARLY, as soon as it is `early_s` long, instead of only when
    the speaker pauses, so "Hey ANVE, what's the weather" is recognised as a wake while
    the request is still being spoken.

Licences: faster-whisper (MIT), Whisper tiny.en weights (MIT), Silero VAD (MIT). No
openWakeWord pretrained model is used (those are CC BY-NC-SA, non-commercial).
"""
import re
import threading
import time
from collections import deque

import numpy as np

RATE = 16000
FRAME = 640                                  # samples per 40 ms frame
FRAME_BYTES = FRAME * 2
GREETING = {"hey", "hi", "hello", "ok", "okay", "hay", "a"}
NAMES = {"anve", "anvé", "anvee", "anvey", "anv"}
BARGE = {"stop", "cancel", "quiet", "enough", "shut up", "be quiet", "never mind",
         "nevermind", "pause"}
STOP_WORDS = {"stop", "cancel", "quiet", "enough", "pause", "silence", "shut"}
VETO = {"annie", "andy", "anna", "anne", "any", "anyway", "navy", "anvay", "andrew",
        "ammy", "amy", "honey"}


def dbfs(pcm16: bytes) -> float:
    a = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32)
    if not a.size:
        return -100.0
    rms = float(np.sqrt(np.mean(a * a))) / 32768.0
    return 20 * np.log10(max(rms, 1e-6))


class EnergyVAD:
    """The SERVER's speech detector (wakeword.py), kept here so the mock server can
    judge the uplink exactly as the platform will: speech / non-speech per 40 ms frame
    against an adaptive noise floor (10th percentile of the last ~10 s, never above
    -35 dBFS); speech is 10 dB over the floor and above -55 dBFS."""

    def __init__(self, hang_frames: int = 12, preroll_frames: int = 8, margin: float = 10.0):
        self.hist = deque(maxlen=250)
        self.pre = deque(maxlen=preroll_frames)
        self.run = self.quiet = 0
        self.active = False
        self.hang, self.margin = hang_frames, margin
        self.level = -100.0

    @property
    def floor(self) -> float:
        if len(self.hist) < 5:
            return -70.0
        return min(float(np.percentile(self.hist, 10)), -35.0)

    def feed(self, pcm16: bytes) -> str:
        db = self.level = dbfs(pcm16)
        speech = db > max(self.floor + self.margin, -55.0)
        self.hist.append(db)
        if not self.active:
            self.pre.append(pcm16)
        if speech:
            self.run += 1
            self.quiet = 0
            if not self.active and self.run >= 3:
                self.active = True
                return "start"
        else:
            self.run = 0
            if self.active:
                self.quiet += 1
                if self.quiet >= self.hang:
                    self.active = False
                    self.quiet = 0
                    return "end"
        return ""

    def take_preroll(self) -> bytes:
        out = b"".join(self.pre)
        self.pre.clear()
        return out


class SileroVAD:
    """Speech probability per 32 ms window from the Silero VAD model that ships with
    faster-whisper, run as a stream: the LSTM state and the 64-sample context are kept
    between calls. A fraction of a millisecond of CPU per window on the Pi 5.

    Why not level alone: in the room this device was built in, a steady non-speech sound
    at about -25 dBFS kept a level detector "in speech" for seconds at a time, and
    Whisper then invented "Hey, hey, hey". Silero scored that sound 0.00 and the owner's
    voice about 0.8."""

    WINDOW, CONTEXT = 512, 64

    def __init__(self):
        from faster_whisper.vad import get_vad_model
        self.session = get_vad_model().session
        self.reset()

    def reset(self):
        self.h = np.zeros((1, 1, 128), dtype=np.float32)
        self.c = np.zeros((1, 1, 128), dtype=np.float32)
        self.ctx = np.zeros(self.CONTEXT, dtype=np.float32)
        self.pending = np.zeros(0, dtype=np.float32)
        self.prob = 0.0

    def feed(self, pcm16: bytes) -> float:
        """The highest speech probability among the windows this frame completed (the
        last known one if it completed none)."""
        x = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
        self.pending = np.concatenate([self.pending, x])
        best = None
        while self.pending.size >= self.WINDOW:
            win, self.pending = self.pending[:self.WINDOW], self.pending[self.WINDOW:]
            inp = np.concatenate([self.ctx, win])[None, :]
            out, self.h, self.c = self.session.run(
                None, {"input": inp, "h": self.h, "c": self.c})
            self.ctx = win[-self.CONTEXT:]
            p = float(np.asarray(out).reshape(-1)[0])
            best = p if best is None else max(best, p)
        if best is not None:
            self.prob = best
        return self.prob


class Segmenter:
    """Turns 40 ms frames into decode requests.

    feed(frame, index, speech) returns a list of (kind, pcm, start_index):
      "early"  the segment so far, once it is `early_s` long (speech still going on)
      "final"  the whole segment, when the speaker pauses or it reaches `max_s`
    `speech` is the caller's speech / non-speech decision for this frame; left out, the
    segmenter runs its own SileroVAD on the frame. `index` is the absolute sample index
    of the frame's first sample, so the caller can map Whisper's word timestamps back
    onto its own audio buffer. A segment starts after 2 speech frames (80 ms), keeps the
    `preroll_frames` before that, and ends after `hang_frames` non-speech frames."""

    def __init__(self, early_s: float = 1.3, max_s: float = 6.0, hang_frames: int = 10,
                 preroll_frames: int = 12, own_vad: bool = True):
        self.vad = SileroVAD() if own_vad else None
        self.early_n = int(early_s * RATE) * 2
        self.max_n = int(max_s * RATE) * 2
        self.hang = hang_frames
        self.pre = deque(maxlen=preroll_frames)
        self.seg = bytearray()
        self.start_index = 0
        self.early_sent = False
        self.active = False
        self.run = self.quiet = 0

    def feed(self, frame: bytes, index: int, speech: bool | None = None) -> list:
        if speech is None:
            speech = self.vad.feed(frame) >= 0.5
        out, ev = [], ""
        if not self.active:
            self.pre.append(frame)
        if speech:
            self.run += 1
            self.quiet = 0
            if not self.active and self.run >= 2:
                self.active, ev = True, "start"
        else:
            self.run = 0
            if self.active:
                self.quiet += 1
                if self.quiet >= self.hang:
                    self.active, self.quiet, ev = False, 0, "end"
        if ev == "start":
            pre = b"".join(self.pre)                 # ends with this frame
            self.pre.clear()
            self.seg = bytearray(pre)
            self.start_index = index + len(frame) // 2 - len(pre) // 2
            self.early_sent = False
        elif self.active or ev == "end":
            self.seg.extend(frame)
        if self.active and not self.early_sent and len(self.seg) >= self.early_n:
            self.early_sent = True
            out.append(("early", bytes(self.seg), self.start_index))
        if self.seg and (ev == "end" or len(self.seg) >= self.max_n):
            out.append(("final", bytes(self.seg), self.start_index))
            self.seg = bytearray()
            if ev != "end":                          # capped mid-speech: start a new one
                self.start_index = index + len(frame) // 2
                self.early_sent = True
        return out

    def reset(self):
        self.seg = bytearray()
        self.early_sent = False
        self.active = False
        self.run = self.quiet = 0


def _norm(word: str) -> str:
    return re.sub(r"[^a-zé]+", "", word.lower())


def find_wake(words: list) -> float | None:
    """End time (s) of the wake phrase if the segment starts with one, else None. The
    name must come within the first three words, after a greeting."""
    toks = [(_norm(w), e) for w, _s, e in words]
    toks = [(t, e) for t, e in toks if t]
    for i, (t, e) in enumerate(toks[:3]):
        name = t in NAMES or (t.startswith("anve") and len(t) <= 7)
        if name and i > 0 and toks[i - 1][0] in GREETING:
            return e
        if name and i == 0 and len(toks) == 1:
            return e
    return None


def find_wake_anywhere(words: list) -> float | None:
    """End time (s) of a greeting followed by the name, wherever it is in the clip.
    For use while the speaker's own music plays: singing keeps the "speech" going, so
    the wake phrase never falls at the start of a segment and find_wake cannot see it."""
    toks = [(_norm(w), e) for w, _s, e in words]
    toks = [(t, e) for t, e in toks if t]
    for i in range(1, len(toks)):
        t, e = toks[i]
        if (t in NAMES or (t.startswith("anve") and len(t) <= 7)) and toks[i - 1][0] in GREETING:
            return e
    return None


def is_barge(words: list) -> bool:
    """'stop', 'cancel' ... as the platform's box-side spotter judges it."""
    text = " ".join(_norm(w) for w, _s, _e in words).strip()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"^(hey |hi |ok |okay )?(anve )?", "", text)
    return text in BARGE or any(text.startswith(b + " ") for b in ("stop", "cancel"))


def has_stop_word(words: list) -> bool:
    """Looser than is_barge, for use WHILE the device itself is talking. The person's
    "stop" reaches Whisper mixed with what is left of the device's own voice and came
    out as "You can hit me stop here" in testing, which is_barge rejects. Here any stop
    word in a short segment counts."""
    toks = [t for t in (_norm(w) for w, _s, _e in words) if t]
    return 0 < len(toks) <= 12 and any(t in STOP_WORDS for t in toks)


class Spotter:
    """The Whisper model and the wake / stop decision for one segment."""

    def __init__(self, model: str = "tiny.en", threads: int = 2, hotwords: str = "Hey ANVE",
                 max_decode_s: float = 3.0, window_s: float = 10.0, tokens: int = 24):
        self.model_name, self.threads, self.hotwords = model, threads, hotwords
        # How much text one decode may produce. Fewer is NOT faster: measured 19 Nov on
        # the owner's clips with nothing else running, 24 tokens took a median of 1.4 s,
        # 12 took 2.4 s and 8 took 3.2 s, because a cut-off answer makes Whisper go over
        # the same audio again.
        self.tokens = tokens
        self.max_n = int(max_decode_s * RATE)
        self.window_frames = int(window_s * 100)       # mel frames, 10 ms each
        self._model = None
        self._lock = threading.Lock()

    def load(self):
        with self._lock:
            if self._model is None:
                import faster_whisper.transcribe as fwt
                from faster_whisper import WhisperModel
                # Whisper pads every clip to 30 s before encoding, and on this Pi the
                # encoder then costs about 0.77 s. A shorter pad is much faster (10 s:
                # 0.13 s) but the model was trained on 30 s and hears the name less
                # often: on the owner's recorded wake phrases, 10 of 16 clips were
                # recognised at 30 s, 6 at 10 s and 2 at 6 s. So the main decode keeps
                # 30 s, and only the second "is it another name" decode uses the short
                # window. `fwt._anve_frames` selects the pad length for the next decode.
                if not hasattr(fwt, "_anve_frames"):
                    orig = fwt.pad_or_trim
                    fwt._anve_frames = 3000
                    fwt.pad_or_trim = lambda arr, length=None, **kw: orig(
                        arr, length or fwt._anve_frames, **kw)
                self._fwt = fwt
                self._model = WhisperModel(self.model_name, device="cpu",
                                           compute_type="int8", cpu_threads=self.threads)
        return self._model

    def words(self, pcm16: bytes, *, hotwords: str | None,
              short: bool = False) -> tuple[list, float]:
        """([(word, start_s, end_s)], confidence 0..1) for the start of one segment.
        `short` pads to `window_s` instead of 30 s: faster, less accurate."""
        audio = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32)[:self.max_n] / 32768.0
        if audio.size < 4000:
            return [], 0.0
        m = self.load()
        with self._lock:
            self._fwt._anve_frames = self.window_frames if short else 3000
            segs, _ = m.transcribe(audio, language="en", beam_size=1, word_timestamps=True,
                                   hotwords=hotwords or None, temperature=0.0,
                                   max_new_tokens=self.tokens, condition_on_previous_text=False,
                                   vad_filter=False, without_timestamps=False)
            segs = list(segs)
        words = [(w.word.strip(), float(w.start), float(w.end))
                 for s in segs for w in (s.words or [])]
        conf = float(np.exp(np.mean([s.avg_logprob for s in segs]))) if segs else 0.0
        return words, conf

    def detect(self, pcm16: bytes, anywhere: bool = False) -> dict:
        """{'wake', 'end_s', 'barge', 'stop_word', 'text', 'score', 'vetoed', 'ms'}.
        `anywhere` looks for the wake phrase in the whole clip, not only at its start."""
        t0 = time.monotonic()
        words, conf = self.words(pcm16, hotwords=self.hotwords)
        end = find_wake_anywhere(words) if anywhere else find_wake(words)
        vetoed = False
        if end is not None and not anywhere:
            toks = [_norm(w) for w, _s, _e in self.words(pcm16, hotwords=None, short=True)[0]]
            toks = [t for t in toks if t]
            vetoed = any(i > 0 and toks[i - 1] in GREETING and t in VETO
                         for i, t in enumerate(toks[:3]))
            if vetoed:
                end = None
        return {"wake": end is not None, "end_s": end or 0.0, "barge": is_barge(words),
                "stop_word": has_stop_word(words),
                "text": " ".join(w for w, _s, _e in words), "score": round(conf, 3),
                "vetoed": vetoed, "ms": int((time.monotonic() - t0) * 1000)}
