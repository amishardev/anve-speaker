"""Getting from one look to the next without a jump."""
from __future__ import annotations


def smooth(x: float) -> float:
    x = 0.0 if x < 0 else 1.0 if x > 1 else x
    return x * x * (3 - 2 * x)


def ease_out(x: float) -> float:
    x = 0.0 if x < 0 else 1.0 if x > 1 else x
    return 1 - (1 - x) ** 3


def crossfade(a: list, b: list, k: float) -> list:
    k = smooth(k)
    j = 1 - k
    return [(p[0] * j + q[0] * k, p[1] * j + q[1] * k, p[2] * j + q[2] * k) for p, q in zip(a, b)]


def fade(frame: list, k: float) -> list:
    return [(c[0] * k, c[1] * k, c[2] * k) for c in frame]
