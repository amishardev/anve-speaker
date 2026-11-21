"""Level and gain for the uplink, after echo cancellation and noise suppression.

The high-pass filter (about 80 Hz), the echo canceller and the noise suppressor run
inside PipeWire's echo-cancel module (WebRTC audio processing). What is left to do here
is gain: a person 3 m away and one at 0.5 m should reach the server at a similar level.

Agc is an automatic gain control built not to pump:
  * the noise floor is the 15th percentile of the last 6 s of frame levels, so steady
    room noise is known as noise within a second and can never be mistaken for speech;
  * the gain only moves on speech frames (12 dB over the floor), towards a target taken
    from a smoothed speech level, about 6 dB a second up and 20 dB a second down;
  * between phrases the signal is turned DOWN a further `gate_db` (after a 0.3 s hold),
    so what the server's own silence detector sees between words is clearly silence;
  * gain changes are ramped across the frame (no clicks) and a soft limiter catches a
    sudden loud word instead of clipping it.

Measured 16 Nov 2026 on this device: the first version of this class had a noise floor
that started too low and rose too slowly; in an idle room it raised the gain to +24 dB
and the speech detector then called the room noise "speech" 81 % of the time.
"""
from collections import deque

import numpy as np


def dbfs(x: np.ndarray) -> float:
    if not x.size:
        return -100.0
    return 20 * np.log10(max(float(np.sqrt(np.mean(x * x))), 1e-6))


class Agc:
    # The gate is for the uplink only. Measured: when the wake-word spotter was given
    # gated audio, the first syllable of every phrase had been turned down before the
    # speech detector opened the gate, and Whisper heard "Hey ANVE, how is ..." as "And
    # we ..." (the "Hey" was gone). With no gate at all, the server's own silence
    # detector called 58 % of a room's non-speech sound "speech" and would not have
    # ended a request. So: the spotter reads `ungated`, the server gets the gated frame,
    # and the gate opens on the first hint of speech.
    def __init__(self, target_db: float = -18.0, max_gain_db: float = 15.0,
                 min_gain_db: float = -6.0, gate_db: float = -24.0, frame_s: float = 0.04):
        self.target, self.max_gain, self.min_gain = target_db, max_gain_db, min_gain_db
        self.gate_db = gate_db
        self.gain_db = 0.0
        self.gate = gate_db                # extra attenuation in force now (0 during speech)
        self.up = 6.0 * frame_s
        self.down = 20.0 * frame_s
        self.hist = deque(maxlen=int(6.0 / frame_s))
        self.floor = -60.0
        self.speech_lv = None
        self.hold = 0
        # 0.6 s: far speech (3 m) scored a median of only 0.32 on the speech detector,
        # so the gate must ride through the dips between its detections
        self.hold_frames = int(0.6 / frame_s)
        self.level_in = self.level_out = -100.0
        self.speech = False
        self._g = 10 ** (self.gain_db / 20)
        self.ungated = b""
        self.lookahead = 2                              # frames the uplink is delayed
        self.delay_samples = self.lookahead * int(16000 * frame_s)
        self._line: deque = deque()

    def process(self, pcm16: bytes, speech: bool | None = None,
                gate_open: bool = False, strict: bool = False) -> bytes:
        """Returns the frame for the UPLINK (gain and gate); `self.ungated` then holds
        the same frame with gain only. `speech` is the caller's (neural) speech decision
        for this frame, when it has one; without it the level rule below decides.
        `gate_open` is a weaker hint (speech is likely starting) that opens the gate at
        once without moving the gain."""
        x = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
        lv = self.level_in = dbfs(x)
        self.hist.append(lv)
        # with only a few frames seen, assume what we hear is the room, not a voice
        self.floor = float(np.percentile(self.hist, 15)) if len(self.hist) >= 12 else lv
        if speech is None:
            self.speech = lv > max(self.floor + 12.0, -50.0)
        elif strict:
            # music is playing underneath: singing looks like speech to the detector, so
            # also demand that the frame stands clearly above what the music leaves
            self.speech = bool(speech) and lv > self.floor + 9.0
        else:
            self.speech = bool(speech) and lv > -60.0
        if self.speech:
            self.hold = self.hold_frames
            if self.speech_lv is None:
                self.speech_lv = lv
            else:
                self.speech_lv += (lv - self.speech_lv) * (0.30 if lv > self.speech_lv else 0.03)
            want = max(self.min_gain, min(self.max_gain, self.target - self.speech_lv))
            step = self.up if want > self.gain_db else self.down
            self.gain_db += max(-step, min(step, want - self.gain_db))
        elif gate_open:
            self.hold = max(self.hold, 2)              # a likely onset: open, briefly
        elif self.hold:
            self.hold -= 1
        g = 10 ** (self.gain_db / 20)
        y = x * np.linspace(self._g, g, x.size, dtype=np.float32)
        self._g = g
        over = np.abs(y) > 0.7                         # soft limiter above -3 dBFS
        if over.any():
            y = np.where(over, np.sign(y) * (0.7 + 0.3 * np.tanh((np.abs(y) - 0.7) / 0.3)), y)
        #: gain only, no gate, no delay: what the wake-word spotter listens to
        self.ungated = (y * 32767.0).astype(np.int16).tobytes()
        # The uplink runs `lookahead` frames late, so that when speech is detected the
        # gate is already open for the frames just before it: the first syllable gets
        # through at full level ("The quarterly ..." had reached Whisper as "Early ...").
        is_open = self.hold > 0
        self._line.append([y, is_open])
        if is_open:
            for item in self._line:
                item[1] = True
        if len(self._line) <= self.lookahead:
            self.level_out = -100.0
            return bytes(len(pcm16))
        y, is_open = self._line.popleft()
        gate_to = 0.0 if is_open else self.gate_db
        prev = self.gate
        self.gate = gate_to if gate_to > prev else prev + (gate_to - prev) * 0.3
        if prev != 0.0 or self.gate != 0.0:
            y = y * np.linspace(10 ** (prev / 20), 10 ** (self.gate / 20), y.size,
                                dtype=np.float32)
        self.level_out = dbfs(y)
        return (y * 32767.0).astype(np.int16).tobytes()

    def meter(self) -> float:
        """0..1 for the LED bar: how far the voice is above the noise floor."""
        return max(0.0, min(1.0, (self.level_in - self.floor - 6.0) / 30.0))
