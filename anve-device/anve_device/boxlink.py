"""The box's link to the ANVE Box cloud (github.com/ANVEAI/anve-box, protocol 1).

This is the real-hardware side of part A of the team's docs/box-cloud-agreement.md. The
connection itself, the signed hello and the claim secret are the team's own code, copied
unchanged into ../anvebox_agent (see VENDORED.md there). What is written here is what
that code needs from a real box:

  * who the box is: /etc/anvebox/identity.json, made once by tools/anvebox_factory.py
    (root-only; systemd hands it to this program as a credential, the program is not root)
  * the box's own claim state, kept across power cuts: /var/lib/anvebox/state.json
    (whether it has an owner, its current claim secret, the "was reset" note), written
    to a temporary file and renamed, so a power cut mid-write cannot corrupt it
  * its mode, in the team's words (SETUP, CONNECTING, ONLINE_UNCLAIMED, READY, OFFLINE)
  * the light: the cloud's commands `light.identify` and `light.show` are played on the
    Pi's own green LED (the agreement's stand-in until an LED is fitted) and mirrored to
    LUMINA, the light strip

It runs beside the speaker (anve-device.service), not inside it, and needs nothing from
it: a box that was set up from the setup page only, with no key for the older platform,
is still online here, can be added to an account and answers "blink my box".

    python -m anve_device.boxlink              run (anve-boxlink.service does)
    python -m anve_device.boxlink --status     what it knows, without connecting
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import socket
import sys
import tempfile
import time
from pathlib import Path

from anvebox_agent import __version__ as AGENT_VERSION
from anvebox_agent.cloud.claim import CloudClaim
from anvebox_agent.cloud.link import WebSocketLink
from anvebox_agent.identity import Identity
from anvebox_agent.lights import IDENTIFY_SECONDS, PATTERNS, pattern_for
from anvebox_agent.model import OfflineReason, State
from anvebox_agent.redact import Secret

from . import config as C
from . import ident

log = logging.getLogger("anve.boxlink")

IDENTITY = Path("/etc/anvebox/identity.json")
STATE_DIR = Path(os.environ.get("STATE_DIRECTORY", "/var/lib/anvebox").split(":")[0])
RESET_NOTE = "reset-requested"        # left by the setup program on a factory reset
MAX_OVERLAY_SECONDS = 600             # as in the team's runtime.py
OVERLAY_STATES = (State.ONLINE_UNCLAIMED, State.READY)   # the cloud may change the light only while online
GRACE_S = 60.0                        # how long "can't reach ANVE" is ridden out before OFFLINE (states.md)


def link_url(cloud: str) -> str:
    """https://cloud.example -> wss://cloud.example/box/v1/link"""
    base = cloud.strip().rstrip("/")
    if base.startswith("https://"):
        base = "wss://" + base[len("https://"):]
    elif base.startswith("http://"):
        base = "ws://" + base[len("http://"):]
    return base + "/box/v1/link"


def load_identity(path: Path | None = None) -> Identity:
    """The identity the factory step made. Under systemd it arrives as a credential."""
    cred = os.environ.get("CREDENTIALS_DIRECTORY")
    p = path or (Path(cred) / "identity" if cred and (Path(cred) / "identity").exists() else IDENTITY)
    d = json.loads(p.read_text(encoding="utf-8"))
    return Identity(device_id=d["device_id"], setup_password=Secret(d["setup_password"]),
                    claim_secret=Secret(d["claim_secret"]), device_key=Secret(d["device_key"]))


