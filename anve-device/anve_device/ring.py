"""What the LED strip shows. One look per state, so the device can be read at a glance:

    idle        a faint steady teal                      (connected, waiting for the wake word)
    listening   blue, with a white bar that follows your voice
      + cloud   the same in cyan: your audio is going to a cloud model right now
    thinking    a purple dot circling
    speaking    green, breathing with the reply's loudness
    muted       solid red
    recording   amber, slowly pulsing, with the voice bar   (a meeting is being recorded)
    error       red blinking slowly: no server / no internet / a device is missing
    halted      red-orange double blink: the server refused and a person must act
    alarm       white flashing

A thread redraws at about 30 frames a second; `set()` and `level()` are safe to call
from the asyncio loop.
"""
import math
import threading
import time

import numpy as np

LOOKS = ("idle", "listening", "thinking", "speaking", "muted", "error", "halted",
         "recording", "alarm", "off")


class Ring:
    def __init__(self, strip, fps: int = 30):
        self.strip, self.n, self.fps = strip, strip.count, fps
        self.look, self.cloud = "error", False
        self._level = 0.0            # 0..1, smoothed
        self._target = 0.0
        self._flash_until = 0.0
        self._flash_rgb = (0, 255, 0)
        self._run = True
        self._t = threading.Thread(target=self._loop, name="anve-leds", daemon=True)
        self._t.start()

    def set(self, look: str, cloud: bool = False):
        self.look, self.cloud = (look if look in LOOKS else "error"), bool(cloud)

    def level(self, v: float):
        """Voice level 0..1 for the bar (call it per audio frame)."""
        self._target = max(0.0, min(1.0, v))

    def flash(self, rgb=(0, 255, 0), seconds: float = 0.35):
        self._flash_rgb, self._flash_until = rgb, time.monotonic() + seconds

    def close(self):
        self._run = False
        self._t.join(timeout=1)
        self.strip.close()

    # ---------- drawing ----------

    def _bar(self, base, bar, floor: float = 0.25):
        px = np.tile(np.array(base, np.float32) * floor, (self.n, 1))
        lit = int(round(self._level * self.n))
        if lit:
            # grow from the middle outwards, so it reads the same from any side of a cube
            mid = self.n // 2
            lo, hi = max(0, mid - lit // 2), min(self.n, mid + (lit + 1) // 2)
            px[lo:hi] = bar
        return px

    def _frame(self, t: float):
        n, look = self.n, self.look
        if t < self._flash_until:
            return np.tile(np.array(self._flash_rgb, np.float32), (n, 1))
        if look == "off":
            return np.zeros((n, 3), np.float32)
        if look == "idle":
            return np.tile(np.array((0, 40, 34), np.float32), (n, 1))
        if look == "listening":
            return self._bar((0, 200, 255) if self.cloud else (0, 60, 255), (255, 255, 255))
        if look == "recording":
            k = 0.55 + 0.45 * math.sin(t * 2.0)
            return self._bar((255 * k, 120 * k, 0), (255, 255, 255), floor=0.6)
        if look == "thinking":
            px = np.tile(np.array((40, 0, 70), np.float32), (n, 1))
            head = int(t * 18) % n
            for i, k in enumerate((1.0, 0.6, 0.3, 0.12)):
                px[(head - i) % n] = np.array((190, 60, 255), np.float32) * k
            return px
        if look == "speaking":
            k = 0.30 + 0.70 * self._level
            return np.tile(np.array((0, 255, 70), np.float32) * k, (n, 1))
        if look == "muted":
            return np.tile(np.array((255, 0, 0), np.float32), (n, 1))
        if look == "alarm":
            on = int(t * 4) % 2 == 0
            return np.tile(np.array((255, 255, 255) if on else (40, 40, 40), np.float32), (n, 1))
        if look == "halted":
            ph = (t % 2.0)
            on = ph < 0.15 or 0.3 < ph < 0.45
            return np.tile(np.array((255, 50, 0) if on else (0, 0, 0), np.float32), (n, 1))
        on = (t % 1.6) < 0.8                                   # error
        return np.tile(np.array((200, 0, 0) if on else (25, 0, 0), np.float32), (n, 1))

    def _loop(self):
        while self._run:
            t = time.monotonic()
            # fast attack, slow release: the bar jumps with a word and falls back gently
            k = 0.6 if self._target > self._level else 0.12
            self._level += (self._target - self._level) * k
            try:
                self.strip.show(self._frame(t))
            except Exception:                                # noqa: BLE001
                pass
            time.sleep(1 / self.fps)
