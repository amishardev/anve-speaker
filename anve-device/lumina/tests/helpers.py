"""Shared by the tests: a clock that only moves when told to, and a fresh setup."""
from __future__ import annotations

import pathlib
import tempfile

from lumina.config import Settings
from lumina.controller import Controller
from lumina.hardware.led_simulator import Simulator
from lumina.state_manager import StateManager


class Clock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def step(self, seconds: float):
        self.t += seconds


def manager(**kw):
    clock = Clock()
    return StateManager(clock=clock, wall=clock, **kw), clock


def settings(**changes) -> Settings:
    s = Settings(pathlib.Path(tempfile.mkdtemp(prefix="lumina-test-")) / "settings.json")
    if changes:
        s.update(changes, trusted=True)
    return s


def controller(**changes):
    clock = Clock()
    s = settings(**changes)
    c = Controller(s, strip=Simulator(s.values["strip"]["count"], s.values["strip"]["order"]), clock=clock)
    return c, clock


def run(c: Controller, clock: Clock, seconds: float, fps: int = 30) -> list:
    """Advance the controller frame by frame; the previews of every frame come back."""
    out = []
    for _ in range(max(1, int(seconds * fps))):
        clock.step(1 / fps)
        c.step()
        out.append(c.preview)
    return out


def online(c: Controller, clock: Clock, **dims):
    """The assistant reports that it is up (and whatever else is given)."""
    status = {"connection": "connected", "setup": "complete", "microphone": "enabled", "voice": "idle",
              "processing": "none", "speech": "idle", "media": "stopped", "fault": "none"}
    status.update(dims)
    c.bus.publish({"type": "status", "status": status, "source": "assistant"})
    clock.step(2.0)                # past the start-up greeting
    c.step()