class Disk:
    """The box's claim state on disk (the team's `ClaimDisk`). A missing or damaged file
    is a box with no owner yet; the secret then falls back to the card's first one."""

    def __init__(self, folder: Path = STATE_DIR):
        self.path = Path(folder) / "state.json"
        self.claimed, self.claim_secret, self.was_reset = False, None, False
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            self.claimed = d.get("claimed") is True
            self.claim_secret = d.get("claim_secret") if isinstance(d.get("claim_secret"), str) else None
            self.was_reset = d.get("was_reset") is True
        except FileNotFoundError:
            pass
        except (OSError, ValueError, AttributeError):
            log.warning("the box's state file could not be read: starting from 'no owner'")

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=".state-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"claimed": self.claimed, "claim_secret": self.claim_secret, "was_reset": self.was_reset}, f)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


class ActLed:
    """The Pi's own green LED. Normally it flickers with the SD card; while a pattern is
    played it is taken over, and handed back afterwards."""

    def __init__(self, name: str = "ACT"):
        self.dir = Path("/sys/class/leds") / name
        self.usual = ""
        self.taken = False

    def _write(self, what: str, value: str) -> bool:
        try:
            (self.dir / what).write_text(value)
            return True
        except OSError:
            return False

    def take(self):
        if self.taken:
            return
        try:
            words = (self.dir / "trigger").read_text().split()
            self.usual = next((w.strip("[]") for w in words if w.startswith("[")), "")
        except OSError:
            self.usual = ""
        self._write("trigger", "none")
        self.taken = True

    def set(self, level: float):
        self._write("brightness", "1" if level >= 0.5 else "0")

    def release(self):
        if not self.taken:
            return
        self.taken = False
        self._write("brightness", "0")
        if self.usual and self.usual != "none":
            self._write("trigger", self.usual)


class NoLed:
    def take(self): pass
    def set(self, level: float): pass
    def release(self): pass


class Mirror:
    """Tells LUMINA (the light strip) about a commanded pattern. Never waits, never raises."""

    def __init__(self, port: int = 8741):
        self.addr = ("127.0.0.1", port)
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.setblocking(False)
        except OSError:
            self.sock = None

    def send(self, message: dict):
        if self.sock is None:
            return
        try:
            self.sock.sendto(json.dumps(dict(message, source="boxlink")).encode(), self.addr)
        except OSError:
            pass


