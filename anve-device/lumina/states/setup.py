"""Before the assistant is ready: starting, not set up yet, being set up, connecting."""
from __future__ import annotations

from ..effects.ambient import breathe, glows
from ..effects.gradients import fill, layer, mix, scale
from ..effects.pulse import burst, travel
from ..effects.scanning import comet, fill_to, particles


def starting(c):
    """The start-up greeting: light spreads from the middle, once."""
    return layer(fill(c.n, c.c["connect"], 0.10), burst(c.n, c.t, 1.1, c.c["wake"], width=4.0, k=0.9))


def required(c):
    """Not set up yet: a slow amber breath, and now and then a soft pulse passing by."""
    base = fill(c.n, c.c["setup"], breathe(c.t, 5.0, 0.14, 0.42))
    return layer(base, travel(c.n, c.t % 7.0, 2.4, c.c["setup_b"], width=3.0, k=0.55))


def in_progress(c):
    """Being set up. With a real percentage: that share of the strip is filled. Without
    one: a light keeps circling and no share is shown, because none is known."""
    a, b = c.c["progress_a"], c.c["progress_b"]
    track = fill(c.n, a, 0.12)
    if c.progress is None:
        return layer(track, layer(comet(c.n, c.t, b, speed=24.0, tail=16, k=0.9),
                                  comet(c.n, c.t + c.n / 48.0, a, speed=24.0, tail=10, k=0.5)))
    done = fill_to(c.n, c.progress, [a, b], k=0.70)
    edge = max(1.0, c.progress * c.n)
    sparkle = glows(c.n, [((c.t * 16.0) % edge, 2.0, mix(b, (1.0, 1.0, 1.0), 0.5), 0.45)]) if c.progress < 1 else []
    return layer(layer(track, done), sparkle) if sparkle else layer(track, done)


def connecting(c):
    """Reaching the internet and the assistant's services: cyan dots travelling, calmly."""
    col = c.c["connect"]
    base = fill(c.n, col, breathe(c.t, 4.0, 0.07, 0.14))
    dots = particles(c.n, c.t, [col, mix(col, c.c["progress_a"], 0.5)], count=3, speed=9.0, width=1.6, k=0.85)
    return layer(layer(base, dots), [scale(p, 1.0) for p in travel(c.n, c.t % 4.5, 2.0, col, width=4.0, k=0.40)])
