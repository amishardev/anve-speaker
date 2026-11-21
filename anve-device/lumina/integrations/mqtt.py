"""MQTT, for home-automation setups (off unless `mqtt.enabled`; needs the `paho-mqtt`
package). NOT yet run against a real broker: the message handling below is tested with a
stand-in client only.

    <prefix>/event    send: an event name, or {"name": "...", "meta": {...}}
    <prefix>/state    send: a state name ("listening", "ready", ...)
    <prefix>/status   read: {"visual": "...", "dims": {...}} whenever the state changes (retained)
"""
from __future__ import annotations

import json


def to_message(prefix: str, topic: str, payload: bytes) -> dict | None:
    """What an incoming MQTT message means to LUMINA, or None."""
    try:
        text = payload.decode("utf-8").strip()
    except UnicodeDecodeError:
        return None
    if not text or len(text) > 2048:
        return None
    if topic == f"{prefix}/state":
        return {"type": "state", "state": text, "source": "mqtt", "meta": {}}
    if topic == f"{prefix}/event":
        if text.startswith("{"):
            try:
                d = json.loads(text)
            except ValueError:
                return None
            if not isinstance(d, dict) or not isinstance(d.get("name"), str):
                return None
            return {"type": "event", "name": d["name"], "source": "mqtt",
                    "meta": d.get("meta") if isinstance(d.get("meta"), dict) else {}}
        return {"type": "event", "name": text, "source": "mqtt", "meta": {}}
    return None


class MqttBridge:
    def __init__(self, settings: dict, publish, client=None):
        self.cfg, self.publish = settings["mqtt"], publish
        self.prefix = self.cfg["prefix"].strip("/") or "lumina"
        self.client, self.problem = client, ""
        self._last = None

    def start(self) -> bool:
        if self.client is None:
            try:
                import paho.mqtt.client as mqtt
                try:
                    self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)       # paho 2
                except AttributeError:
                    self.client = mqtt.Client()                                       # paho 1
            except ImportError:
                self.problem = "paho-mqtt is not installed"
                return False
        c = self.client
        c.on_connect = lambda client, *a: client.subscribe([(f"{self.prefix}/event", 0), (f"{self.prefix}/state", 0)])
        c.on_message = lambda client, userdata, msg: self._incoming(msg.topic, msg.payload)
        try:
            if self.cfg.get("username"):
                c.username_pw_set(self.cfg["username"], self.cfg.get("password") or None)
            c.connect_async(self.cfg["host"], int(self.cfg["port"]))
            c.loop_start()
        except Exception as e:                   # noqa: BLE001
            self.problem = f"{type(e).__name__}: {e}"
            return False
        return True

    def _incoming(self, topic: str, payload: bytes):
        m = to_message(self.prefix, topic, payload)
        if m:
            self.publish(m)

    def announce(self, view: dict):
        """Publish the state when it changed."""
        key = (view["visual"], tuple(sorted(view["dims"].items())))
        if self.client is None or key == self._last:
            return
        self._last = key
        try:
            self.client.publish(f"{self.prefix}/status", json.dumps({"visual": view["visual"], "dims": view["dims"],
                                                                      "demo": view["demo"]}), retain=True)
        except Exception:                        # noqa: BLE001
            pass

    def stop(self):
        try:
            if self.client is not None:
                self.client.loop_stop()
                self.client.disconnect()
        except Exception:                        # noqa: BLE001
            pass
