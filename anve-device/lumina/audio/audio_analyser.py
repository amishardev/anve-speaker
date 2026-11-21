"""Turning raw loudness into something pleasant to look at.

A level that simply follows the sound flickers. Here it rises fast and falls slowly
(attack / release), ignores what is below a noise floor, and is scaled by a sensitivity.
"""
from __future__ import annotations

import math


class Smoother:
    def __init__(self, attack: float = 0.55, release: float = 0.10, gate: float = 0.04):
        self.attack, self.release, self.gate = attack, release, gate
        self.value = 0.0

    def feed(self, x: float, sensitivity: float = 1.0) -> float:
        x = 0.0 if x != x else max(0.0, min(1.0, float(x)))           # NaN -> 0
        x = 0.0 if x < self.gate else min(1.0, (x - self.gate) / (1 - self.gate) * sensitivity)
        self.value += (x - self.value) * (self.attack if x > self.value else self.release)
        return self.value

    def decay(self) -> float:
        self.value *= 1 - self.release
        if self.value < 0.003:
            self.value = 0.0
        return self.value


def loudness(pcm: bytes) -> float:
    """0..1 for a block of 16-bit mono sound: about 0 at -60 dB, 1 at -6 dB."""
    n = len(pcm) // 2
    if not n:
        return 0.0
    try:
        import numpy as np
        a = np.frombuffer(pcm[:n * 2], dtype="<i2").astype(np.float32) / 32768.0
        rms = float(np.sqrt(np.mean(a * a)))
    except ImportError:
        import array
        a = array.array("h", pcm[:n * 2])
        rms = math.sqrt(sum(v * v for v in a) / n) / 32768.0
    if rms <= 1e-6:
        return 0.0
    return max(0.0, min(1.0, (20 * math.log10(rms) + 60) / 54))


class Bands:
    """Bass, mids, treble and beats from 16-bit mono sound (needs numpy).

    Quiet music is turned up first (up to `max_gain_db`), so the lights move as much at a
    low volume, or with the music turned down under a conversation, as at a loud one. The
    reference is the loudest recent block, which falls 3 dB a second; near-silence
    (below -55 dBFS) is never turned up, so the noise between two songs stays dark."""

    def __init__(self, rate: int = 16000, target_db: float = -20.0, max_gain_db: float = 30.0):
        self.rate = rate
        self.bass, self.mid, self.treble = Smoother(0.6, 0.18, 0.02), Smoother(0.5, 0.15, 0.02), Smoother(0.7, 0.25, 0.02)
        self.beat = 0.0
        self._avg = 0.0
        self.target_db, self.max_gain_db = target_db, max_gain_db
        self._peak = None
        self.gain_db = 0.0

    def feed(self, pcm: bytes) -> dict | None:
        try:
            import numpy as np
        except ImportError:
            return None
        n = len(pcm) // 2
        if n < 256:
            return None
        a = np.frombuffer(pcm[:n * 2], dtype="<i2").astype(np.float32) / 32768.0
        rms = float(np.sqrt(np.mean(a * a)))
        db = 20 * math.log10(rms) if rms > 1e-6 else -120.0
        fall = 3.0 * n / self.rate                                     # dB per block
        self._peak = db if self._peak is None else max(db, self._peak - fall)
        self.gain_db = 0.0 if self._peak < -55.0 else max(0.0, min(self.max_gain_db, self.target_db - self._peak))
        spec = np.abs(np.fft.rfft(a * np.hanning(n))) / (n / 4) * (10 ** (self.gain_db / 20))
        hz = np.fft.rfftfreq(n, 1 / self.rate)

        def band(lo, hi):
            sel = spec[(hz >= lo) & (hz < hi)]
            e = float(np.sqrt(np.mean(sel * sel))) if sel.size else 0.0
            return 0.0 if e <= 1e-6 else max(0.0, min(1.0, (20 * math.log10(e) + 60) / 50))

        raw_bass = band(40, 180)
        bass, mid, treble = self.bass.feed(raw_bass), self.mid.feed(band(180, 2000)), self.treble.feed(band(2000, 8000))
        # a beat: the bass jumps well above its own recent average
        if raw_bass > 0.25 and raw_bass > self._avg * 1.35 + 0.05:
            self.beat = 1.0
        else:
            self.beat *= 0.80
        self._avg += (raw_bass - self._avg) * 0.08
        return {"bass": bass, "mid": mid, "treble": treble, "beat": self.beat}