class BoxAgent:
    """What the team's link calls the "agent": the box's mode, its light and the commands.

    The mode follows the team's states.md, from what this program can see for itself:
    setup (the box has not been set up, its setup Wi-Fi is on), connecting, online with
    or without an owner, and offline once ANVE has been out of reach for a minute."""

    def __init__(self, *, led=None, mirror: Mirror | None = None, setup_open=lambda: False,
                 network_via=lambda: "wifi", clock=time.monotonic):
        self.led, self.mirror = led or NoLed(), mirror
        self.setup_open, self.network_via, self.clock = setup_open, network_via, clock
        self.claimed = False
        self.reachable = False
        self.state: State = State.CONNECTING
        self.offline_reason: OfflineReason | None = None
        self._down_since: float | None = clock()
        self._observers: list = []
        self._overlay: str | None = None
        self._overlay_until = 0.0
        self._player: asyncio.Task | None = None
        self.notes: list = []                 # (kind, text), for the tests

    # ---------- what the link needs (AgentPort) ----------

    def add_observer(self, observer):
        self._observers.append(observer)

    def status(self) -> dict:
        via = self.network_via()
        return {"mode": self.state.value, "light": self.shown_pattern, "network": {"via": via} if via else {}}

    def run_command(self, name: str, args: dict) -> tuple:
        """A command from the cloud (protocol 1). Returns (ok, error) at once."""
        if name == "status.get":
            return True, None
        if name == "light.identify":
            return self._show_overlay("identify", IDENTIFY_SECONDS)
        if name == "light.show":
            pattern, seconds = args.get("pattern"), args.get("seconds", 10)
            if not isinstance(pattern, str) or pattern not in PATTERNS:
                return False, "bad_args"
            if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
                return False, "bad_args"
            if not 1 <= seconds <= MAX_OVERLAY_SECONDS:
                return False, "bad_args"
            return self._show_overlay(pattern, float(seconds))
        return False, "unknown_command"

    # ---------- the mode ----------

    @property
    def shown_pattern(self) -> str:
        return self._overlay or pattern_for(self.state, self.offline_reason)

    def _go(self, state: State, reason: OfflineReason | None = None):
        if state is self.state and reason is self.offline_reason:
            return
        old, self.state, self.offline_reason = self.state, state, reason
        if state not in OVERLAY_STATES:
            self._drop_overlay()            # a commanded light never hides a problem
        if old is not state:
            log.info("mode %s -> %s%s", old.value, state.value, f" ({reason.value})" if reason else "")
            self._tell("event", {"name": "mode.changed", "from": old.value, "to": state.value})
        self._tell("status", {})

    def on_reachable(self, reachable: bool):
        """The link's word on whether ANVE answers."""
        self.reachable = reachable
        self._down_since = None if reachable else self.clock()
        self.tick()

    def on_owner(self, claimed: bool):
        self.claimed = claimed
        self.tick()

    def tick(self):
        """Work out the mode again. Called on every change and once a second."""
        if self.setup_open():
            return self._go(State.SETUP)
        if self.reachable:
            return self._go(State.READY if self.claimed else State.ONLINE_UNCLAIMED)
        waited = self.clock() - (self._down_since if self._down_since is not None else self.clock())
        if self.state in (State.SETUP, State.CONNECTING) and waited < GRACE_S:
            return self._go(State.CONNECTING)
        if waited < GRACE_S and self.state in OVERLAY_STATES:
            return None                      # the grace period: stays as it is
        self._go(State.OFFLINE, OfflineReason.ANVE_UNREACHABLE if self.network_via() else OfflineReason.NO_NETWORK)

    # ---------- the light ----------

    def _show_overlay(self, pattern: str, seconds: float) -> tuple:
        if self.state not in OVERLAY_STATES:
            return False, "busy"             # setup, offline and reset lights always win
        self._drop_overlay(tell=False)
        self._overlay, self._overlay_until = pattern, self.clock() + seconds
        p = PATTERNS[pattern]
        log.info("light: %s for %.0f s, asked for by the cloud", p.label.lower(), seconds)
        self.notes.append(("light", pattern))
        if self.mirror:
            self.mirror.send({"type": "pattern", "name": pattern, "steps": [list(s) for s in p.steps],
                              "smooth": p.smooth, "seconds": seconds})
        try:
            self._player = asyncio.get_running_loop().create_task(self._play(p, seconds))
        except RuntimeError:
            self._player = None              # no loop (a test): the state is kept all the same
        self._tell("status", {})
        return True, None

    async def _play(self, p, seconds: float):
        """Blink the LED through the pattern until its time is up. Never blocks the link."""
        end = self.clock() + seconds
        self.led.take()
        try:
            while self.clock() < end:
                for level, ms in p.steps:
                    self.led.set(level)
                    left = end - self.clock()
                    if left <= 0:
                        break
                    await asyncio.sleep(min(ms / 1000.0, left))
        except asyncio.CancelledError:
            raise
        finally:
            self.led.release()
        self._overlay, self._player = None, None
        self._tell("status", {})

    def _drop_overlay(self, tell: bool = True):
        if self._player is not None:
            self._player.cancel()
            self._player = None
        if self._overlay is None:
            return
        self._overlay = None
        self.led.release()
        if self.mirror:
            self.mirror.send({"type": "pattern", "name": "off", "steps": [], "seconds": 0})
        if tell:
            self._tell("status", {})

    def _tell(self, kind: str, data: dict):
        for observer in list(self._observers):
            try:
                observer(kind, data)
            except Exception:                # an observer must never break the box
                log.exception("observer failed")


