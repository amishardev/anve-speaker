"""Awake, set up, online, waiting for the wake word. Deliberately almost nothing."""
from __future__ import annotations

import math

from ..effects.ambient import breathe, glows
from ..effects.gradients import blank, fill, mix, scale


def ready(c):
    col = c.c["ready"]
    v = c.variant
    if v == "off":
        return blank(c.n)
    if v == "breathe":
        return fill(c.n, col, breathe(c.t, 6.0, 0.08, 0.20))
    if v == "dots":
        k = breathe(c.t, 6.0, 0.18, 0.45)
        return [scale(col, k) if i % 10 == 5 else (0.0, 0.0, 0.0) for i in range(c.n)]
    if v == "glow":
        # the whole strip lit evenly, a faint slow wave running along it: how bright is the
        # `brightness` setting's business (the strip "at 20 %" when nothing is going on)
        return [scale(col, 0.82 + 0.18 * (0.5 + 0.5 * math.sin(i * 0.18 - c.t * 0.6))) for i in range(c.n)]
    # drift: one soft glow wandering slowly along the strip, with a fainter one mirrored
    x = (0.5 + 0.38 * math.sin(c.t * 0.21)) * (c.n - 1)
    return glows(c.n, [(x, 3.2, col, breathe(c.t, 5.5, 0.22, 0.48)),
                       (c.n - 1 - x, 2.0, mix(col, c.c["listen_low"], 0.5), breathe(c.t + 2.0, 7.0, 0.08, 0.20))])
