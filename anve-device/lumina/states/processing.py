"""The request is in: working on it, thinking, searching, running a tool."""
from __future__ import annotations

import math

from ..effects.ambient import breathe
from ..effects.gradients import fill, layer, mix, scale
from ..effects.music import flow
from ..effects.scanning import comet, particles, scanner


def processing(c):
    """Got it, working: violet flowing slowly with a few soft particles."""
    a, b = c.c["think_a"], c.c["think_b"]
    return layer(flow(c.n, c.t, [a, b, scale(a, 0.35)], k=0.40, speed=0.10),
                 particles(c.n, c.t, [b], count=3, speed=7.0, width=1.8, k=0.70))


def thinking(c):
    """The model is thinking: lights circling over deep violet, with slow waves of energy."""
    a, b = c.c["think_a"], c.c["think_b"]
    if c.variant == "flow":
        waves = [scale(mix(a, b, 0.5 + 0.5 * math.sin(i * 0.35 - c.t * 2.2)), 0.22 + 0.40 * (0.5 + 0.5 * math.sin(i * 0.21 + c.t * 1.3)))
                 for i in range(c.n)]
        return layer(waves, particles(c.n, c.t, [b, mix(b, (1.0, 1.0, 1.0), 0.3)], count=4, speed=11.0, width=1.5, k=0.75))
    base = [scale(a, 0.16 + 0.16 * (0.5 + 0.5 * math.sin(i * 0.30 - c.t * 1.6)) * breathe(c.t, 3.0, 0.6, 1.0)) for i in range(c.n)]
    lights = layer(comet(c.n, c.t, b, speed=20.0, tail=12, k=0.95), comet(c.n, c.t + c.n / 40.0, mix(a, b, 0.5), speed=20.0, tail=8, k=0.6))
    return layer(base, lights)


def searching(c):
    """Looking something up: a scanning light sweeping to and fro."""
    col = c.c["search"]
    return layer(layer(fill(c.n, col, 0.12), scanner(c.n, c.t, col, period=1.8, width=3.0, k=0.95)),
                 scanner(c.n, c.t + 0.9, mix(col, c.c["tool"], 0.6), period=1.8, width=1.5, k=0.50))


def tool(c):
    """Doing something: quick particles all running one way."""
    return layer(fill(c.n, c.c["search"], 0.11),
                 particles(c.n, c.t, [c.c["tool"], c.c["search"]], count=6, speed=26.0, width=1.1, k=0.9))
