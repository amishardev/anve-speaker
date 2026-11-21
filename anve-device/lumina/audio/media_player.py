"""A media player's own words for its state, as LUMINA events.

Players (and Home Assistant's media_player entities) say things like "playing", "paused",
"idle", "buffering". This turns a change of that word into the right event, once, so that
music is shown because a player says it plays, never because a microphone hears sound.
"""
from __future__ import annotations

_WORDS = {
    "playing": "playing", "buffering": "playing", "on": "playing",
    "paused": "paused",
    "idle": "stopped", "stopped": "stopped", "off": "stopped", "standby": "stopped",
    "unavailable": "stopped", "unknown": "stopped", "ended": "stopped", "finished": "stopped",
}


class MediaPlayer:
    def __init__(self):
        self.state = "stopped"

    def update(self, word: str) -> str | None:
        """The event to publish for the player's new state, or None when nothing changed."""
        new = _WORDS.get(str(word).strip().lower())
        if new is None or new == self.state:
            return None
        old, self.state = self.state, new
        if new == "playing":
            return "media.resumed" if old == "paused" else "media.started"
        return "media.paused" if new == "paused" else "media.stopped"
