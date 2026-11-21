"""What the assistant calls to tell LUMINA what it is doing.

    from lumina_client import AssistantLEDs
    leds = AssistantLEDs()
    leds.listening()          # ... and so on, see the methods below

Every call is one small UDP message to LUMINA on this machine. It never waits, never
raises and needs LUMINA neither running nor installed: if the lights are down, the
assistant does not notice. Nothing but the standard library is used.
"""
from __future__ import annotations

import json
import socket
import threading
import time

DEFAULT_PORT = 8741


class AssistantLEDs:
    def __init__(self, host: str = "127.0.0.1", port: int = DEFAULT_PORT, source: str = "assistant"):
        self._addr, self._source = (host, port), source
        self._sock = None
        self._last_level = 0.0
        self._follow = None                    # the reply's loudness, when a source for it was given
        self.sent = self.failed = 0
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setblocking(False)
        except OSError:
            self._sock = None

    # ---------- the plain interface ----------

    def set_state(self, state: str, **meta) -> bool:
        return self._send({"type": "state", "state": state, "meta": meta})

    def emit(self, event: str, **meta) -> bool:
        return self._send({"type": "event", "name": event, "meta": meta})

    def status(self, **dims) -> bool:
        """The whole state at once, e.g. status(connection="connected", voice="idle",
        media="playing", microphone="enabled"). Send it every few seconds: it tells
        LUMINA the assistant is alive, and puts right anything a lost message left wrong."""
        return self._send({"type": "status", "status": dims})

    def level(self, mic: float | None = None, out: float | None = None) -> bool:
        """Loudness 0..1 of the microphone (while listening) or of the reply being played
        (while speaking). At most about 25 a second are sent; the rest are dropped here."""
        now = time.monotonic()
        if now - self._last_level < 0.04:
            return False
        self._last_level = now
        m = {"type": "level"}
        if mic is not None:
            m["mic"] = round(float(mic), 3)
        if out is not None:
            m["out"] = round(float(out), 3)
        return self._send(m)

    # ---------- one method per moment ----------

    def setup_required(self): return self.emit("setup.required")
    def setup_started(self, percent: float | None = None):
        return self.emit("setup.started", **({} if percent is None else {"percent": percent}))
    def setup_progress(self, percent: float): return self.emit("setup.progress", percent=percent)
    def setup_completed(self): return self.emit("setup.completed")
    def connecting(self): return self.emit("assistant.connecting")
    def connected(self): return self.emit("assistant.connected")
    def disconnected(self): return self.emit("assistant.disconnected")
    def wake_word_detected(self): return self.emit("wake_word.detected")
    def listening(self): return self.emit("voice.listening.started")
    def listening_ended(self): return self.emit("voice.listening.ended")
    def processing(self): return self.emit("request.processing")
    def thinking(self): return self.emit("ai.thinking")
    def searching(self): return self.emit("ai.searching")
    def tool_executing(self): return self.emit("tool.executing")
    def response_generated(self): return self.emit("response.generated")
    def music_playing(self): return self.emit("media.started")
    def music_resumed(self): return self.emit("media.resumed")
    def music_paused(self): return self.emit("media.paused")
    def music_stopped(self): return self.emit("media.stopped")
    def muted(self): return self.emit("microphone.muted")
    def unmuted(self): return self.emit("microphone.unmuted")
    def notice(self): return self.emit("assistant.notice")
    def shutting_down(self): return self.emit("assistant.shutdown")

    def ready(self):
        """Set up, connected and waiting for the wake word. Also ends speaking."""
        self._stop_following()
        return self.emit("assistant.ready")

    def speaking(self, audio_stream=None):
        """The reply starts playing. `audio_stream`, if given, is where its loudness can be
        read while it plays: a function returning 0..1, or an object with a `level`
        attribute (0..1). Without it the lights move like speech by themselves."""
        ok = self.emit("tts.started")
        self._stop_following()
        if audio_stream is not None:
            read = audio_stream if callable(audio_stream) else (lambda: getattr(audio_stream, "level", 0.0))
            stop = threading.Event()
            self._follow = stop
            threading.Thread(target=self._follow_level, args=(read, stop), name="lumina-level", daemon=True).start()
        return ok

    def speaking_finished(self):
        self._stop_following()
        return self.emit("tts.finished")

    def error(self, message: str = "", code: str = ""):
        return self.emit("error", message=str(message)[:160], code=str(code)[:40])

    def error_cleared(self): return self.emit("error.cleared")

    def feed_pcm(self, pcm: bytes, kind: str = "out") -> bool:
        """Report loudness straight from 16-bit mono sound (kind "out" or "mic")."""
        from .audio.audio_analyser import loudness
        return self.level(**{kind: loudness(pcm)})

    def close(self):
        self._stop_following()
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    # ---------- inside ----------

    def _follow_level(self, read, stop):
        end = time.monotonic() + 300
        while not stop.is_set() and time.monotonic() < end:
            try:
                self.level(out=float(read()))
            except Exception:                    # noqa: BLE001  (the source went away)
                break
            stop.wait(0.05)

    def _stop_following(self):
        if self._follow is not None:
            self._follow.set()
            self._follow = None

    def _send(self, message: dict) -> bool:
        if self._sock is None:
            return False
        try:
            message["source"] = self._source
            self._sock.sendto(json.dumps(message, separators=(",", ":")).encode(), self._addr)
            self.sent += 1
            return True
        except (OSError, TypeError, ValueError):
            self.failed += 1
            return False
