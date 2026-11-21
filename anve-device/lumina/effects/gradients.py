"""Colour arithmetic. A colour is (r, g, b) with each part 0..1; a frame is a list of them."""
from __future__ import annotations

BLACK = (0.0, 0.0, 0.0)
WHITE = (1.0, 1.0, 1.0)


def rgb(hex_: str) -> tuple:
    h = hex_.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def to_hex(c) -> str:
    return "#" + "".join(f"{max(0, min(255, round(v * 255))):02x}" for v in c)


def mix(a, b, k: float) -> tuple:
    k = 0.0 if k < 0 else 1.0 if k > 1 else k
    return (a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k)


def scale(c, k: float) -> tuple:
    return (c[0] * k, c[1] * k, c[2] * k)


def add(a, b) -> tuple:
    return (min(1.0, a[0] + b[0]), min(1.0, a[1] + b[1]), min(1.0, a[2] + b[2]))


def blank(n: int) -> list:
    return [BLACK] * n


def fill(n: int, c, k: float = 1.0) -> list:
    return [scale(c, k)] * n


def ramp(colours: list, x: float) -> tuple:
    """A point x (0..1) on a gradient through `colours`."""
    if len(colours) == 1:
        return colours[0]
    x = max(0.0, min(1.0, x)) * (len(colours) - 1)
    i = min(int(x), len(colours) - 2)
    return mix(colours[i], colours[i + 1], x - i)


def loop(colours: list, x: float) -> tuple:
    """Like ramp, but the gradient closes on itself, so it can scroll for ever."""
    x = x % 1.0 * len(colours)
    i = int(x) % len(colours)
    return mix(colours[i], colours[(i + 1) % len(colours)], x - int(x))


def layer(base: list, top: list) -> list:
    """Add one frame of light on top of another."""
    return [add(a, b) for a, b in zip(base, top)]
