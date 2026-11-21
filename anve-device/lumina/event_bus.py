"""A small thread-safe message bus: adapters publish, the controller listens.

Everything that reaches LUMINA from outside (the assistant's UDP messages, the dashboard,
MQTT, Home Assistant) becomes one of these messages:

    {"type": "event",  "name": "wake_word.detected", "meta": {...}, "source": "assistant"}
    {"type": "state",  "state": "listening",                         "source": "api"}
    {"type": "status", "status": {"voice": "idle", ...},             "source": "assistant"}
    {"type": "level",  "mic": 0.2, "out": 0.0}

Publishing never blocks and never raises. When the queue is full, a level message is
dropped before an event is (a level is only good for a moment).
"""
from __future__ import annotations

import threading
from collections import deque


class EventBus:
    def __init__(self, limit: int = 512):
        self._q: deque = deque()
        self._lock = threading.Lock()
        self._limit = limit
        self.dropped = 0
        self._wake = None                      # set by the controller: wakes its loop

    def on_publish(self, wake):
        self._wake = wake

    def publish(self, message: dict) -> bool:
        if not isinstance(message, dict) or "type" not in message:
            return False
        with self._lock:
            if len(self._q) >= self._limit:
                self.dropped += 1
                for i, m in enumerate(self._q):
                    if m.get("type") == "level":
                        del self._q[i]
                        break
                else:
                    if message.get("type") == "level":
                        return False
                    self._q.popleft()
            self._q.append(message)
        if self._wake:
            try:
                self._wake(message)
            except Exception:                  # noqa: BLE001
                pass
        return True

    def drain(self) -> list:
        with self._lock:
            out = list(self._q)
            self._q.clear()
        return out
