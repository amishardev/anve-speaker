"""The assistant is talking."""
from __future__ import annotations

from ..effects.waveform import bars, speech_like, voice


def speaking(c):
    """Follows the loudness of the reply itself when the assistant reports it. When it
    does not, the wave moves like speech by the clock; the microphone is never used."""
    level = c.lv.out if c.lv.out_real else speech_like(c.t)
    if c.variant == "bars":
        return bars(c.n, level, c.c["speak_low"], c.c["speak_peak"])
    return voice(c.n, c.t, level, c.c["speak_low"], c.c["speak_high"], c.c["speak_peak"],
                 intensity=c.s["wave_intensity"], rest=0.24)