def network_via() -> str:
    """"wifi", "cable" or "" (no way out at all), from the routing table."""
    try:
        with open("/proc/net/route", encoding="ascii") as f:
            for line in f.readlines()[1:]:
                p = line.split()
                if len(p) > 3 and p[1] == "00000000" and int(p[3], 16) & 2:
                    return "wifi" if p[0].startswith("wl") else "cable"
    except (OSError, ValueError):
        return "wifi"                        # not Linux (a test on a laptop): assume the usual
    return ""


def build(identity: Identity, cloud: str, *, disk: Disk | None = None, agent: BoxAgent | None = None, **link_args):
    """Wire the team's link and claim layer to this box. Returns (link, claim, agent, disk)."""
    disk = disk or Disk()
    agent = agent or BoxAgent()
    link = WebSocketLink(url=link_url(cloud), identity=identity, software=f"anve-device {ident.VERSION} / agent {AGENT_VERSION}",
                         **link_args)
    claim = CloudClaim(disk=disk, save=disk.save, first_secret=identity.claim_secret.reveal(), link=link)
    agent.claimed = claim.is_claimed()
    claim.listen(lambda event: agent.on_owner(bool(getattr(event, "claimed", False))))
    link.listen(agent.on_reachable)
    link.attach(agent)
    return link, claim, agent, disk


def claim_link(cloud: str, identity: Identity, disk: Disk | None = None) -> str:
    """The link that adds this box to an account. SECRET until it has been used: whoever
    opens it owns the box. The secret sits after the #, which browsers never send."""
    disk = disk or Disk()
    secret = disk.claim_secret or identity.claim_secret.reveal()
    return f"{cloud.rstrip('/')}/claim#{identity.device_id}.{secret}"


async def run(cfg: dict):
    b = cfg["boxlink"]
    identity = load_identity()
    led = ActLed(b["led"]) if b["led"] else NoLed()
    agent = BoxAgent(led=led, mirror=Mirror(cfg["lumina"]["port"]) if cfg["lumina"]["enabled"] else None,
                     setup_open=lambda: not ident.provisioned(), network_via=network_via)
    link, claim, agent, disk = build(identity, b["cloud"], agent=agent)
    log.info("box %s: linking to %s (owner on record: %s)", identity.device_id, link.url, "yes" if disk.claimed else "no")
    link.start()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            pass
    told = None
    note = STATE_DIR / RESET_NOTE
    while not stop.is_set():
        if note.exists():
            # the setup program did a factory reset: no owner, a new claim secret, and a
            # note for the cloud, delivered in the next hello (the team's CloudClaim.forget)
            log.warning("factory reset: the box drops its owner and makes a new claim secret")
            claim.forget()
            try:
                note.unlink()
            except OSError:
                pass
        agent.tick()
        if link.last_problem != told:
            told = link.last_problem
            if told:
                log.warning("link: %s", told)
        try:
            await asyncio.wait_for(stop.wait(), 1.0)
        except asyncio.TimeoutError:
            pass
    agent._drop_overlay(tell=False)
    await link.stop()
    led.release()


def main() -> int:
    ap = argparse.ArgumentParser(description="The box's link to the ANVE Box cloud")
    ap.add_argument("--config")
    ap.add_argument("--status", action="store_true", help="say what is on record, without connecting")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    cfg = C.load(a.config)
    if a.status:
        try:
            identity = load_identity()
        except (OSError, ValueError, KeyError) as e:
            print(f"no identity yet ({type(e).__name__}): run tools/anvebox_factory.py on the box first")
            return 1
        d = Disk()
        print(f"box {identity.device_id}, cloud {cfg['boxlink']['cloud']}, owner on record: {'yes' if d.claimed else 'no'}")
        return 0
    if not cfg["boxlink"]["enabled"]:
        log.info("the box link is switched off in the settings")
        return 0
    try:
        asyncio.run(run(cfg))
    except (OSError, ValueError, KeyError) as e:
        log.error("cannot start: %s: %s (was the identity made with tools/anvebox_factory.py?)", type(e).__name__, e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
