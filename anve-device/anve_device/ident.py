"""Who this box is: its short id, and the name and room its owner gave it.

    /var/lib/anve-device/device.json    {"name": ..., "room": ...}   (not secret)
    /var/lib/anve-device/provisioned    exists once first-time setup has been completed

`ANVE_STATE_DIR` moves the folder (the tests use a temporary one)."""
import json
import os
import re
from pathlib import Path

VERSION = "0.4.0"
PORT = 8738                       # the app's API on the running speaker
SETUP_ADDR = "10.42.0.1"          # the speaker's address on its own setup hotspot
SETUP_PORT = 80


def state_dir() -> Path:
    return Path(os.environ.get("ANVE_STATE_DIR", "/var/lib/anve-device"))


def device_id() -> str:
    """The last four hex digits of the board's serial number: stable, and short enough
    to read on a phone ("ANVE-4C39")."""
    try:
        for line in open("/proc/cpuinfo"):
            if line.startswith("Serial"):
                return line.split(":")[1].strip()[-4:].lower()
    except OSError:
        pass
    try:
        return open("/etc/machine-id").read().strip()[-4:].lower()
    except OSError:
        return "0000"


def clean(text, limit: int = 40) -> str:
    """A name typed on a phone: printable, single-spaced, not too long."""
    s = re.sub(r"\s+", " ", "".join(ch for ch in str(text or "") if ch.isprintable())).strip()
    return s[:limit]


def load() -> dict:
    d = {"id": device_id(), "name": "ANVE Box", "room": ""}
    try:
        saved = json.loads((state_dir() / "device.json").read_text())
        d["name"] = clean(saved.get("name")) or d["name"]
        d["room"] = clean(saved.get("room"))
    except (OSError, ValueError, AttributeError):
        pass
    return d


def save(name: str, room: str):
    p = state_dir() / "device.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"name": clean(name) or "ANVE Box", "room": clean(room)}))
    os.replace(tmp, p)


def provisioned() -> bool:
    return (state_dir() / "provisioned").exists()
