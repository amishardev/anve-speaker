"""From "what to show" to the numbers that go to the strip.

For every frame: draw the current state, cross-fade from the previous one, add what
belongs on top (the red ends while muted, a trace of music under "thinking", a notice),
then fit it to the strip (direction, how many LEDs are used) and make it safe (brightness
limit, power limit) and smooth (gamma, dithering).

How bright depends on what is shown: `brightness` at rest, `active_brightness` from the
wake word to the end of the reply, `music_brightness` while music plays. The brightness
rises with the look's own cross-fade (at once for the wake word) and falls slowly, over
DIM_S, so the strip settles back after a conversation and a short gap between two lively
looks is not a dip.
"""
from __future__ import annotations

import math

from .effects.gradients import blank, layer, scale, to_hex
from .effects.pulse import blink
from .effects.transitions import crossfade
from .states import RENDERERS, SLOW, VARIANTS, Ctx, Levels

IDLE_MA, FULL_MA = 1.0, 20.0        # per LED doing nothing; per colour channel at full
#: the looks of a conversation (and an alarm): shown at `active_brightness`
ACTIVE = frozenset({"WAKE_WORD_DETECTED", "LISTENING", "RECORDING", "PROCESSING", "THINKING", "SEARCHING",
                    "TOOL_EXECUTING", "SPEAKING", "ALARM"})
#: `bright_when = "listening"`: only while the box listens to the person (and an alarm)
LISTENING_ONLY = frozenset({"WAKE_WORD_DETECTED", "LISTENING", "RECORDING", "ALARM"})
MUSIC = frozenset({"MUSIC_PLAYING"})
DIM_S = 1.5                          # seconds for the brightness to fall from 100 % to 0


