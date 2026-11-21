"""Things the person must notice: muted, offline, an error, an alarm; and switching off."""
from __future__ import annotations

from ..effects.ambient import breathe
from ..effects.gradients import blank, fill, scale
from ..effects.pulse import blink
from ..effects.transitions import fade


def muted(c):
    """The microphone is off: the whole strip red, breathing slowly. Never a listening look."""
    return fill(c.n, c.c["muted"], breathe(c.t, 4.5, 0.40, 0.72))


def disconnected(c):
    """No connection: only three small red marks, pulsing slowly. Unlike idle, unlike an error."""
    k = breathe(c.t, 3.2, 0.15, 0.85)
    out = blank(c.n)
    for centre in (c.n // 6, c.n // 2, c.n - 1 - c.n // 6):
        for i in (centre, centre + 1):
            if 0 <= i < c.n:
                out[i] = scale(c.c["disconnected"], k)
    return out


def error(c):
    """Three short red pulses, a pause, and again."""
    k = 0.10 + 0.90 * blink(c.t, [(0.0, 0.22), (0.38, 0.22), (0.76, 0.22)], 2.6)
    return fill(c.n, c.c["error"], k)


def alarm(c):
    """An alarm or timer is ringing: white, flashing."""
    return fill(c.n, c.c["alarm"], 0.15 + 0.85 * blink(c.t, [(0.0, 0.25)], 0.5))


def shutting_down(c):
    """Whatever was showing fades away."""
    return fade(c.entry or blank(c.n), max(0.0, 1.0 - c.t / 1.2))


def off(c):
    return blank(c.n)
