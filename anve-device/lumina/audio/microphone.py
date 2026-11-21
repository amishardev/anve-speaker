"""The microphone's level, for the listening animation.

LUMINA does not open the microphone itself: the assistant already has it, and two programs
on one microphone is how wake words get missed. The assistant sends its own measurement
(`AssistantLEDs.level(mic=...)`), and this keeps it smooth. If no level arrives for a
moment, the level is unknown and the animation rests; it is never guessed.
"""
from __future__ import annotations

from .audio_analyser import Smoother

FRESH_S = 0.6            # a level older than this is no longer the truth


class Level:
    """One smoothed level with a "do we actually know it" flag."""

    def __init__(self, attack: float, release: float, gate: float):
        self._s = Smoother(attack, release, gate)
        self._at = -1e9

    def feed(self, value: float, now: float, sensitivity: float = 1.0):
        self._s.feed(value, sensitivity)
        self._at = now

    def read(self, now: float) -> tuple:
        """(level, real)"""
        if now - self._at > FRESH_S:
            self._s.decay()
            return self._s.value, False
        return self._s.value, True


def microphone() -> Level:
    return Level(attack=0.60, release=0.12, gate=0.05)
