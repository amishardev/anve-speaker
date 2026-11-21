"""Settings: the defaults, the person's own changes, and the themes.

Only what was changed is stored, in ~/.config/lumina/settings.json (or $LUMINA_HOME), away
from the program's own folder, so that updating the program never changes a setting. A
missing or broken file means the defaults, and the default LED output is the simulator:
the real strip lights only after someone chose `strip.driver = "spi"`.
"""
from __future__ import annotations

import copy
import json
import os
import re
import secrets
import tempfile
from pathlib import Path

from .effects.gradients import rgb

HERE = Path(__file__).resolve().parent

DEFAULTS = {
    "strip": {
        "count": 60,            # LEDs on the strip
        "active": 60,           # how many of them are used
        "offset": 0,            # the first one used
        "order": "GRB",
        "driver": "simulator",  # "simulator" | "spi"
        "spi_bus": 0, "spi_device": 0,
    },
    "brightness": 0.20,         # what is shown, 0..max_brightness (at rest: ready, muted, errors ...)
    "max_brightness": 0.20,     # the limit no brightness can ever pass
    # after the wake word: listening, thinking, speaking (and an alarm); and while music
    # plays. Both stay under max_brightness, so with the default limit nothing changes.
    "active_brightness": 1.0,
    "music_brightness": 1.0,
    # which looks get active_brightness: "conversation" (wake word to the end of the reply)
    # or "listening" (only while the box listens; thinking and speaking stay at rest brightness)
    "bright_when": "conversation",
    "power_limit_ma": 1500,     # frames that would draw more are dimmed as a whole
    # the lights for the assistant's voice and for music wait this long, so that they move
    # with the sound in the room and not before it (a Bluetooth speaker plays late)
    "voice_delay_s": 0.0,
    "music_delay_s": 0.0,
    "gamma": 1.8,
    "fps": 30,
    "speed": 1.0,               # all animations faster or slower
    "transition_s": 0.35,
    "wave_intensity": 1.0,
    "listening_sensitivity": 1.0,
    "speaking_sensitivity": 1.0,
    "music_mode": "smooth",     # smooth | festival | ambient
    "music_audio": "off",       # off | loopback (listen to what the speaker plays)
    "music_target": "",         # the output whose sound loopback records ("" = the default one)
    "direction": "forward",     # forward | reverse
    "muted_overlay": True,      # muted + speaking: the reply shows between red ends
    "blend_media": True,        # music keeps glowing faintly under "thinking"
    "theme": "futuristic-cyan",
    "colours": {},              # role -> "#rrggbb", on top of the theme
    "animations": {},           # state -> variant, e.g. {"READY": "breathe"}
    "events": {"host": "127.0.0.1", "port": 8741},
    "dashboard": {"enabled": True, "host": "0.0.0.0", "port": 8742},
    "setup_marker": "",         # a file that exists once the assistant has been set up
    "assistant_timeout_s": 8,
    "startup_grace_s": 90,
    "mqtt": {"enabled": False, "host": "127.0.0.1", "port": 1883, "prefix": "lumina", "username": "", "password": ""},
    "home_assistant": {"enabled": False, "url": "", "token": "", "media_player": ""},
}

MUSIC_MODES = ("smooth", "festival", "ambient", "rainbow")
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def home() -> Path:
    return Path(os.environ.get("LUMINA_HOME") or Path.home() / ".config" / "lumina")


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k not in ("colours", "animations"):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def themes() -> dict:
    data = json.loads((HERE / "data" / "themes.json").read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if isinstance(v, dict)}


