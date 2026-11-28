"""The status light: one table of patterns, one pattern per mode.

The exact look depends on the LED we fit (PLAN.md 4.3), so everything about it
lives here. Each pattern is a loop of steps: (brightness 0-1, milliseconds).
With smooth=True the light glides between steps instead of switching.
"""

from __future__ import annotations

from dataclasses import dataclass

from .model import OfflineReason, State


@dataclass(frozen=True)
class Pattern:
    name: str
    label: str  # plain words, used in docs and the simulator
    steps: tuple[tuple[float, int], ...]
    smooth: bool = False

    @property
    def cycle_ms(self) -> int:
        return sum(ms for _, ms in self.steps)


_SHORT = 150  # one short blink, ms
_PAUSE = 1050  # the gap after a group of short blinks, ms

PATTERNS: dict[str, Pattern] = {
    p.name: p
    for p in (
        Pattern("off", "Off", ((0.0, 1000),)),
        Pattern("setup_pulse", "Slow pulse", ((0.12, 1200), (1.0, 1200)), smooth=True),
        Pattern("fast_blink", "Fast blink", ((1.0, _SHORT), (0.0, _SHORT))),
        Pattern(
            "double_blink",
            "Two short blinks",
            ((1.0, _SHORT), (0.0, _SHORT), (1.0, _SHORT), (0.0, _PAUSE)),
        ),
        Pattern("solid", "Solid", ((1.0, 1000),)),
        Pattern("slow_blink", "Slow blink", ((1.0, 1000), (0.0, 1000))),
        Pattern(
            "triple_blink",
            "Three short blinks",
            (
                (1.0, _SHORT),
                (0.0, _SHORT),
                (1.0, _SHORT),
                (0.0, _SHORT),
                (1.0, _SHORT),
                (0.0, _PAUSE),
            ),
        ),
        Pattern("rapid_flash", "Rapid flash", ((1.0, 60), (0.0, 60))),
        # Asked for from the cloud ("Find my box"); not a mode of its own.
        Pattern(
            "identify",
            "Find-me flicker",
            ((1.0, 80), (0.0, 80), (1.0, 80), (0.0, 80), (1.0, 80), (0.0, 80), (1.0, 80), (0.0, 640)),
        ),
    )
}

IDENTIFY_SECONDS = 10.0

STATE_PATTERNS: dict[State, str] = {
    State.SETUP: "setup_pulse",
    State.CONNECTING: "fast_blink",
    State.ONLINE_UNCLAIMED: "double_blink",
    State.READY: "solid",
    State.RESETTING: "rapid_flash",
}

OFFLINE_PATTERNS: dict[OfflineReason, str] = {
    OfflineReason.NO_NETWORK: "slow_blink",
    OfflineReason.ANVE_UNREACHABLE: "triple_blink",
}

# While the button is held past the 5 s mark, the light previews setup mode:
# "let go now to change Wi-Fi".
HOLD_SETUP_HINT = "setup_pulse"


def as_json(name: str) -> dict:
    """A pattern in the shape the setup page and simulator draw (led.js)."""
    pattern = PATTERNS[name]
    return {
        "name": pattern.name,
        "label": pattern.label,
        "steps": [list(step) for step in pattern.steps],
        "smooth": pattern.smooth,
    }


def pattern_for(state: State | None, offline_reason: OfflineReason | None = None) -> str:
    if state is None:
        return "off"
    if state is State.OFFLINE:
        return OFFLINE_PATTERNS[offline_reason or OfflineReason.NO_NETWORK]
    return STATE_PATTERNS[state]
