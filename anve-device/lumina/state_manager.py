"""The assistant's state, as the assistant reports it, and what the strip should show.

The state is not one word. It is a handful of independent facts ("dimensions"):

    power        on | shutting_down
    fault        none | error | hardware
    setup        unknown | required | in_progress | complete
    connection   unknown | connecting | connected | disconnected
    microphone   enabled | muted
    voice        idle | listening | recording
    processing   none | processing | thinking | searching | tool
    speech       idle | speaking
    media        stopped | playing | paused
    alarm        off | ringing

Music can play while the assistant thinks; the microphone can be muted while it speaks.
Events change dimensions; `visual()` then picks the one thing worth showing, by priority.
Nothing here guesses: a dimension only changes because an event or a status snapshot from
the assistant said so, or because a state that needs refreshing was not refreshed in time
(a missed "finished" event must not leave the strip listening for ever).
"""
from __future__ import annotations

import threading
import time
from collections import deque

DIMS = {
    "power": ("on", "shutting_down"),
    "fault": ("none", "error", "hardware"),
    "setup": ("unknown", "required", "in_progress", "complete"),
    "connection": ("unknown", "connecting", "connected", "disconnected"),
    "microphone": ("enabled", "muted"),
    "voice": ("idle", "listening", "recording"),
    "processing": ("none", "processing", "thinking", "searching", "tool"),
    "speech": ("idle", "speaking"),
    "media": ("stopped", "playing", "paused"),
    "alarm": ("off", "ringing"),
}
START = {"power": "on", "fault": "none", "setup": "unknown", "connection": "unknown",
         "microphone": "enabled", "voice": "idle", "processing": "none", "speech": "idle",
         "media": "stopped", "alarm": "off"}

# event name -> the dimensions it sets
EVENTS = {
    "assistant.connecting": {"connection": "connecting", "power": "on"},
    "assistant.connected": {"connection": "connected", "power": "on"},
    "assistant.disconnected": {"connection": "disconnected", "voice": "idle", "processing": "none", "speech": "idle"},
    "assistant.ready": {"connection": "connected", "setup": "complete", "voice": "idle", "processing": "none",
                        "speech": "idle", "fault": "none", "alarm": "off", "power": "on"},
    "assistant.shutdown": {"power": "shutting_down"},
    "setup.required": {"setup": "required"},
    "setup.started": {"setup": "in_progress"},
    "setup.progress": {"setup": "in_progress"},
    "setup.completed": {"setup": "complete"},
    "wake_word.detected": {},                                   # a one-shot, see below
    "voice.listening.started": {"voice": "listening", "speech": "idle"},
    "voice.listening.ended": {"voice": "idle"},
    "voice.recording.started": {"voice": "recording"},
    "voice.recording.ended": {"voice": "idle"},
    "request.processing": {"voice": "idle", "processing": "processing"},
    "ai.thinking": {"voice": "idle", "processing": "thinking"},
    "ai.searching": {"voice": "idle", "processing": "searching"},
    "tool.executing": {"voice": "idle", "processing": "tool"},
    "response.generated": {"processing": "none"},
    "tts.started": {"speech": "speaking", "processing": "none", "voice": "idle"},
    "tts.finished": {"speech": "idle"},
    "media.started": {"media": "playing"},
    "media.resumed": {"media": "playing"},
    "media.paused": {"media": "paused"},
    "media.stopped": {"media": "stopped"},
    "media.finished": {"media": "stopped"},
    "microphone.muted": {"microphone": "muted", "voice": "idle"},
    "microphone.unmuted": {"microphone": "enabled"},
    "error": {"fault": "error"},
    "error.cleared": {"fault": "none"},
    "hardware.error": {"fault": "hardware"},
    "alarm.started": {"alarm": "ringing"},
    "alarm.stopped": {"alarm": "off"},
    "assistant.notice": {},                                     # a one-shot amber blink
}

# set_state("listening") and friends: a plain name -> the event that means it
STATES = {
    "setup_required": "setup.required", "setup_in_progress": "setup.started", "setup_complete": "setup.completed",
    "connecting": "assistant.connecting", "connected": "assistant.connected", "ready": "assistant.ready",
    "idle": "assistant.ready", "wake_word_detected": "wake_word.detected", "wake_word": "wake_word.detected",
    "listening": "voice.listening.started", "recording": "voice.recording.started",
    "processing": "request.processing", "thinking": "ai.thinking", "searching": "ai.searching",
    "tool_executing": "tool.executing", "speaking": "tts.started", "music_playing": "media.started",
    "music_paused": "media.paused", "paused": "media.paused", "music_stopped": "media.stopped",
    "stopped": "media.stopped", "muted": "microphone.muted", "unmuted": "microphone.unmuted",
    "disconnected": "assistant.disconnected", "error": "error", "shutting_down": "assistant.shutdown",
}

