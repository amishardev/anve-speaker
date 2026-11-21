"""Lights that move with purpose: scanners, comets and streams of particles."""
from __future__ import annotations

import math

from .ambient import glows
from .gradients import blank, ramp, scale


def scanner(n: int, t: float, colour, period: float = 2.2, width: float = 2.5, k: float = 1.0) -> list:
    """A light sweeping end to end and back, slowing at the turns."""
    x = 0.5 - 0.5 * math.cos(2 * math.pi * t / period)
    return glows(n, [(x * (n - 1), width, colour, k)])


def comet(n: int, t: float, colour, speed: float = 18.0, tail: int = 10, k: float = 1.0) -> list:
    """A bright head with a fading tail, going round the strip at `speed` pixels a second."""
    out = blank(n)
    head = (t * speed) % n
    for i in range(tail + 1):
        pos = head - i
        f = pos - math.floor(pos)
        a = int(math.floor(pos)) % n
        w = k * (1 - i / (tail + 1)) ** 2
        for j, share in ((a, 1 - f), ((a + 1) % n, f)):
            c = out[j]
            out[j] = (min(1.0, c[0] + colour[0] * w * share), min(1.0, c[1] + colour[1] * w * share),
                      min(1.0, c[2] + colour[2] * w * share))
    return out


def particles(n: int, t: float, colours: list, count: int = 4, speed: float = 10.0, width: float = 1.4,
              k: float = 1.0) -> list:
    """Soft dots streaming along the strip, each at a slightly different pace."""
    spots = []
    for p in range(count):
        v = speed * (0.8 + 0.4 * ((p * 0.618) % 1.0))
        pos = (p * n / count + t * v) % (n + 8) - 4
        spots.append((pos, width, colours[p % len(colours)], k * (0.6 + 0.4 * ((p * 0.382) % 1.0))))
    return glows(n, spots)


def fill_to(n: int, fraction: float, colours: list, k: float = 1.0, soft: float = 1.5) -> list:
    """The first `fraction` of the strip lit as a gradient, with a soft leading edge."""
    edge = max(0.0, min(1.0, fraction)) * n
    out = []
    for i in range(n):
        a = max(0.0, min(1.0, (edge - i) / soft))
        out.append(scale(ramp(colours, i / max(1, n - 1)), k * a))
    return out