class Settings:
    """`values` is defaults + the stored changes; `changed` is what gets written."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else home() / "settings.json"
        self.changed: dict = {}
        self.problem = ""
        try:
            if self.path.exists():
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("not an object")
                self.changed = data
        except (OSError, ValueError) as e:
            self.problem = f"settings unreadable ({e}): using the defaults"
            self.changed = {}
        self.values = self._clamp(_merge(DEFAULTS, self.changed))

    @staticmethod
    def _clamp(v: dict) -> dict:
        s = v["strip"]
        s["count"] = max(1, min(600, int(s["count"])))
        s["active"] = max(1, min(s["count"], int(s["active"])))
        s["offset"] = max(0, min(s["count"] - s["active"], int(s["offset"])))
        if s["driver"] not in ("simulator", "spi"):
            s["driver"] = "simulator"
        if sorted(str(s["order"]).upper()) != ["B", "G", "R"]:
            s["order"] = "GRB"
        v["max_brightness"] = max(0.01, min(1.0, float(v["max_brightness"])))
        for k in ("brightness", "active_brightness", "music_brightness"):
            v[k] = max(0.0, min(v["max_brightness"], float(v[k])))
        v["power_limit_ma"] = max(100, min(20000, int(v["power_limit_ma"])))
        for k in ("voice_delay_s", "music_delay_s"):
            v[k] = max(0.0, min(1.5, float(v[k])))
        v["fps"] = max(5, min(60, int(v["fps"])))
        v["speed"] = max(0.2, min(3.0, float(v["speed"])))
        v["transition_s"] = max(0.0, min(3.0, float(v["transition_s"])))
        for k in ("wave_intensity", "listening_sensitivity", "speaking_sensitivity"):
            v[k] = max(0.1, min(3.0, float(v[k])))
        if v["music_mode"] not in MUSIC_MODES:
            v["music_mode"] = "smooth"
        if v["bright_when"] not in ("conversation", "listening"):
            v["bright_when"] = "conversation"
        if v["music_audio"] not in ("off", "loopback"):
            v["music_audio"] = "off"
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{0,80}", str(v["music_target"])):
            v["music_target"] = ""
        if v["direction"] not in ("forward", "reverse"):
            v["direction"] = "forward"
        v["colours"] = {k: c for k, c in (v.get("colours") or {}).items() if isinstance(c, str) and _HEX.match(c)}
        v["animations"] = {str(k).upper(): str(a) for k, a in (v.get("animations") or {}).items()}
        return v

    # what the dashboard may change; the LED output itself (strip.driver) is not in it
    EDITABLE = ("brightness", "active_brightness", "music_brightness", "bright_when", "speed", "transition_s", "wave_intensity",
                "listening_sensitivity", "speaking_sensitivity", "voice_delay_s", "music_delay_s",
                "music_mode", "direction", "theme", "colours", "animations", "muted_overlay", "blend_media")

    def update(self, patch: dict, *, trusted: bool = False) -> list:
        """Apply a change and save it. Returns the keys that were accepted."""
        took = []
        for k, val in (patch or {}).items():
            if k == "active" and isinstance(val, (int, float)):       # number of LEDs used
                self.changed.setdefault("strip", {})["active"] = int(val)
                took.append(k)
            elif k in self.EDITABLE or (trusted and k in DEFAULTS):
                if k == "theme" and val not in themes():
                    continue
                if k in ("colours", "animations"):
                    if not isinstance(val, dict):
                        continue
                    cur = dict(self.changed.get(k) or {})
                    for kk, vv in val.items():
                        if vv in (None, ""):
                            cur.pop(kk, None)
                        else:
                            cur[kk] = vv
                    val = cur
                elif isinstance(DEFAULTS[k], dict) and isinstance(val, dict):
                    val = _merge(self.changed.get(k) or {}, val)
                self.changed[k] = val
                took.append(k)
        try:
            self.values = self._clamp(_merge(DEFAULTS, self.changed))
        except (TypeError, ValueError):
            for k in took:
                self.changed.pop(k, None)
            self.values = self._clamp(_merge(DEFAULTS, self.changed))
            return []
        if took:
            self.save()
        return took

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=".settings-")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(self.changed, f, indent=2)
        os.replace(tmp, self.path)

    def colours(self) -> dict:
        """The theme's colours with the person's own on top, as (r, g, b) 0..1."""
        all_ = themes()
        theme = dict(all_["futuristic-cyan"], **all_.get(self.values["theme"], {}))
        out = {}
        for role, val in theme.items():
            if role == "name":
                continue
            out[role] = [rgb(c) for c in val] if isinstance(val, list) else rgb(val)
        for role, val in self.values["colours"].items():
            if role in out and not isinstance(out[role], list):
                out[role] = rgb(val)
        return out


def dashboard_key(folder: Path | None = None) -> str:
    """The key the dashboard asks for. Made once, kept in a file only its owner can read."""
    folder = Path(folder) if folder else home()
    p = folder / "dashboard.key"
    try:
        key = p.read_text(encoding="utf-8").strip()
        if len(key) >= 8:
            return key
    except OSError:
        pass
    key = secrets.token_urlsafe(9)
    folder.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(key + "\n")
    return key