VISUALS = ("STARTING", "SETUP_REQUIRED", "SETUP_IN_PROGRESS", "CONNECTING", "READY", "WAKE_WORD_DETECTED",
           "LISTENING", "RECORDING", "PROCESSING", "THINKING", "SEARCHING", "TOOL_EXECUTING", "SPEAKING",
           "MUSIC_PLAYING", "MUSIC_PAUSED", "MUTED", "DISCONNECTED", "ERROR", "ALARM", "SHUTTING_DOWN", "OFF")

# a state that nobody refreshes for this long is dropped (seconds); the assistant's status
# snapshots refresh everything it still stands by, so real long states are never cut
TIMEOUTS = {("voice", "listening"): 45, ("voice", "recording"): 4 * 3600, ("processing", "processing"): 60,
            ("processing", "thinking"): 120, ("processing", "searching"): 120, ("processing", "tool"): 300,
            ("speech", "speaking"): 180, ("setup", "in_progress"): 600, ("alarm", "ringing"): 600,
            ("connection", "connecting"): 180}
FALLBACK = {"voice": "idle", "processing": "none", "speech": "idle", "setup": "unknown", "alarm": "off",
            "connection": "disconnected"}

_PROCESSING = {"processing": "PROCESSING", "thinking": "THINKING", "searching": "SEARCHING", "tool": "TOOL_EXECUTING"}
# real things that end a demonstration at once: the truth must never be hidden behind it
_ENDS_DEMO = ("wake_word.detected", "voice.listening.started", "microphone.muted", "error", "hardware.error",
              "assistant.shutdown", "assistant.disconnected", "alarm.started", "setup.required")


