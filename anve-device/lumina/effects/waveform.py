"""Sound drawn as light: a glowing middle with waves leaving it."""
from __future__ import annotations

import math

from .gradients import mix, scale


def voice(n: int, t: float, level: float, low, high, peak, intensity: float = 1.0, rest: float = 0.10) -> list:
    """Listening or speaking. `level` (0..1, already smoothed) widens and brightens the
    middle; waves run from the middle to both ends, so it reads the same from either side.
    With no sound it rests as a dim, slowly moving glow."""
    mid = (n - 1) / 2
    level = max(0.0, min(1.0, level))
    reach = 0.22 + 0.78 * level                       # how far from the middle the light gets
    out = []
    for i in range(n):
        d = abs(i - mid) / max(mid, 1)                # 0 in the middle, 1 at the ends
        body = max(0.0, 1 - d / reach) ** 1.4         # the glowing region
        w1 = 0.5 + 0.5 * math.sin(d * 9.0 - t * 5.2)
        w2 = 0.5 + 0.5 * math.sin(d * 17.0 - t * 8.3 + 1.3)
        wave = (0.55 * w1 + 0.45 * w2) * intensity
        k = rest * (0.6 + 0.4 * w1) * (1 - 0.5 * d) + body * level * (0.45 + 0.55 * wave)
        colour = mix(low, high, min(1.0, body + 0.3 * wave))
        if body * level > 0.55:
            colour = mix(colour, peak, (body * level - 0.55) / 0.45 * 0.9)
        out.append(scale(colour, min(1.0, k)))
    return out


def bars(n: int, level: float, base, bar, floor: float = 0.28) -> list:
    """The plain version: a bar growing from the middle with the level."""
    mid = (n - 1) / 2
    half = level * n / 2
    return [bar if abs(i - mid) <= half else scale(base, floor) for i in range(n)]


def speech_like(t: float) -> float:
    """A level that moves like talking, for when the real sound cannot be measured. It is
    a stand-in driven by time only, never by the microphone."""
    syllable = 0.5 + 0.5 * math.sin(t * 9.4) * math.sin(t * 2.3 + 0.7)
    phrase = 0.5 + 0.5 * math.sin(t * 0.9)
    return max(0.0, min(1.0, 0.18 + 0.62 * syllable * (0.45 + 0.55 * phrase)))
