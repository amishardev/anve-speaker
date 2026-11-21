"""Music: playing or paused."""
from __future__ import annotations

import math

from ..effects import music
from ..effects.ambient import glows
from ..effects.gradients import layer


#: every colour of the rainbow, for the "rainbow" music look (whatever the theme)
RAINBOW = [(1.0, 0.0, 0.0), (1.0, 0.5, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0), (0.0, 1.0, 1.0), (0.0, 0.2, 1.0), (0.7, 0.0, 1.0), (1.0, 0.0, 0.6)]


def playing(c):
    palette = c.c["music"]
    mode = c.variant or c.s["music_mode"]
    if mode == "rainbow":                       # all colours, moving and jumping with the beat
        palette, mode = RAINBOW, "festival"
    if c.lv.music_real:
        return music.reactive(c.n, c.t, palette, c.lv.bass, c.lv.mid, c.lv.treble, c.lv.beat, mode=mode,
                              intensity=c.s["wave_intensity"])
    # the sound itself is not available: the palette flows, and no beat is invented
    k = 0.34 if mode == "ambient" else 0.46
    frame = music.flow(c.n, c.t, palette, k=k, speed=0.05 if mode == "ambient" else 0.08)
    x = (0.5 + 0.42 * math.sin(c.t * 0.5)) * (c.n - 1)
    return layer(frame, glows(c.n, [(x, 4.0, palette[0], 0.35)]))


def paused(c):
    return music.paused(c.n, c.t, c.c["music"][0], k=0.75)