class StateManager:
    def __init__(self, *, clock=time.monotonic, wall=time.time, assistant_timeout: float = 8.0,
                 startup_grace: float = 90.0, wake_s: float = 0.55, wake_hold_s: float = 3.0,
                 muted_overlay: bool = True, timeouts: dict | None = None):
        self._clock, self._wall = clock, wall
        self._lock = threading.RLock()
        self.dims = dict(START)
        self._fresh = {d: clock() for d in DIMS}       # when each dimension was last confirmed
        self.meta: dict = {}                           # progress, error code/message
        self.assistant_timeout, self.startup_grace = assistant_timeout, startup_grace
        # The wake pulse lasts wake_s. After it, the wake look stays until listening begins,
        # at most wake_hold_s: the speaker's server answers a wake in a few tenths of a
        # second, and "ready" in that gap would show as a flash of the resting look.
        self.wake_s, self.wake_hold_s, self.muted_overlay = wake_s, wake_hold_s, muted_overlay
        self.timeouts = dict(TIMEOUTS, **(timeouts or {}))
        self.started = clock()
        self.heartbeat_at: float | None = None         # the assistant's last status snapshot
        self.ever_alive = False
        self.wake_until = self.wake_hold_until = self.notice_until = 0.0
        self.demo_state, self.demo_until = None, 0.0
        self.history: deque = deque(maxlen=200)        # visual transitions
        self.log: deque = deque(maxlen=200)            # what came in, and what was refused
        self.counts = {"events": 0, "duplicates": 0, "invalid": 0, "timeouts": 0, "snapshots": 0}
        self._visual = "STARTING"
        self._since = clock()
        self.listeners: list = []                      # called with each transition record

    # ---------- what comes in ----------

    def set_state(self, name: str, source: str = "api", **meta) -> bool:
        ev = STATES.get(str(name).strip().lower().replace(" ", "_").replace("-", "_"))
        if not ev:
            return self._refuse(f"state {name!r}", "unknown state", source)
        return self.emit(ev, source=source, **meta)

    def emit(self, event: str, source: str = "api", **meta) -> bool:
        """One event from the assistant. False when it was refused (and logged)."""
        with self._lock:
            now = self._clock()
            changes = EVENTS.get(event)
            if changes is None:
                return self._refuse(event, "unknown event", source)
            if event in ("voice.listening.started", "voice.recording.started", "wake_word.detected") \
                    and self.dims["microphone"] == "muted":
                return self._refuse(event, "the microphone is muted", source)
            if self.dims["power"] == "shutting_down" and "power" not in changes:
                return self._refuse(event, "shutting down", source)
            self.counts["events"] += 1
            changed = self._apply(changes, now)
            # something real and new that must be seen ends a demonstration; a standing
            # condition that is only repeated (an error that was already there) does not
            if self.demo_state and event in _ENDS_DEMO and (changed or event == "wake_word.detected"):
                self.demo_state = None
            if event == "wake_word.detected":
                self.wake_until, changed = now + self.wake_s, True
                idle = self.dims["voice"] == "idle" and self.dims["processing"] == "none"
                self.wake_hold_until = now + max(self.wake_s, self.wake_hold_s) if idle else 0.0
            elif event == "assistant.notice":
                self.notice_until, changed = now + 0.7, True
            elif event == "setup.progress" or (event == "setup.started" and "percent" in meta):
                p = meta.get("percent")
                self.meta["progress"] = max(0.0, min(1.0, float(p) / 100.0)) if isinstance(p, (int, float)) else None
            elif event == "setup.started":
                self.meta["progress"] = None           # no percentage known: none is shown
            elif event in ("error", "hardware.error"):
                self.meta["error"] = {"code": str(meta.get("code", ""))[:40], "message": str(meta.get("message", ""))[:160]}
            elif event in ("error.cleared", "assistant.ready"):
                self.meta.pop("error", None)
            if not changed:
                self.counts["duplicates"] += 1
            self._note(event, source, "duplicate" if not changed else "")
            self._update(now, cause=event)
            return True

    def snapshot(self, status: dict, source: str = "assistant") -> bool:
        """The assistant's whole state at once. It is the authority: it overrides what
        single events built up, and it refreshes every state it still stands by."""
        with self._lock:
            now = self._clock()
            clean = {}
            for dim, value in (status or {}).items():
                if dim in DIMS and value in DIMS[dim]:
                    clean[dim] = value
                elif dim not in ("progress", "error"):
                    self._refuse(f"status {dim}={value!r}", "unknown value", source)
            if not clean:
                return False
            self.counts["snapshots"] += 1
            clean.setdefault("power", "on")        # whoever reports is running
            self.heartbeat_at, self.ever_alive = now, True
            if clean.get("microphone", self.dims["microphone"]) == "muted" and clean.get("voice") in ("listening", "recording"):
                clean["voice"] = "idle"
                self._refuse("status voice", "the microphone is muted", source)
            d = self.dims
            if self.demo_state and ((clean.get("fault", "none") != "none" and d["fault"] == "none")
                                    or (clean.get("microphone") == "muted" and d["microphone"] != "muted")
                                    or (clean.get("voice") == "listening" and d["voice"] != "listening")
                                    or (clean.get("connection") == "disconnected" and d["connection"] != "disconnected")):
                self.demo_state = None             # news that must be seen ends a demonstration
            if "error" in status and isinstance(status["error"], dict):
                self.meta["error"] = {"code": str(status["error"].get("code", ""))[:40],
                                      "message": str(status["error"].get("message", ""))[:160]}
            elif clean.get("fault") == "none":
                self.meta.pop("error", None)
            if self._apply(clean, now):
                self._note("status", source, "")
            self._update(now, cause="status")
            return True

    def demo(self, visual: str | None, seconds: float = 20.0) -> bool:
        """Show a state for testing. The real state is untouched and comes back by itself."""
        with self._lock:
            now = self._clock()
            if visual is None:
                self.demo_state = None
            else:
                v = str(visual).upper()
                if v not in VISUALS:
                    return self._refuse(f"demo {visual!r}", "unknown state", "dashboard")
                self.demo_state, self.demo_until = v, now + max(1.0, min(120.0, float(seconds)))
                if v == "WAKE_WORD_DETECTED":
                    self.demo_until = now + self.wake_s
            self._update(now, cause="demo")
            return True

    def tick(self):
        """Call regularly: drops states that were not refreshed, ends one-shots and demos."""
        with self._lock:
            now = self._clock()
            for (dim, value), limit in self.timeouts.items():
                if self.dims[dim] == value and now - self._fresh[dim] > limit:
                    self.dims[dim] = FALLBACK[dim]
                    self._fresh[dim] = now
                    self.counts["timeouts"] += 1
                    self._note(f"{dim}={value}", "timeout", f"not refreshed for {limit} s")
            if self.demo_state and now >= self.demo_until:
                self.demo_state = None
            self._update(now, cause="tick")

    # ---------- what goes out ----------

    @property
    def alive(self) -> bool:
        return self.heartbeat_at is not None and self._clock() - self.heartbeat_at <= self.assistant_timeout

    def real_visual(self) -> str:
        """What the assistant's real state calls for, by priority."""
        d, now = self.dims, self._clock()
        if d["power"] == "shutting_down":
            return "SHUTTING_DOWN"
        if d["fault"] != "none":
            return "ERROR"
        if d["alarm"] == "ringing":
            return "ALARM"
        muted = d["microphone"] == "muted"
        busy = _PROCESSING.get(d["processing"]) or ("SPEAKING" if d["speech"] == "speaking" else None)
        if muted and not (self.muted_overlay and busy):
            return "MUTED"                 # with the overlay, a reply still shows, between red ends
        if d["setup"] == "required":
            return "SETUP_REQUIRED"
        if d["setup"] == "in_progress":
            return "SETUP_IN_PROGRESS"
        if self.heartbeat_at is None and d["connection"] == "unknown":
            # nothing has reported yet: starting up, and after a while plainly not there
            return "CONNECTING" if now - self.started < self.startup_grace else "DISCONNECTED"
        if self.ever_alive and not self.alive:
            return "DISCONNECTED"          # the assistant stopped reporting
        if d["connection"] == "disconnected":
            return "DISCONNECTED"
        if d["connection"] in ("connecting", "unknown"):
            return "CONNECTING"
        if not muted:
            if now < self.wake_until:
                return "WAKE_WORD_DETECTED"
            if now < self.wake_hold_until and d["voice"] == "idle" and d["processing"] == "none":
                return "WAKE_WORD_DETECTED"            # heard; the listening has not begun yet
            if d["voice"] == "listening":
                return "LISTENING"
            if d["voice"] == "recording":
                return "RECORDING"
        if busy:
            return busy
        if muted:
            return "MUTED"
        if d["media"] == "playing":
            return "MUSIC_PLAYING"
        if d["media"] == "paused":
            return "MUSIC_PAUSED"
        return "READY"

    def visual(self) -> str:
        with self._lock:
            return self._visual

    def view(self) -> dict:
        """Everything a renderer or the dashboard needs, in one consistent piece."""
        with self._lock:
            now = self._clock()
            d = self.dims
            return {
                "visual": self._visual, "real": self.real_visual(), "since": now - self._since,
                "demo": self.demo_state is not None, "demo_left": max(0.0, self.demo_until - now) if self.demo_state else 0.0,
                "dims": dict(d), "alive": self.alive, "progress": self.meta.get("progress"),
                "error": self.meta.get("error"),
                "privacy": d["microphone"] == "muted" and self._visual not in ("MUTED", "ERROR", "SHUTTING_DOWN", "OFF"),
                "media_under": d["media"] == "playing" and self._visual in ("PROCESSING", "THINKING", "SEARCHING", "TOOL_EXECUTING"),
                "notice": now < self.notice_until,
            }

    # ---------- inside ----------

    def _apply(self, changes: dict, now: float) -> bool:
        changed = False
        for dim, value in changes.items():
            self._fresh[dim] = now
            if self.dims[dim] != value:
                self.dims[dim], changed = value, True
        if self.wake_hold_until and (self.dims["voice"] != "idle" or self.dims["processing"] != "none"):
            self.wake_hold_until = 0.0          # the request has begun: the wake look holds no longer
        return changed

    def _update(self, now: float, cause: str):
        new = self.demo_state or self.real_visual()
        if new == self._visual:
            return
        rec = {"t": round(now, 3), "time": self._wall(), "previous": self._visual, "new": new, "cause": cause,
               "demo": self.demo_state is not None, "meta": {k: v for k, v in self.meta.items() if v is not None}}
        self._visual, self._since = new, now
        self.history.append(rec)
        for f in list(self.listeners):
            try:
                f(rec)
            except Exception:                                # noqa: BLE001
                pass

    def _note(self, what: str, source: str, remark: str):
        self.log.append({"time": self._wall(), "what": what, "source": source, "remark": remark})

    def _refuse(self, what: str, why: str, source: str) -> bool:
        with self._lock:
            self.counts["invalid"] += 1
            self._note(what, source, f"refused: {why}")
        return False
