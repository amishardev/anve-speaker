"""LUMINA itself: the one process that owns the strip.

    python -m lumina                 run (the strip named in the settings; the simulator by default)
    python -m lumina --preview       also draw the strip in this terminal
    python -m lumina --set strip.driver=spi      change a stored setting and leave
    python -m lumina --key           print the dashboard key and leave

It takes in what the assistant reports, keeps the state, draws about 30 frames a second
and shows them, and serves the dashboard. Nothing in it can hold the assistant up: they
only share a UDP port.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
import time
from collections import deque
from pathlib import Path

from . import __version__
from .audio.microphone import microphone
from .audio.speaker import Loopback, speaker
from .compositor import Compositor
from .config import Settings, dashboard_key, themes
from .event_bus import EventBus
from .hardware.led_simulator import Simulator, ansi
from .hardware.pi5neo_driver import open_strip
from .state_manager import StateManager
from .states import VARIANTS

log = logging.getLogger("lumina")
MUSIC_FRESH_S = 0.6


class Controller:
    def __init__(self, settings: Settings, *, strip=None, clock=time.monotonic):
        self.settings, self.clock = settings, clock
        s = settings.values
        self.bus = EventBus()
        self.state = StateManager(clock=clock, assistant_timeout=s["assistant_timeout_s"],
                                  startup_grace=s["startup_grace_s"], muted_overlay=s["muted_overlay"])
        self.comp = Compositor(s, settings.colours())
        if strip is not None:
            self.strip, self.hardware_problem = strip, ""
        else:
            self.strip, self.hardware_problem = open_strip(s)
        if settings.problem:
            log.warning(settings.problem)
        if self.hardware_problem:
            log.error("%s: carrying on without the real strip", self.hardware_problem)
        self.mic, self.out = microphone(), speaker()
        self._music = {"bass": 0.0, "mid": 0.0, "treble": 0.0, "beat": 0.0}
        self._music_at = -1e9
        # levels of what the speaker plays, held back until it is heard (voice_delay_s,
        # music_delay_s): (when, level) in the order they came, so the oldest is first
        self._later_out: deque = deque(maxlen=400)
        self._later_music: deque = deque(maxlen=400)
        self._pattern = None                       # a blink the box's cloud asked for (see handle)
        self.loopback = Loopback(self.bus.publish)
        self.dashboard = None
        self.extras: list = []                     # MQTT, Home Assistant
        self.preview = ""
        self._wire = None
        self._errors = 0
        self._started = clock()
        self._marker_at = 0.0
        self._stopping = False
        self.frames = self.shown = 0

    # ---------- what comes in ----------

    def handle(self, m: dict):
        kind, source = m.get("type"), str(m.get("source") or "api")
        if kind == "level":
            now, s = self.clock(), self.settings.values
            if isinstance(m.get("mic"), (int, float)):
                self.mic.feed(m["mic"], now, s["listening_sensitivity"])     # the room, live
            if isinstance(m.get("out"), (int, float)):
                self._later_out.append((now + s["voice_delay_s"], float(m["out"])))
            if isinstance(m.get("bass"), (int, float)):
                bands = {k: max(0.0, min(1.0, float(m[k]))) if isinstance(m.get(k), (int, float)) else 0.0
                         for k in self._music}
                self._later_music.append((now + s["music_delay_s"], bands))
        elif kind == "pattern":
            # The ANVE Box cloud asked the box to blink ("find my box", light.show): the box
            # link (anve_device/boxlink.py) plays it on the Pi's LED and sends it here too.
            # steps: [[brightness 0..1, milliseconds], ...], repeated for `seconds`.
            steps = [(max(0.0, min(1.0, float(s[0]))), max(10, int(s[1]))) for s in (m.get("steps") or [])
                     if isinstance(s, (list, tuple)) and len(s) == 2 and all(isinstance(x, (int, float)) for x in s)][:32]
            seconds = m.get("seconds")
            if steps and isinstance(seconds, (int, float)) and seconds > 0:
                now = self.clock()
                self._pattern = {"name": str(m.get("name", ""))[:24], "steps": steps, "smooth": m.get("smooth") is True,
                                 "t0": now, "until": now + min(600.0, float(seconds))}
            else:
                self._pattern = None
        elif kind == "event":
            meta = m.get("meta") if isinstance(m.get("meta"), dict) else {}
            self.state.emit(str(m.get("name", "")), source=source, **{k: v for k, v in meta.items() if k in ("percent", "code", "message")})
        elif kind == "state":
            self.state.set_state(str(m.get("state", "")), source=source)
        elif kind == "status":
            self.state.snapshot(m.get("status") if isinstance(m.get("status"), dict) else {}, source=source)

    def _release(self, now: float):
        """The levels of what the speaker plays whose moment has come (see voice_delay_s)."""
        s = self.settings.values
        while self._later_out and self._later_out[0][0] <= now:
            self.out.feed(self._later_out.popleft()[1], now, s["speaking_sensitivity"])
        while self._later_music and self._later_music[0][0] <= now:
            self._music = self._later_music.popleft()[1]
            self._music_at = now

    def apply_settings(self, patch: dict, trusted: bool = False) -> list:
        took = self.settings.update(patch, trusted=trusted)
        if took:
            s = self.settings.values
            self.comp.configure(s, self.settings.colours())
            self.state.muted_overlay = s["muted_overlay"]
            self._wire = None
        return took

    def _check_marker(self, now: float):
        """Has the assistant been set up? Asked of the file that says so, every 2 s."""
        path = self.settings.values["setup_marker"]
        if not path or now - self._marker_at < 2.0:
            return
        self._marker_at = now
        setup = self.state.dims["setup"]
        if os.path.exists(path):
            if setup in ("unknown", "required"):
                self.state.emit("setup.completed", source="marker")
        elif setup in ("unknown", "complete"):
            self.state.emit("setup.required", source="marker")

    # ---------- one frame ----------

    def step(self) -> bool:
        """Take in what arrived, decide, draw, show. True when the strip was written."""
        now = self.clock()
        for m in self.bus.drain():
            self.handle(m)
        self._release(self.clock())
        self._check_marker(now)
        self.state.tick()
        view = self.state.view()
        if now - self._started < 1.2 and view["visual"] in ("CONNECTING", "READY", "SETUP_REQUIRED"):
            view = dict(view, visual="STARTING")            # the start-up greeting, once
        lv = self.comp.levels
        lv.mic, lv.mic_real = self.mic.read(now)
        lv.out, lv.out_real = self.out.read(now)
        lv.music_real = now - self._music_at < MUSIC_FRESH_S
        lv.bass, lv.mid, lv.treble, lv.beat = (self._music[k] if lv.music_real else 0.0 for k in ("bass", "mid", "treble", "beat"))
        self.loopback.target = self.settings.values["music_target"]
        self.loopback.want(self.settings.values["music_audio"] == "loopback" and view["dims"]["media"] == "playing"
                           and not self._stopping)
        view["pattern"] = self._pattern_level(now, view["visual"])
        frame = self.comp.render(view, now)
        wire, self.preview = self.comp.output(frame)
        self.frames += 1
        wrote = False
        if wire != self._wire:                  # an unchanged picture is not sent again
            try:
                self.strip.show(wire)
                self._wire, self._errors, wrote = wire, 0, True
                self.shown += 1
            except Exception as e:              # noqa: BLE001
                self._errors += 1
                if self._errors >= 3 and not isinstance(self.strip, Simulator):
                    self.hardware_problem = f"LED strip stopped answering ({type(e).__name__}: {e})"
                    log.error("%s: carrying on without it", self.hardware_problem)
                    try:
                        self.strip.close()
                    except Exception:           # noqa: BLE001
                        pass
                    self.strip = Simulator(self.settings.values["strip"]["count"], self.settings.values["strip"]["order"])
        if self.dashboard:
            self.dashboard.broadcast(self.preview, now)
        for x in self.extras:
            if hasattr(x, "announce"):
                x.announce(view)
        return wrote

    def _pattern_level(self, now: float, visual: str):
        """How bright the commanded blink is right now (0..1), or None when there is none.
        It never plays over an error or a shutdown: a commanded light must not hide a problem."""
        p = self._pattern
        if p is None:
            return None
        if now >= p["until"]:
            self._pattern = None
            return None
        if visual in ("ERROR", "SHUTTING_DOWN", "OFF"):
            return None
        cycle = sum(ms for _, ms in p["steps"])
        at = ((now - p["t0"]) * 1000.0) % cycle
        for i, (level, ms) in enumerate(p["steps"]):
            if at < ms:
                if p["smooth"]:
                    nxt = p["steps"][(i + 1) % len(p["steps"])][0]
                    return level + (nxt - level) * (at / ms)
                return level
            at -= ms
        return p["steps"][-1][0]

    def period(self) -> float:
        fps = self.settings.values["fps"]
        if self._pattern is not None:
            return 1.0 / fps                       # a blink has steps of 60 ms: draw every frame
        return 1.0 / (min(fps, 12) if self.comp.slow(self.state.visual()) else fps)

    def status(self) -> dict:
        s, view, lv = self.settings.values, self.state.view(), self.comp.levels
        return {
            "version": __version__, **view,
            "animation": self.comp.variant(view["visual"]) or (VARIANTS.get(view["visual"]) or ["default"])[0],
            "pattern": self._pattern["name"] if self._pattern else None,
            "strip": {"driver": getattr(self.strip, "name", "?"), "wanted": s["strip"]["driver"], "count": s["strip"]["count"],
                      "active": s["strip"]["active"], "problem": self.hardware_problem},
            "power_ma": round(self.comp.estimate_ma), "power_limit_ma": s["power_limit_ma"], "limited": self.comp.limited,
            "levels": {"mic": round(lv.mic, 3), "mic_real": lv.mic_real, "out": round(lv.out, 3), "out_real": lv.out_real,
                       "music_real": lv.music_real, "loopback": self.loopback.problem},
            "settings": {k: s[k] for k in ("brightness", "active_brightness", "music_brightness", "max_brightness", "speed",
                                           "transition_s", "wave_intensity", "listening_sensitivity", "speaking_sensitivity",
                                           "voice_delay_s", "music_delay_s", "music_mode", "music_audio",
                                           "direction", "theme", "colours", "animations", "muted_overlay", "blend_media")},
            "themes": {k: v.get("name", k) for k, v in themes().items()},
            "theme_colours": {k: v for k, v in themes().get(s["theme"], {}).items() if isinstance(v, str) and v.startswith("#")},
            "variants": VARIANTS,
            "counts": dict(self.state.counts, frames=self.frames, shown=self.shown, dropped=self.bus.dropped),
            "log": list(self.state.log)[-40:], "history": list(self.state.history)[-30:],
            "extras": {type(x).__name__: getattr(x, "problem", "") for x in self.extras},
        }

    # ---------- running ----------

    async def run(self, preview: bool = False, serve: bool = True):
        from .integrations import assistant_adapter
        from .integrations.websocket import Dashboard
        s = self.settings.values
        loop = asyncio.get_running_loop()
        wake = asyncio.Event()
        self.bus.on_publish(lambda m: m.get("type") != "level" and loop.call_soon_threadsafe(wake.set))
        transport = None
        try:
            transport, _ = await assistant_adapter.listen(self.bus.publish, s["events"]["host"], s["events"]["port"])
        except OSError as e:
            log.error("cannot listen for the assistant on %s:%s (%s)", s["events"]["host"], s["events"]["port"], e)
        if serve and s["dashboard"]["enabled"]:
            try:
                self.dashboard = await Dashboard(self, dashboard_key(self.settings.path.parent), s["dashboard"]["host"],
                                                 s["dashboard"]["port"]).start()
            except OSError as e:
                log.error("dashboard not started (%s)", e)
        self._start_extras()
        stop = asyncio.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except (NotImplementedError, RuntimeError, ValueError):     # Windows, or not the main thread
                try:
                    signal.signal(sig, lambda *a: loop.call_soon_threadsafe(stop.set))
                except ValueError:
                    pass
        log.info("LUMINA %s: %s LEDs on %s, brightness %.0f %% at rest, %.0f %% in a conversation, %.0f %% with music "
                 "(limit %.0f %%, %d mA), music sound %s, dashboard %s", __version__, s["strip"]["count"],
                 getattr(self.strip, "name", "?"), s["brightness"] * 100, s["active_brightness"] * 100,
                 s["music_brightness"] * 100, s["max_brightness"] * 100, s["power_limit_ma"], s["music_audio"],
                 f"port {s['dashboard']['port']}" if self.dashboard else "off")
        self.state.listeners.append(lambda r: log.info("%s -> %s (%s)%s", r["previous"], r["new"], r["cause"], " demo" if r["demo"] else ""))
        try:
            while not stop.is_set():
                self.step()
                if preview:
                    sys.stdout.write("\r" + ansi(_preview_rgb(self.preview)) + f" {self.state.visual():<20}")
                    sys.stdout.flush()
                wake.clear()
                try:
                    await asyncio.wait_for(wake.wait(), self.period())
                except asyncio.TimeoutError:
                    pass
            # going down: fade out, switch everything off, let go of the SPI bus
            self._stopping = True
            self.state.emit("assistant.shutdown", source="lumina")
            end = self.clock() + 1.35
            while self.clock() < end:
                self.step()
                await asyncio.sleep(1 / 30)
        finally:
            for x in self.extras:
                x.stop()
            self.loopback.close()
            if self.dashboard:
                await self.dashboard.stop()
            if transport:
                transport.close()
            try:
                self.strip.close()
            except Exception:                    # noqa: BLE001
                log.exception("closing the strip failed")
            log.info("LUMINA stopped: the strip is off")

    def _start_extras(self):
        s = self.settings.values
        if s["mqtt"]["enabled"]:
            from .integrations.mqtt import MqttBridge
            b = MqttBridge(s, self.bus.publish)
            if b.start():
                self.extras.append(b)
            else:
                log.error("MQTT not started: %s", b.problem)
        if s["home_assistant"]["enabled"]:
            from .integrations.home_assistant import HomeAssistant
            h = HomeAssistant(s, self.bus.publish)
            if h.start():
                self.extras.append(h)
            else:
                log.error("Home Assistant not started: %s", h.problem)


def _preview_rgb(px: str) -> list:
    return [(int(px[i:i + 2], 16), int(px[i + 2:i + 4], 16), int(px[i + 4:i + 6], 16)) for i in range(0, len(px), 6)]


def _set(settings: Settings, pair: str) -> bool:
    """--set a.b=value: a stored setting, changed by someone at the machine itself."""
    key, _, raw = pair.partition("=")
    try:
        val = json.loads(raw)
    except ValueError:
        val = raw
    parts = key.split(".")
    patch = val
    for p in reversed(parts[1:]):
        patch = {p: patch}
    return bool(settings.update({parts[0]: patch}, trusted=True))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="lumina", description="The light strip of the voice assistant.")
    ap.add_argument("--settings", help="settings file (default ~/.config/lumina/settings.json)")
    ap.add_argument("--simulate", action="store_true", help="never touch the real strip this run")
    ap.add_argument("--preview", action="store_true", help="draw the strip in this terminal")
    ap.add_argument("--no-dashboard", action="store_true")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="store a setting and leave")
    ap.add_argument("--key", action="store_true", help="print the dashboard key and leave")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format="%(levelname)s %(message)s")
    settings = Settings(Path(a.settings) if a.settings else None)
    if a.key:
        print(dashboard_key(settings.path.parent))
        return 0
    if a.set:
        ok = all(_set(settings, pair) for pair in a.set)
        print("stored in", settings.path) if ok else print("not a setting:", a.set)
        return 0 if ok else 2
    try:
        if os.nice(0) < 10:                      # the lights never compete with sound
            os.nice(10 - os.nice(0))
    except (AttributeError, OSError):
        pass
    strip = Simulator(settings.values["strip"]["count"], settings.values["strip"]["order"]) if a.simulate else None
    asyncio.run(Controller(settings, strip=strip).run(preview=a.preview, serve=not a.no_dashboard))
    return 0
