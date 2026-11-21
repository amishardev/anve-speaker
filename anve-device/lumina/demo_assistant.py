"""A pretend assistant, to see every state without the real one:

    python -m lumina.demo_assistant            one conversation, music, a mute, an error
    python -m lumina.demo_assistant --loop     again and again

It talks to LUMINA exactly the way a real assistant does (`AssistantLEDs`), so what the
strip shows is LUMINA's reaction to real events, in order, with believable timing. Do not
run it next to the real assistant: both would be reporting at once.
"""
from __future__ import annotations

import math
import sys
import time

from .assistant_client import AssistantLEDs

SCRIPT = [
    ("not set up yet", 4.0, lambda a: a.setup_required()),
    ("being set up", 4.0, lambda a: a.setup_started()),
    ("set up; connecting", 3.0, lambda a: (a.setup_completed(), a.connecting())),
    ("ready", 4.0, lambda a: a.ready()),
    ("wake word", 0.6, lambda a: a.wake_word_detected()),
    ("listening", 4.0, lambda a: a.listening()),
    ("working on it", 1.5, lambda a: a.processing()),
    ("thinking", 3.0, lambda a: a.thinking()),
    ("searching", 2.5, lambda a: a.searching()),
    ("running a tool", 2.0, lambda a: a.tool_executing()),
    ("speaking", 5.0, lambda a: a.speaking()),
    ("ready", 2.0, lambda a: a.ready()),
    ("music", 6.0, lambda a: a.music_playing()),
    ("music paused", 3.0, lambda a: a.music_paused()),
    ("music again", 3.0, lambda a: a.music_resumed()),
    ("music stopped", 2.0, lambda a: a.music_stopped()),
    ("microphone off", 4.0, lambda a: a.muted()),
    ("microphone on", 2.0, lambda a: a.unmuted()),
    ("connection lost", 4.0, lambda a: a.disconnected()),
    ("back", 2.0, lambda a: (a.connecting(), a.ready())),
    ("an error", 4.0, lambda a: a.error("demonstration", code="demo")),
    ("cleared", 2.0, lambda a: (a.error_cleared(), a.ready())),
]


def run(loop: bool = False, leds: AssistantLEDs | None = None, pace: float = 1.0):
    leds = leds or AssistantLEDs(source="demo")
    while True:
        for name, seconds, do in SCRIPT:
            print(f"  {name}")
            do(leds)
            end = time.monotonic() + seconds * pace
            while time.monotonic() < end:
                t = time.monotonic()
                if name == "listening":                     # a pretend voice, as a real assistant would measure it
                    leds.level(mic=max(0.0, 0.45 + 0.4 * math.sin(t * 7.0) * math.sin(t * 1.7)))
                elif name == "speaking":
                    leds.level(out=max(0.0, 0.5 + 0.45 * math.sin(t * 9.0) * math.sin(t * 2.1)))
                time.sleep(0.05)
        if not loop:
            break
    leds.close()


if __name__ == "__main__":
    run(loop="--loop" in sys.argv)
