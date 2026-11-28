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


class Burst:
    """While the speaker's own music plays: cut out a short sound that stands out of it.

    The speech detector is no use then (singing is speech), and a fixed 3 s window every
    2 s cut "Hey ANVE" in two as often as not. What is left of the music after echo
    cancelling is steady and quiet once the microphone no longer overloads (about
    -38 dBFS), and a voice in the room is 15 to 20 dB over it. So: a frame is "hot" when
    it is `rise_db` over the floor (the 20th percentile of the last 3 s); a burst starts
    after 3 hot frames, keeps the 12 frames before it (the same lead-in the Segmenter
    gives, so the owner's recordings line up) and ends after 6 frames that are not hot.
    Only bursts of 0.5 to 2.2 s are passed on: a drum hit is shorter, a chorus longer.

    feed(frame, index) returns [(pcm, start_index)], empty most of the time."""

    def __init__(self, rise_db: float = 8.0):
        self.rise = rise_db
        self.levels = deque(maxlen=75)
        self.pre = deque(maxlen=12)
        self.seg = bytearray()
        self.active = False
        self.run = self.quiet = self.hot_n = 0
        self.start_index = 0

    def reset(self):
        self.seg = bytearray()
        self.active = False
        self.run = self.quiet = self.hot_n = 0

    def feed(self, frame: bytes, index: int) -> list:
        db = dbfs(frame)
        floor = float(np.percentile(self.levels, 20)) if len(self.levels) >= 25 else 0.0
        hot = db > floor + self.rise and db > -50.0
        if not self.active:
            self.levels.append(db)                   # the floor is the music, not the burst
            self.pre.append(frame)
            self.run = self.run + 1 if hot else 0
            if self.run >= 3:
                self.active, self.quiet, self.hot_n = True, 0, 3
                pre = b"".join(self.pre)
                self.seg = bytearray(pre)
                self.start_index = index + len(frame) // 2 - len(pre) // 2
                self.pre.clear()
            return []
        self.seg.extend(frame)
        self.hot_n += hot
        self.quiet = 0 if hot else self.quiet + 1
        if self.quiet < 6 and len(self.seg) < int(3.2 * RATE) * 2:
            return []
        ended, pcm, n = self.quiet >= 6, bytes(self.seg), self.hot_n
        self.reset()                                 # too long = the music itself got louder
        # 8 hot frames (0.32 s), not 12: the owner's "stop" alone is that short
        return [(pcm, self.start_index)] if ended and 8 <= n <= 55 else []


def _norm(word: str) -> str:
    return re.sub(r"[^a-zé]+", "", word.lower())


def find_wake(words: list) -> float | None:
    """End time (s) of the wake phrase if the segment starts with one, else None. The
    name must come within the first three words, after a greeting, or be the very first
    word: since 23 Nov 2026 a bare "ANVE" wakes it too, alone or with the request right
    behind it ("ANVE, what's the weather"), as the owner asked."""
    toks = [(_norm(w), e) for w, _s, e in words]
    toks = [(t, e) for t, e in toks if t]
    for i, (t, e) in enumerate(toks[:3]):
        name = t in NAMES or (t.startswith("anve") and len(t) <= 7)
        if name and (i == 0 or toks[i - 1][0] in GREETING):
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


