"""Light that travels: single pulses, and pulses that spread from the middle."""
from __future__ import annotations

from .ambient import glows
from .gradients import blank
from .transitions import ease_out


def travel(n: int, t: float, seconds: float, colour, width: float = 3.0, k: float = 1.0, reverse: bool = False) -> list:
    """One pulse crossing the strip in `seconds`; nothing before 0 or after the end."""
    if not 0.0 <= t <= seconds:
        return blank(n)
    x = t / seconds
    pos = (1 - x if reverse else x) * (n + 6 * width) - 3 * width
    return glows(n, [(pos, width, colour, k)])


def burst(n: int, t: float, seconds: float, colour, width: float = 3.5, k: float = 1.0) -> list:
    """Two pulses leaving the middle for both ends: the whole strip is crossed in `seconds`."""
    if not 0.0 <= t <= seconds:
        return blank(n)
    x = ease_out(t / seconds)
    mid, reach = (n - 1) / 2, (n / 2 + 2 * width) * x
    fade = k * (1.0 - 0.35 * x)
    return glows(n, [(mid - reach, width, colour, fade), (mid + reach, width, colour, fade)])


def blink(t: float, pattern: list, period: float) -> float:
    """Brightness 0..1 for a repeating pattern of (start, length) flashes with soft edges."""
    ph = t % period
    for start, length in pattern:
        if start <= ph < start + length:
            x = (ph - start) / length
            return min(1.0, 4 * x, 4 * (1 - x))
    return 0.0
