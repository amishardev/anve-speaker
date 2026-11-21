"""Music as light. With real sound: bass moves the whole strip, treble throws sparks.
Without it: the same palette just flows, and nothing pretends to hear a beat."""
from __future__ import annotations

import math

from .ambient import breathe
from .gradients import add, blank, loop, scale


def flow(n: int, t: float, palette: list, k: float = 0.5, speed: float = 0.06, stretch: float = 1.2) -> list:
    """The palette drifting along the strip."""
    return [scale(loop(palette, i / n * stretch + t * speed), k) for i in range(n)]


def _spark(i: int, step: int) -> float:
    """A repeatable pseudo-random number 0..1 for pixel i at time step `step`."""
    x = math.sin(i * 12.9898 + step * 78.233) * 43758.5453
    return x - math.floor(x)


def reactive(n: int, t: float, palette: list, bass: float, mid: float, treble: float, beat: float,
             mode: str = "smooth", intensity: float = 1.0) -> list:
    """bass, mid, treble: 0..1 smoothed band levels. beat: 1 at a bass hit, decaying to 0."""
    if mode == "ambient":
        return flow(n, t, palette, k=0.40 + 0.30 * bass, speed=0.04)
    festival = mode == "festival"
    centre = (n - 1) / 2
    out = []
    step = int(t * (24 if festival else 14))
    speed = 0.25 if festival else 0.08
    for i in range(n):
        d = abs(i - centre) / max(centre, 1)
        colour = loop(palette, i / n * (2.0 if festival else 1.2) + t * speed + (0.25 * beat if festival else 0.0))
        # bass: a pulse that fills the strip from the middle; mids keep a floor of light
        reach = 0.25 + 0.75 * max(bass, beat)
        body = max(0.0, 1 - d / reach) ** (1.0 if festival else 1.6)
        k = (0.20 + 0.25 * mid) + body * (0.35 + 0.65 * max(bass, beat)) * intensity
        if festival and beat > 0.6 and (i // 6 + step) % 2 == 0:
            k = min(1.0, k + 0.35 * beat)                         # blocks that flip on the beat
        c = scale(colour, min(1.0, k))
        if treble > 0.12 and _spark(i, step) > 1 - 0.20 * treble * intensity:
            c = add(c, scale((1.0, 1.0, 1.0), 0.55 * treble))     # treble sparks
        out.append(c)
    return out


def paused(n: int, t: float, colour, k: float = 0.5) -> list:
    """Two still bars in the middle, like the pause sign, barely breathing."""
    b = k * breathe(t, 5.0, 0.55, 1.0)
    mid = n // 2
    out = blank(n)
    for lo, hi in ((mid - 5, mid - 2), (mid + 2, mid + 5)):
        for i in range(max(0, lo), min(n, hi)):
            out[i] = scale(colour, b)
    return out
