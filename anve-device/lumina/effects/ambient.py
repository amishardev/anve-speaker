"""Slow, quiet light: breathing and soft glows."""
from __future__ import annotations

import math

from .gradients import blank, scale


def breathe(t: float, period: float = 4.0, lo: float = 0.2, hi: float = 1.0) -> float:
    """A smooth rise and fall between lo and hi, once every `period` seconds."""
    return lo + (hi - lo) * (0.5 - 0.5 * math.cos(2 * math.pi * t / period))


def bump(x: float, centre: float, width: float) -> float:
    """A soft bell 0..1 around `centre` (positions and width in pixels)."""
    d = (x - centre) / max(width, 1e-6)
    return math.exp(-0.5 * d * d) if abs(d) < 4 else 0.0


def glow(n: int, centre: float, width: float, colour, k: float = 1.0) -> list:
    return [scale(colour, k * bump(i, centre, width)) for i in range(n)]


def glows(n: int, spots: list) -> list:
    """Several glows at once: spots are (centre, width, colour, brightness)."""
    out = blank(n)
    for centre, width, colour, k in spots:
        lo, hi = max(0, int(centre - 4 * width)), min(n, int(centre + 4 * width) + 2)
        for i in range(lo, hi):
            b = k * bump(i, centre, width)
            c = out[i]
            out[i] = (min(1.0, c[0] + colour[0] * b), min(1.0, c[1] + colour[1] * b), min(1.0, c[2] + colour[2] * b))
    return out
