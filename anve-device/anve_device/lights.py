"""The speaker's side of LUMINA, the light strip (see ../lumina/README.md).

The speaker does not drive the LEDs itself any more. It tells LUMINA, a separate small
program, what it is really doing, and LUMINA draws it. If LUMINA is not running, nothing
here waits or fails: every message is a UDP datagram that is simply lost.

`Lights` has the same four methods the old in-process `Ring` had (set, level, flash,
close), so the speaker's code calls it in the same places:

    set()     main.Device.look()   every time the speaker's state changes; the whole state is sent
    level()   main.Device.mic_loop()   the microphone's level, or the reply's while it is spoken
    flash()   the wake word was heard (white); a confirmation is waiting (amber)
    close()   the speaker is stopping

and it repeats the whole state every second, which is how LUMINA knows the speaker is
alive and how anything a lost message left wrong gets put right.

What the speaker's state means to LUMINA (the dimensions are in lumina/state_manager.py):

    not connected to ANVE            connection: connecting (never was) / disconnected (was)
    the server refused the key       fault: error, code "halted"
    microphone or speaker missing    fault: hardware, code "microphone" / "speaker"
    the server reports an error      fault: error, code "server"
    muted (button, app or server)    microphone: muted
    the server is listening, or the action button is held     voice: listening
    a meeting is being recorded      voice: recording
    the server is thinking           processing: thinking
    a tool is running (a confirmed action, a local skill)     processing: tool
    the reply is being played        speech: speaking
    music or radio from the speaker's own player              media: playing / paused
    an alarm or timer is ringing     alarm: ringing

There is no signal from the server for "searching the web", so that look is never shown by
the speaker itself (it can be shown through LUMINA's API or its dashboard).
"""
from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger("anve.lights")
STARTING_S = 45.0        # while starting, a device not found yet is "connecting", not a fault


class Lights:
    def __init__(self, dev, port: int = 8741, every: float = 1.0):
        from lumina.assistant_client import AssistantLEDs
        self.dev, self.leds = dev, AssistantLEDs(port=port)
        self._born = time.monotonic()
        self._was_up = False
        self._last: dict | None = None
        self._stop = threading.Event()
        self._every = every
        threading.Thread(target=self._beat, name="anve-lights", daemon=True).start()

    def status(self) -> dict:
        """The speaker's state in LUMINA's words. It only reads the speaker's own fields."""
        d = self.dev
        now = time.monotonic()
        s = d.srv.get("s", "idle")
        look = getattr(d, "_look", "idle")
        st = {"setup": "complete", "fault": "none", "voice": "idle", "processing": "none", "speech": "idle",
              "media": "stopped", "alarm": "off"}
        self._was_up = self._was_up or bool(d.link)
        starting = now - self._born < STARTING_S
        st["connection"] = "connected" if d.link else ("disconnected" if self._was_up else "connecting")
        if d.client.halted:
            st["fault"] = "error"
            st["error"] = {"code": "halted", "message": "ANVE refused this box. It needs setting up again."}
        elif d.link and not (d.mic_ok and d.speaker_ok):
            if starting:
                st["connection"] = "connecting"
            else:
                missing = "microphone" if not d.mic_ok else "speaker"
                st["fault"] = "hardware"
                st["error"] = {"code": missing, "message": f"The {missing} is not connected."}
        elif d.link and s == "error":
            st["fault"] = "error"
            st["error"] = {"code": "server", "message": "ANVE reported an error."}
        st["microphone"] = "muted" if (d.muted or s == "muted") else "enabled"
        if now < d.alarm_until:
            st["alarm"] = "ringing"
        if look == "listening":
            st["voice"] = "listening"
        elif look == "recording":
            st["voice"] = "recording"
        if look == "speaking":
            st["speech"] = "speaking"
        elif d.tool_pending and look != "listening":
            st["processing"] = "tool"
        elif s == "thinking":
            st["processing"] = "thinking"
        playing = d.media.now() if d.media else None
        if playing:
            st["media"] = "paused" if playing.get("paused") else "playing"
        return st

    # ---------- the four calls the speaker makes ----------

    def set(self, look: str = "", cloud: bool = False):
        self._report()

    def level(self, v: float):
        if getattr(self.dev, "_look", "") == "speaking":
            self.leds.level(out=v)
        else:
            self.leds.level(mic=v)

    def flash(self, rgb=(255, 255, 255), seconds: float = 0.15):
        if tuple(rgb) == (255, 255, 255):
            self.leds.wake_word_detected()
        else:
            self.leds.notice()

    def close(self):
        self._stop.set()
        self.leds.shutting_down()
        self.leds.close()

    # ---------- inside ----------

    def _report(self, force: bool = False):
        try:
            st = self.status()
        except Exception:                        # noqa: BLE001  (never let the lights hurt the speaker)
            log.exception("could not read the speaker's state for the lights")
            return
        if force or st != self._last:
            self._last = st
            self.leds.status(**st)

    def _beat(self):
        while not self._stop.wait(self._every):
            self._report(force=True)