class Enrolled:
    """The owner's own "Hey ANVE", matched by sound instead of by spelling.

    Why: on the right charger, in a quiet room, the recogniser wrote the owner's phrase as
    "Hey ANVE" 3 times in 12 (23 Nov 2026); the rest came out as "Hey AVE", "Here we go.",
    "Hey Andy!", "Yeah, I'm good." A made-up name has no spelling Whisper knows, and a
    bigger model (base.en) was slower and no better. So the phrase is also compared with
    recordings of the owner saying it (tools/wake_enroll.py makes the file): the Whisper
    encoder turns a clip into one vector per 20 ms, and dynamic time warping measures how
    far the new clip is from each recording (mean cosine distance along the best path;
    the score is the mean of the 3 nearest recordings).

    Measured on the Pi, each of those 12 clips against the other 11: 0.07 to 0.15, ten of
    them under 0.122. Against 158 clips that were not the phrase (six minutes of talk in
    the same room, the owner reading aloud, the day before's questions): 0.125 and up,
    and the two nearest (0.125, 0.142) may well have been the phrase said in passing.
    So the limit is 0.122: 10 of 12 by sound, 11 of 12 together with the text rules,
    none of the 158. The margin is thin; the journal prints the two scores with every
    wake made this way, so the limit can be moved on evidence. Two tests:
      whole   the clip IS the phrase                         (bare "Hey ANVE")
      prefix  the clip STARTS with the phrase and goes on    ("Hey ANVE, what's the ...")
    The prefix test is the looser kind of match, so its limit is tighter.

    It only ever adds a wake; everything the text rules accept still counts. It knows one
    voice: other people are heard by the text rules alone, as before."""

    def __init__(self, path, whole: float = 0.122, prefix: float = 0.110):
        self.whole, self.prefix = whole, prefix
        try:
            with np.load(path) as z:
                self.templates = [z[k] for k in z.files]
        except (OSError, ValueError):
            self.templates = []

    @staticmethod
    def _dtw(t: np.ndarray, q: np.ndarray) -> tuple[float, float, int]:
        """(whole, prefix, frames of q the prefix match used) for template t, query q."""
        d = (1.0 - t @ q.T).tolist()
        n, k = len(d), len(d[0])
        inf = float("inf")
        prev = [0.0] + [inf] * k
        for i in range(n):
            cur, di, left = [inf] * (k + 1), d[i], inf
            for j in range(1, k + 1):
                a, b = prev[j], prev[j - 1]
                m = a if a < b else b
                if left < m:
                    m = left
                left = cur[j] = di[j - 1] + m
            prev = cur
        whole, best, at = prev[k] / (n + k), inf, k
        for j in range(max(1, int(n * 0.6)), k + 1):
            v = prev[j] / (n + j)
            if v < best:
                best, at = v, j
        return whole, min(best, whole), at

    @staticmethod
    def _inside(t: np.ndarray, q: np.ndarray) -> tuple[float, int]:
        """Template t somewhere INSIDE q (free start and end): (mean cost per step of
        the best path, the frame of q where it ends)."""
        d = (1.0 - t @ q.T).tolist()
        n, k = len(d), len(d[0])
        inf = float("inf")
        pc, pl = [0.0] * (k + 1), [0] * (k + 1)
        for i in range(n):
            di, cc, cl = d[i], [inf] * (k + 1), [0] * (k + 1)
            for j in range(1, k + 1):
                a, b, c = pc[j], pc[j - 1], cc[j - 1]
                if b <= a and b <= c:
                    m, ln = b, pl[j - 1]
                elif a <= c:
                    m, ln = a, pl[j]
                else:
                    m, ln = c, cl[j - 1]
                cc[j], cl[j] = di[j - 1] + m, ln + 1
            pc, pl = cc, cl
        best, at = inf, k
        for j in range(max(1, n // 2), k + 1):
            v = pc[j] / pl[j]
            if v < best:
                best, at = v, j
        return best, at

    def inside(self, e: np.ndarray, limit: float) -> dict:
        """The phrase anywhere in a 3 s window of the speaker's own music. The scale is
        not the one `match` uses: here the owner's phrase over a song scored 0.22 to
        0.44 and windows without it 0.37 and up (64 windows, 23 Nov 2026), so this
        catches about half and is the second net under Burst, not the first."""
        if len(self.templates) < 3 or len(e) < 40:
            return {"hit": False, "whole": 9.0, "prefix": 9.0, "end_s": 0.0}
        r = sorted(self._inside(t[6:], e) for t in self.templates)[:3]   # less lead-in
        s = float(np.mean([x[0] for x in r]))
        return {"hit": s <= limit, "whole": s, "prefix": s,
                "end_s": float(np.median([x[1] for x in r])) * 0.02}

    def match(self, e: np.ndarray, whole_limit: float | None = None) -> dict:
        """{'hit', 'whole', 'prefix', 'end_s'} for one clip's encoder vectors.
        `whole_limit`: use this limit for the whole-clip test and skip the prefix test
        (a burst cut out of music, see Burst: the clip is the phrase or it is nothing)."""
        if len(self.templates) < 3 or len(e) < 20:
            return {"hit": False, "whole": 9.0, "prefix": 9.0, "end_s": 0.0}
        r = [self._dtw(t, e) for t in self.templates]
        whole = float(np.mean(sorted(x[0] for x in r)[:3]))
        near = sorted(r, key=lambda x: x[1])[:3]
        prefix = float(np.mean([x[1] for x in near]))
        if whole_limit is not None:
            # (Tried 23 Nov 2026 and taken out again: also accepting a burst that only
            # STARTS with the phrase, for "ANVE stop" in one breath. 0 of 8 over a song.)
            return {"hit": whole <= whole_limit, "whole": whole, "prefix": prefix,
                    "end_s": len(e) * 0.02}
        if whole <= self.whole:
            return {"hit": True, "whole": whole, "prefix": prefix, "end_s": len(e) * 0.02}
        return {"hit": prefix <= self.prefix, "whole": whole, "prefix": prefix,
                "end_s": float(np.median([x[2] for x in near])) * 0.02}


class Head:
    """The trained wake model: a small convolution net over the same encoder vectors
    (`Spotter.embed`), made on the owner's laptop on 23 Nov 2026 from the phrase in 28
    synthetic voices (Kokoro, Apache-2.0), audiobook speech (LibriSpeech, CC BY 4.0) and
    the owner's own recordings. See models/WAKE_MODEL.md for the data, the licences and
    the measured results. score(e) is 0..1: how sure it is that the clip starts with
    "Hey ANVE" / "ANVE". Unlike `Enrolled` it is meant to know other voices too."""

    def __init__(self, path):
        self.session = None
        try:
            import onnxruntime as ort
            o = ort.SessionOptions()
            o.intra_op_num_threads = 1
            self.session = ort.InferenceSession(str(path), o, providers=["CPUExecutionProvider"])
        except Exception:                                    # noqa: BLE001  (no file, no runtime)
            pass

    def score(self, e: np.ndarray) -> float:
        if self.session is None or len(e) < 20:
            return 0.0
        z = float(self.session.run(None, {"x": e[None, :150].astype(np.float32)})[0].reshape(-1)[0])
        return 1.0 / (1.0 + float(np.exp(-z)))


class Spotter:
    """The Whisper model and the wake / stop decision for one segment."""

    def __init__(self, model: str = "tiny.en", threads: int = 2, hotwords: str = "Hey ANVE",
                 max_decode_s: float = 3.0, window_s: float = 10.0, tokens: int = 24,
                 enrolled: "Enrolled | None" = None, stop: "Enrolled | None" = None):
        self.enrolled = enrolled
        # the trained wake model and what it may do (set by main from the config):
        # head_live False = "shadow": its score is only reported, it wakes nothing
        self.head, self.head_limit, self.head_live = None, 0.9, False
        # the owner's recorded "stop": stops the music at once, without a wake and
        # without the cloud (tools/wake_enroll.py --stop). None or empty = off. The
        # owner says "ANVE ... stop" with a pause, so the two words arrive as two clips
        # and it is the "stop" that is matched (23 Nov 2026: 6 recordings, each against
        # the others 0.06 to 0.13; 142 other clips, wake phrases included, 0.20 and up).
        self.stop = stop
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
              short: bool = False, no_repeat: int = 0) -> tuple[list, float]:
        """([(word, start_s, end_s)], confidence 0..1) for the start of one segment.
        `short` pads to `window_s` instead of 30 s: faster, less accurate.
        `no_repeat`: forbid repeating any run of this many tokens (0 = allow)."""
        audio = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32)[:self.max_n] / 32768.0
        if audio.size < 4000:
            return [], 0.0
        m = self.load()
        with self._lock:
            self._fwt._anve_frames = self.window_frames if short else 3000
            segs, _ = m.transcribe(audio, language="en", beam_size=1, word_timestamps=True,
                                   hotwords=hotwords or None, temperature=0.0,
                                   max_new_tokens=self.tokens, condition_on_previous_text=False,
                                   vad_filter=False, without_timestamps=False,
                                   **({"no_repeat_ngram_size": no_repeat} if no_repeat else {}))
            segs = list(segs)
        words = [(w.word.strip(), float(w.start), float(w.end))
                 for s in segs for w in (s.words or [])]
        conf = float(np.exp(np.mean([s.avg_logprob for s in segs]))) if segs else 0.0
        return words, conf

    def says_stop(self, pcm16: bytes) -> str:
        """The stop word heard in the LAST 3 s of this sound ('' if none): what the
        speaker listens for by itself right after it was woken over its own music, when
        the music is turned down and muffled and a short order is readable again."""
        tail = pcm16[-self.max_n * 2:]
        words, _ = self.words(tail, hotwords="Hey ANVE, stop the music", no_repeat=3)
        toks = [t for t in (_norm(w) for w, _s, _e in words) if t]
        hit = [t for t in toks if t in STOP_WORDS or t in ("stopped", "stopping", "stops")]
        return hit[0] if hit and len(toks) <= 14 else ""

    def embed(self, pcm16: bytes) -> np.ndarray:
        """One unit vector per 20 ms of the first 3 s of a clip, from the Whisper encoder.
        The clip is padded to 5 s only: 62 ms on the Pi against 855 ms for the usual 30 s,
        and the clips told apart just as well (measured 23 Nov 2026)."""
        audio = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32)[:self.max_n] / 32768.0
        m = self.load()
        with self._lock:
            f = m.feature_extractor(audio)[:, :500]
            f = np.pad(f, ((0, 0), (0, 500 - f.shape[1])))
            e = np.array(m.encode(f))[0][:max(8, int(np.ceil(audio.size / 320)))]
        return e / (np.linalg.norm(e, axis=1, keepdims=True) + 1e-9)

    def detect_burst(self, pcm16: bytes, limit: float) -> dict:
        """The same answer as detect(), for a burst cut out of the speaker's own music:
        matched against the owner's recordings only (about 0.2 s; no text is decoded,
        the recogniser writes such a clip as "Yeah, me." or "Hey A")."""
        t0 = time.monotonic()
        e = self.embed(pcm16) if len(pcm16) >= 22400 else None
        m = self.enrolled.match(e, whole_limit=limit) \
            if e is not None and self.enrolled and self.enrolled.templates else None
        s = self.stop.match(e, whole_limit=limit) \
            if e is not None and self.stop and self.stop.templates else None
        return self._by_sound(m, s, t0)

    @staticmethod
    def _by_sound(m: dict | None, s: dict | None, t0: float) -> dict:
        """One answer from the two matches: "ANVE stop" wins when it is the nearer one
        (it begins with the name, so a wake match alone would swallow it)."""
        stop = bool(s and s["hit"] and not (m and m["hit"] and m["whole"] < s["whole"]))
        hit = bool(m and m["hit"]) and not stop
        return {"wake": hit, "end_s": m["end_s"] if hit else 0.0, "barge": False,
                "stop_word": False, "match": m, "stop_match": s, "stop_music": stop,
                "text": "", "score": 0.0, "vetoed": False,
                "ms": int((time.monotonic() - t0) * 1000)}

    def detect_window(self, pcm16: bytes, limit: float) -> dict:
        """The same answer for a 3 s window of the speaker's own music, by sound only.
        It replaces the text decode of such windows: in the test over a song the
        recogniser wrote the owner's ten phrases as "Yeah, me." and "Hey A", none right."""
        t0 = time.monotonic()
        e = self.embed(pcm16)
        m = self.enrolled.inside(e, limit) if self.enrolled and self.enrolled.templates else None
        # "stop" is NOT looked for in these windows: a 0.4 s word matches a song too
        # easily on this scale (it paused the music by itself at 0.234, 23 Nov 2026)
        return self._by_sound(m, None, t0)

    def detect(self, pcm16: bytes, anywhere: bool = False) -> dict:
        """{'wake', 'end_s', 'barge', 'stop_word', 'text', 'score', 'vetoed', 'ms'}.
        `anywhere` looks for the wake phrase in the whole clip, not only at its start."""
        t0 = time.monotonic()
        # Over music the recogniser often gets stuck writing "Hey, hey, hey, hey ..." until
        # its token limit. That took 2.5 to 4 s per window on the Pi (22 Nov 2026), longer
        # than the 2 s between windows, so whole stretches of sound were never looked at.
        # Forbidding a repeated run of 3 tokens ends the loop at once. On 144 test mixes the
        # wake decisions were identical with and without it; the slowest tenth of decodes
        # took half as long. Used for the music windows only: the segment path is unchanged.
        words, conf = self.words(pcm16, hotwords=self.hotwords, no_repeat=3 if anywhere else 0)
        end = find_wake_anywhere(words) if anywhere else find_wake(words)
        vetoed = False
        if end is not None and not anywhere:
            toks = [_norm(w) for w, _s, _e in self.words(pcm16, hotwords=None, short=True)[0]]
            toks = [t for t in toks if t]
            vetoed = any(i > 0 and toks[i - 1] in GREETING and t in VETO
                         for i, t in enumerate(toks[:3]))
            if vetoed:
                end = None
        # the owner's own phrase, by sound (see Enrolled). Not over music, where the clip
        # is 3 s of song with the phrase somewhere in it, and not for a plain "stop".
        match, stop_match, stop_music, model = None, None, False, None
        if not anywhere and self.head and self.head.session is not None and len(pcm16) >= 22400:
            model = self.head.score(self.embed(pcm16))
            if self.head_live and end is None and model >= self.head_limit and not is_barge(words):
                # the clip may go on after the phrase: the text's end if it has one, else all of it
                end, vetoed = min(len(pcm16) / 32000, self.max_n / RATE), False
        if end is None and not anywhere and self.enrolled and self.enrolled.templates \
                and len(pcm16) >= 22400:
            e = self.embed(pcm16)
            match = self.enrolled.match(e)
            stop_match = self.stop.match(e) if self.stop and self.stop.templates else None
            if stop_match and stop_match["hit"] and stop_match["whole"] <= match["whole"]:
                stop_music = True                    # "ANVE stop", not a wake
            elif match["hit"]:
                end, vetoed = match["end_s"], False
        return {"wake": end is not None, "end_s": end or 0.0, "barge": is_barge(words),
                "stop_word": has_stop_word(words), "match": match,
                "stop_match": stop_match, "stop_music": stop_music, "model": model,
                "text": " ".join(w for w, _s, _e in words), "score": round(conf, 3),
                "vetoed": vetoed, "ms": int((time.monotonic() - t0) * 1000)}
