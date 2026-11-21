"""Home Assistant, as a source of MUSIC state only (off unless `home_assistant.enabled`).
NOT yet run against a real Home Assistant: the mapping is tested, the connection is not.

What other makers' speakers can tell us, honestly:

* An Amazon Echo or a Google Nest speaker does NOT tell anyone outside when it is
  listening, thinking or speaking. There is no supported interface for that, in Home
  Assistant or anywhere else, and this code does not try to get it by scraping private
  interfaces or by listening to the room.
* What Home Assistant does offer for such a speaker (Google Cast; Alexa only through an
  unofficial add-on) is a `media_player` entity: playing, paused, idle, off. That is what
  is used here, for the music looks.
* Your own assistant, whose source is here, reports everything directly
  (`AssistantLEDs`); it does not need this file.

It asks Home Assistant's REST API for the entity's state every two seconds, with a
long-lived access token you create in your Home Assistant profile.
"""
from __future__ import annotations

import json
import threading
import urllib.request

from ..audio.media_player import MediaPlayer


def to_event(player: MediaPlayer, entity_state: dict) -> dict | None:
    """Home Assistant's JSON for the entity -> a LUMINA message, when the state changed."""
    if not isinstance(entity_state, dict):
        return None
    ev = player.update(str(entity_state.get("state", "")))
    return {"type": "event", "name": ev, "source": "home_assistant", "meta": {}} if ev else None


class HomeAssistant:
    def __init__(self, settings: dict, publish, fetch=None):
        self.cfg, self.publish = settings["home_assistant"], publish
        self.player, self.problem = MediaPlayer(), ""
        self._fetch = fetch or self._http
        self._stop = threading.Event()

    def _http(self) -> dict:
        url = self.cfg["url"].rstrip("/") + "/api/states/" + self.cfg["media_player"]
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + self.cfg["token"]})
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read(65536).decode("utf-8"))

    def poll_once(self) -> bool:
        try:
            m = to_event(self.player, self._fetch())
            self.problem = ""
        except Exception as e:                   # noqa: BLE001
            self.problem = f"{type(e).__name__}"
            return False
        if m:
            self.publish(m)
        return True

    def start(self) -> bool:
        if not (self.cfg.get("url", "").startswith(("http://", "https://")) and self.cfg.get("token") and self.cfg.get("media_player")):
            self.problem = "url, token and media_player are needed"
            return False
        threading.Thread(target=self._run, name="lumina-ha", daemon=True).start()
        return True

    def _run(self):
        while not self._stop.wait(2.0):
            self.poll_once()

    def stop(self):
        self._stop.set()
