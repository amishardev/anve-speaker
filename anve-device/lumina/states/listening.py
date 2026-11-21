"""The wake word was heard; the assistant is listening; a meeting is being recorded."""
from __future__ import annotations

from ..effects.ambient import breathe
from ..effects.gradients import fill, layer, scale
from ..effects.pulse import burst
from ..effects.waveform import bars, voice


def wake(c):
    """Heard you: a bright pulse leaves the middle and crosses the whole strip at once."""
    return layer(fill(c.n, c.c["listen_low"], 0.22), burst(c.n, c.t, 0.5, c.c["wake"], width=3.5, k=1.0))


def listening(c):
    level = c.lv.mic if c.lv.mic_real else 0.0     # no microphone level known: it rests, it does not pretend
    if c.variant == "bars":
        return bars(c.n, level, c.c["listen_low"], c.c["listen_peak"])
    return voice(c.n, c.t, level, c.c["listen_low"], c.c["listen_high"], c.c["listen_peak"],
                 intensity=c.s["wave_intensity"], rest=0.34)


def recording(c):
    """A meeting is being recorded: the voice wave in the recording colour, with two
    markers at the ends that pulse slowly the whole time."""
    col = c.c["record"]
    level = c.lv.mic if c.lv.mic_real else 0.0
    frame = voice(c.n, c.t, level * 0.8, scale(col, 0.6), col, c.c["listen_peak"], intensity=c.s["wave_intensity"], rest=0.22)
    k = breathe(c.t, 2.4, 0.35, 0.90)
    for i in list(range(0, min(3, c.n))) + list(range(max(0, c.n - 3), c.n)):
        frame[i] = scale(col, k)
    return frame