class Compositor:
    def __init__(self, settings: dict, colours: dict):
        self.levels = Levels()
        self._visual = None
        self._entered = 0.0
        self._from: list | None = None       # the frame we are fading away from
        self._fade_t0, self._fade_s = 0.0, 0.0
        self._entry: list = []
        self._last: list = []
        self._bright = None                  # the brightness in use now (it follows the look)
        self._b_at = None
        self.estimate_ma = 0.0
        self.limited = False
        self.configure(settings, colours)

    def target_brightness(self, visual: str) -> float:
        s = self.s
        active = LISTENING_ONLY if s.get("bright_when") == "listening" else ACTIVE
        k = s["active_brightness"] if visual in active else s["music_brightness"] if visual in MUSIC else s["brightness"]
        return min(k, s["max_brightness"])

    def configure(self, settings: dict, colours: dict):
        self.s, self.colours = settings, colours
        self._bright = None                  # a changed setting shows at once, not after a fade
        strip = settings["strip"]
        self.count, self.n, self.offset = strip["count"], strip["active"], strip["offset"]
        self.order = ["RGB".index(ch) for ch in strip["order"].upper()]
        # a fixed pattern of rounding thresholds: neighbouring LEDs round differently, so a
        # slow fade steps through many more than 51 levels without any flicker
        self._dither = [[((i * 3 + ch) * 0.6180339887) % 1.0 for ch in range(3)] for i in range(self.count)]
        if len(self._last) != self.n:
            self._last = blank(self.n)
            self._from = None

    def slow(self, visual: str) -> bool:
        return visual in SLOW and self._from is None

    def variant(self, visual: str) -> str:
        v = self.s["animations"].get(visual, "")
        return v if v in VARIANTS.get(visual, []) else ""

    def render(self, view: dict, now: float) -> list:
        """The frame to show now, as colours 0..1 for the LEDs in use."""
        visual = view["visual"]
        if visual != self._visual:
            self._from = list(self._last)
            self._fade_t0 = now
            self._fade_s = 0.06 if visual in ("WAKE_WORD_DETECTED", "ERROR", "ALARM") else self.s["transition_s"]
            if visual == "SHUTTING_DOWN":
                self._fade_s = 0.0
            self._entry = list(self._last)
            self._visual, self._entered = visual, now
        self._follow_brightness(visual, now)
        t = (now - self._entered) * (1.0 if visual in ("SHUTTING_DOWN", "WAKE_WORD_DETECTED", "STARTING") else self.s["speed"])
        ctx = Ctx(self.n, t, self.colours, self.levels, self.s, self.variant(visual), view.get("progress"), self._entry)
        frame = RENDERERS.get(visual, RENDERERS["OFF"])(ctx)
        if view.get("media_under") and self.s["blend_media"]:
            under = RENDERERS["MUSIC_PLAYING"](Ctx(self.n, now * self.s["speed"], self.colours, self.levels, self.s, self.variant("MUSIC_PLAYING")))
            frame = layer(frame, [scale(p, 0.30) for p in under])
        if self._from is not None:
            k = 1.0 if self._fade_s <= 0 else (now - self._fade_t0) / self._fade_s
            if k >= 1.0:
                self._from = None
            else:
                frame = crossfade(self._from, frame, k)
        level = view.get("pattern")
        if level is not None:
            # a blink the box's cloud asked for: the whole strip in the "wake" colour at that
            # brightness, with what was showing dimmed underneath so the blink reads clearly
            glow = scale(self.colours["wake"], 0.9 * level)
            frame = [(min(1.0, p[0] * 0.25 + glow[0]), min(1.0, p[1] * 0.25 + glow[1]), min(1.0, p[2] * 0.25 + glow[2])) for p in frame]
        if view.get("notice"):
            k = 0.35 * blink(now, [(0.0, 0.35)], 0.35)
            frame = layer(frame, [scale(self.colours["notice"], k)] * self.n)
        if view.get("privacy"):                     # muted, but something else is showing: red ends
            red = scale(self.colours["muted"], 0.6)
            for i in list(range(0, min(4, self.n))) + list(range(max(0, self.n - 4), self.n)):
                frame[i] = red
        self._last = frame
        return frame

    def _follow_brightness(self, visual: str, now: float):
        target = self.target_brightness(visual)
        dt = 0.0 if self._b_at is None else max(0.0, min(0.5, now - self._b_at))
        self._b_at = now
        b = self._bright
        if b is None or (target > b and self._fade_s <= 0.1):
            b = target                                       # the first frame; the wake word: at once
        elif target > b:
            b = min(target, b + dt / max(self._fade_s, 0.05))
        else:
            b = max(target, b - dt / DIM_S)
        self._bright = b

    def output(self, frame: list) -> tuple:
        """(wire, preview): `wire` is one (a, b, c) of 0..255 per LED in the strip's own
        colour order; `preview` is the same picture as text for the dashboard."""
        s = self.s
        if s["direction"] == "reverse":
            frame = frame[::-1]
        full = blank(self.count)
        full[self.offset:self.offset + self.n] = frame[:self.n]
        bright = min(self._bright if self._bright is not None else s["brightness"], s["max_brightness"])
        gamma = s["gamma"]
        lit = [(c[0] ** gamma * bright, c[1] ** gamma * bright, c[2] ** gamma * bright) for c in full]
        ma = self.count * IDLE_MA + FULL_MA * sum(c[0] + c[1] + c[2] for c in lit)
        self.limited = ma > s["power_limit_ma"]
        if self.limited:
            k = max(0.0, (s["power_limit_ma"] - self.count * IDLE_MA) / (ma - self.count * IDLE_MA))
            lit = [(c[0] * k, c[1] * k, c[2] * k) for c in lit]
            ma = s["power_limit_ma"]
        self.estimate_ma = ma
        wire, o = [], self.order
        for i, c in enumerate(lit):
            d = self._dither[i]
            v = [0 if x * 255.0 < 0.08 else min(255, int(math.floor(x * 255.0 + d[ch]))) for ch, x in enumerate(c)]
            wire.append((v[o[0]], v[o[1]], v[o[2]]))
        return wire, "".join(to_hex(c)[1:] for c in full)
