"""First-time setup without a developer: the box opens its own Wi-Fi hotspot, and the
phone app sends it everything it needs.

    A box that has never been set up (no /var/lib/anve-device/provisioned):
      1. looks which Wi-Fi networks are in range (it cannot look while the hotspot is up)
      2. opens the hotspot "ANVE-xxxx" (open network, the box is 10.42.0.1)
      3. serves, on that hotspot only (port 80):
           GET  /api/hello      who it is, its public key, why the last try failed
           GET  /api/networks   the Wi-Fi networks it saw
           POST /api/provision  sealed (pairing.py): the home Wi-Fi name and password,
                                an optional sign-in for Wi-Fi with a login page, the
                                box's name and room, and its ANVE device key
      4. answers (sealed) with the token the app will use from then on, closes the
         hotspot and joins the home Wi-Fi
      5. worked: stores key and settings, marks the box as set up, starts the speaker.
         did not work (wrong password, out of range): forgets that Wi-Fi and opens the
         hotspot again; /api/hello now says why.

    A box that is set up: this program only waits. The speaker asks it (over a local
    socket) for a factory reset when the owner chooses that in the app.

    Hotspot test (tools/hotspot_trial.sh): on a box that IS set up, open the hotspot for
    a few minutes and change nothing. A phone can find the box, join it, read the Wi-Fi
    list and send its settings; the box checks them, answers "trial" and stores nothing.
    Then it goes back to the Wi-Fi it was on. For trying the app on real hardware.

    Set up again (tools/hotspot_trial.sh --apply): the same window, but for real. What
    the phone sends IS applied: the box joins that Wi-Fi and takes the new name and key
    (the old key is kept beside it as key.before-resetup). If the Wi-Fi cannot be joined
    the hotspot comes back with the reason, as in a first setup; if the window ends with
    nothing applied, the box returns to the Wi-Fi and key it had. (The Wi-Fi profile it
    was on is kept as a root-only copy, wifi.before-resetup, for the time of the window:
    joining another Wi-Fi deletes that profile, and without the copy a wrong password
    would have left the box with no way home.)

    Optional (ANVE_RECOVERY=1, off by default until it has been tried on real hardware):
    when a set-up box has had no Wi-Fi for 3 minutes, it opens the hotspot for 4 minutes
    so a paired phone can give it a new Wi-Fi, then tries the old Wi-Fi again, in turns.

    The setup page (added 19 Nov 2026; nothing above changes): the same hotspot also
    serves a page, for setting the box up from a computer or any browser, with no app.
      * A computer or phone that joins the hotspot opens the page by itself (a "captive
        portal"): while the hotspot is up every name leads to the box, and the box answers
        the "am I online?" questions of Windows, macOS, iOS and Android with a redirect
        to http://10.42.0.1/. Typing that address, or any address, works too.
        ANVE_CAPTIVE=0 turns the automatic opening off; the page stays.
      * GET  /                     the page (portal/, built on the ANVE Box team's page)
        GET  /api/status           what the page shows, in the team's shape; never a secret
        GET  /api/alive            the open page asks every few seconds, so it can say that
                                   the box has left; also how long a test window has left
        POST /api/portal/connect   sealed, like /api/provision: Wi-Fi name and password,
                                   optional Wi-Fi sign-in, name, room, optional voice key.
                                   The sealed answer carries the link that adds the box to
                                   an ANVE account (when the box has an identity for the
                                   ANVE Box cloud and no owner yet).
        POST /api/portal/go        "I have the link, join the Wi-Fi now". Until then (at
                                   most 2 minutes) the hotspot stays up, because the page
                                   disappears from some devices the moment it closes.
        POST /api/connect          the team's unsealed call: refused. This hotspot is an
                                   open network, so a password must never cross it in the
                                   clear; the page seals it in the browser instead.
      * A box set up from the page alone is on the Wi-Fi and in the ANVE Box cloud. The
        voice assistant needs its key too: typed into the page, or the box is set up with
        the phone app instead. A box that was set up WITHOUT the key cannot be given one by
        the app afterwards: the app talks to the speaker, and the speaker does not run
        without its key. It has to be set up again (tools/hotspot_trial.sh --apply, or a
        factory reset), which today needs someone at the box's command line.

It runs as root, because it changes the network and writes the root-only key file. It
uses NetworkManager through `nmcli`. Passwords and keys are never logged and never put
on a command line (connections are written as root-only NetworkManager files).

    python3 -m anve_device.provision                normal use (systemd)
    python3 -m anve_device.provision --dry-run ...  no network changes; for the tests
"""
import argparse
import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import socket
import struct
import time
from pathlib import Path

from . import ident
from .httpd import HttpError, Raw, Server
from .pairing import BadMessage, Box, b64, new_token

log = logging.getLogger("anve.setup")

IFACE = "wlan0"
AP_CON, WIFI_CON = "anve-setup", "anve-wifi"
NM_DIR = Path("/etc/NetworkManager/system-connections")
WIFI_KEPT = "wifi.before-resetup"     # in etc_dir(), root-only, only during "set up again"
SOCK = "/run/anve-provision.sock"
REASONS = ("wrong_password", "not_found", "timeout", "unsupported", "failed", "internal")

# ---- the setup page ----
PORTAL_DIR = Path(__file__).resolve().parent.parent / "portal"
TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8", ".svg": "image/svg+xml", ".woff2": "font/woff2"}
# While the hotspot is up, NetworkManager's own dnsmasq (it serves the hotspot's addresses)
# reads this folder. One line makes every name lead to the box, which is what makes a
# computer open the setup page by itself. The file exists only while the hotspot is up.
CAPTIVE_CONF = Path("/etc/NetworkManager/dnsmasq-shared.d/anve-captive.conf")
CAPTIVE_TEXT = ("# ANVE Box setup (anve_device/provision.py). Written when the setup hotspot opens,\n"
                "# removed when it closes: every name leads to the box's setup page.\n"
                f"address=/#/{ident.SETUP_ADDR}\n")
# the box's identity for the ANVE Box cloud and its claim state (see boxlink.py)
BOX_IDENTITY = Path(os.environ.get("ANVEBOX_IDENTITY", "/etc/anvebox/identity.json"))
BOX_STATE = Path(os.environ.get("ANVEBOX_STATE", "/var/lib/anvebox"))
# what the team's page calls the reasons a Wi-Fi could not be joined
PAGE_REASONS = {"timeout": "no_answer", "failed": "no_address"}


def captive_on() -> bool:
    return os.environ.get("ANVE_CAPTIVE", "1") != "0"


def hold_s() -> int:
    """How long the hotspot waits for "I have the link" before it joins the Wi-Fi anyway."""
    try:
        return max(1, min(600, int(os.environ.get("ANVE_PORTAL_HOLD_S", "120"))))
    except ValueError:
        return 120


def etc_dir() -> Path:
    return Path(os.environ.get("ANVE_ETC_DIR", "/etc/anve-device"))


def _secret_write(path: Path, text: str):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.chmod(path, 0o600)


def _kf(s: str) -> str:
    """A string value in a NetworkManager keyfile."""
    s = s.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    return "\\s" + s[1:] if s.startswith(" ") else s


def wifi_keyfile(ssid: str, password: str, hidden: bool, sec: str) -> str:
    # the name as a list of bytes: no escaping rules to get wrong, any name works
    lines = ["[connection]", f"id={WIFI_CON}", "type=wifi", f"interface-name={IFACE}",
             "autoconnect=true", "autoconnect-priority=50", "",
             "[wifi]", "mode=infrastructure", "ssid=" + "".join(f"{b};" for b in ssid.encode())]
    if hidden:
        lines.append("hidden=true")
    if password:
        lines += ["", "[wifi-security]", f"key-mgmt={'sae' if sec == 'sae' else 'wpa-psk'}",
                  f"psk={_kf(password)}"]
    return "\n".join(lines + ["", "[ipv4]", "method=auto", "", "[ipv6]", "method=auto", ""])


def ap_keyfile(ssid: str) -> str:
    return "\n".join([
        "[connection]", f"id={AP_CON}", "type=wifi", f"interface-name={IFACE}",
        "autoconnect=false", "",
        "[wifi]", "mode=ap", "ssid=" + "".join(f"{b};" for b in ssid.encode()),
        "band=bg", "channel=6", "",
        # "shared": NetworkManager gives phones an address (10.42.0.x) by itself
        "[ipv4]", "method=shared", f"address1={ident.SETUP_ADDR}/24", "",
        "[ipv6]", "method=disabled", ""])


def parse_scan(out: str, own: str) -> list:
    """`nmcli -t -f IN-USE,SIGNAL,SECURITY,SSID dev wifi list` -> networks, best first."""
    best: dict = {}
    for line in out.splitlines():
        p = re.split(r"(?<!\\):", line, maxsplit=3)
        if len(p) != 4:
            continue
        ssid = p[3].replace("\\:", ":").replace("\\\\", "\\")
        if not ssid or ssid == own:
            continue
        sec = p[2].upper()
        kind = "" if not sec or sec == "--" else "sae" if ("WPA3" in sec and "WPA2" not in sec) else "psk"
        ok = "802.1X" not in sec and "WEP" not in sec
        try:
            sig = int(p[1])
        except ValueError:
            sig = 0
        if ssid not in best or sig > best[ssid]["signal"]:
            best[ssid] = {"ssid": ssid, "signal": sig, "secure": bool(kind) or not ok,
                          "sec": kind, "supported": ok}
    return sorted(best.values(), key=lambda n: -n["signal"])[:40]


class Net:
    """The network, through NetworkManager."""

    async def _run(self, *argv, timeout: float = 30):
        try:
            p = await asyncio.create_subprocess_exec(
                *argv, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE)
        except OSError as e:
            return 127, "", str(e)
        try:
            out, err = await asyncio.wait_for(p.communicate(), timeout)
        except asyncio.TimeoutError:
            p.kill()
            return 124, "", "timeout"
        return p.returncode, out.decode(errors="replace"), err.decode(errors="replace")

    async def ready(self) -> bool:
        rc, out, _ = await self._run("nmcli", "-t", "-f", "DEVICE,STATE", "dev", timeout=10)
        return rc == 0 and any(line.startswith(IFACE + ":") and "unavailable" not in line
                               and "unmanaged" not in line for line in out.splitlines())

    async def scan(self, own: str) -> list:
        rc, out, err = await self._run("nmcli", "-t", "-f", "IN-USE,SIGNAL,SECURITY,SSID", "dev",
                                       "wifi", "list", "ifname", IFACE, "--rescan", "yes")
        if rc:
            log.warning("Wi-Fi scan failed: %s", err.strip()[:120])
        return parse_scan(out, own)

    async def connected(self) -> str:
        """The name of the Wi-Fi connection in use ('' when none, or our own hotspot)."""
        rc, out, _ = await self._run("nmcli", "-t", "-f", "GENERAL.STATE,GENERAL.CONNECTION",
                                     "dev", "show", IFACE, timeout=10)
        d = dict(line.split(":", 1) for line in out.splitlines() if ":" in line)
        con = d.get("GENERAL.CONNECTION", "")
        return con if d.get("GENERAL.STATE", "").startswith("100") and con != AP_CON else ""

    async def ap_active(self) -> bool:
        rc, out, _ = await self._run("nmcli", "-t", "-f", "GENERAL.STATE,GENERAL.CONNECTION",
                                     "dev", "show", IFACE, timeout=10)
        return f"GENERAL.CONNECTION:{AP_CON}" in out and "GENERAL.STATE:100" in out

    def captive(self, on: bool):
        """Every name leads to the box (on), or the file is gone (off). It only matters
        while the hotspot is up: nothing else on this Pi reads that folder."""
        try:
            if on and captive_on():
                CAPTIVE_CONF.parent.mkdir(parents=True, exist_ok=True)
                CAPTIVE_CONF.write_text(CAPTIVE_TEXT)
            else:
                CAPTIVE_CONF.unlink(missing_ok=True)
        except OSError as e:
            log.warning("the setup page will not open by itself (%s); http://%s/ still works",
                        e.__class__.__name__, ident.SETUP_ADDR)

    async def ap_up(self, ssid: str) -> bool:
        path = NM_DIR / f"{AP_CON}.nmconnection"
        _secret_write(path, ap_keyfile(ssid))
        self.captive(True)
        await self._run("nmcli", "con", "load", str(path))
        rc, _, err = await self._run("nmcli", "-w", "25", "con", "up", "id", AP_CON, timeout=35)
        if rc:
            log.error("the setup hotspot did not come up: %s", err.strip()[:160])
        return rc == 0

    async def ap_down(self):
        await self._run("nmcli", "con", "down", "id", AP_CON, timeout=15)
        await self._run("nmcli", "con", "delete", "id", AP_CON, timeout=15)
        self.captive(False)

    async def join(self, ssid: str, password: str, hidden: bool, sec: str):
        """-> (worked, reason)"""
        await self.forget()
        path = NM_DIR / f"{WIFI_CON}.nmconnection"
        _secret_write(path, wifi_keyfile(ssid, password, hidden, sec))
        rc, _, err = await self._run("nmcli", "con", "load", str(path))
        if rc:
            return False, "failed"
        if not hidden:
            await self._run("nmcli", "dev", "wifi", "rescan", "ifname", IFACE, timeout=15)
            await asyncio.sleep(4)
        rc, _, err = await self._run("nmcli", "-w", "45", "con", "up", "id", WIFI_CON, timeout=60)
        if rc == 0:
            return True, ""
        e = err.lower()
        if "secrets were required" in e or "psk" in e or "(7)" in e:
            return False, "wrong_password"
        if "ssid" in e and ("not found" in e or "no network" in e) or "(53)" in e:
            return False, "not_found"
        if rc == 124 or "timeout" in e or "timed out" in e:
            return False, "timeout"
        log.warning("joining the Wi-Fi failed: %s", err.strip()[:160])
        return False, "failed"

    async def forget(self):
        await self._run("nmcli", "con", "delete", "id", WIFI_CON, timeout=15)

    # "Set up again" on a box that is on WIFI_CON: join() and forget() delete that very
    # profile, the only way home. So a root-only copy is kept for the time of the window
    # and put back when nothing was applied (found 19 Nov 2026, before the first real run).
    def keep(self) -> bool:
        try:
            _secret_write(etc_dir() / WIFI_KEPT, (NM_DIR / f"{WIFI_CON}.nmconnection").read_text())
            return True
        except OSError as e:
            log.error("could not keep a copy of the Wi-Fi profile (%s)", e.__class__.__name__)
            return False

    def kept(self) -> bool:
        return (etc_dir() / WIFI_KEPT).exists()

    def drop_kept(self):
        try:
            (etc_dir() / WIFI_KEPT).unlink(missing_ok=True)
        except OSError:
            pass

    async def put_back(self) -> bool:
        """The kept profile back in its place -> True when it is there again."""
        try:
            text = (etc_dir() / WIFI_KEPT).read_text()
        except OSError:
            return False
        path = NM_DIR / f"{WIFI_CON}.nmconnection"
        try:
            _secret_write(path, text)
        except OSError as e:
            log.error("could not put the Wi-Fi profile back (%s)", e.__class__.__name__)
            return False
        rc, _, err = await self._run("nmcli", "con", "load", str(path))
        if rc:
            log.error("could not load the Wi-Fi profile that was put back: %s", err.strip()[:120])
            return False
        self.drop_kept()
        return True

    async def restore(self, name: str) -> bool:
        """Back onto the Wi-Fi the box was on before a hotspot test."""
        for _ in range(3):
            if name:
                rc, _, _ = await self._run("nmcli", "-w", "40", "con", "up", "id", name, timeout=50)
                if rc == 0:
                    return True
            rc, _, _ = await self._run("nmcli", "-w", "40", "dev", "connect", IFACE, timeout=50)
            if rc == 0:
                return True
            await asyncio.sleep(5)
        return False

    async def unit(self, verb: str, name: str):
        rc, _, err = await self._run("systemctl", verb, name, timeout=40)
        if rc:
            log.warning("systemctl %s %s: %s", verb, name, err.strip()[:120])


class FakeNet(Net):
    """No network changes at all (--dry-run): the tests drive the whole flow with it. A
    Wi-Fi password that starts with "wrong" is refused, the name "Nowhere" is not found."""

    def __init__(self):
        self.ap = False
        self.joined = ""
        self.calls: list = []
        self.profile = False              # is there a saved WIFI_CON profile?
        self.kept_copy = False

    async def ready(self):
        return True

    def keep(self):
        self.kept_copy = self.profile
        self.calls.append(("keep", self.kept_copy))
        return self.kept_copy

    def kept(self):
        return self.kept_copy

    def drop_kept(self):
        self.kept_copy = False

    async def put_back(self):
        if not self.kept_copy:
            return False
        self.profile, self.kept_copy = True, False
        self.calls.append(("put_back",))
        return True

    def captive(self, on):
        self.calls.append(("captive", bool(on) and captive_on()))

    async def scan(self, own):
        return parse_scan(" :82:WPA2:Home Wi-Fi\n :64:WPA1 WPA2:Caf\\:e\n :40::Open Campus\n"
                          " :33:WPA2 802.1X:Office\n :30:WPA2:Home Wi-Fi\n", own)

    async def connected(self):
        return self.joined

    async def ap_active(self):
        return self.ap

    async def ap_up(self, ssid):
        self.ap = True
        self.captive(True)
        self.calls.append(("ap_up", ssid))
        return True

    async def ap_down(self):
        self.ap = False
        self.calls.append(("ap_down",))
        self.captive(False)

    async def join(self, ssid, password, hidden, sec):
        self.calls.append(("join", ssid, hidden, sec))
        wifi_keyfile(ssid, password, hidden, sec)
        self.profile, self.joined = False, ""                # like the real one: the old profile goes first
        await asyncio.sleep(0.2)
        if password.startswith("wrong"):
            return False, "wrong_password"
        if ssid == "Nowhere":
            return False, "not_found"
        self.profile, self.joined = True, WIFI_CON
        return True, ""

    async def forget(self):
        self.profile, self.joined = False, ""
        self.calls.append(("forget",))

    async def restore(self, name):
        # like the real one: a profile that was deleted cannot be brought up
        ok = bool(name) and (name != WIFI_CON or self.profile)
        self.joined = name if ok else ""
        self.calls.append(("restore", name, ok))
        return ok

    async def unit(self, verb, name):
        self.calls.append(("systemctl", verb, name))


def _check(p: dict, *, need_key: bool) -> dict:
    """What the app sent, checked field by field. Anything odd is refused."""
    def bad(field):
        return HttpError(400, "bad_field", field=field)
    w = p.get("wifi")
    if not isinstance(w, dict):
        raise bad("wifi")
    ssid, pw = w.get("ssid"), w.get("password", "")
    if not isinstance(ssid, str) or not (1 <= len(ssid.encode()) <= 32) or "\x00" in ssid:
        raise bad("wifi.ssid")
    if not isinstance(pw, str) or (pw and not (re.fullmatch(r"[\x20-\x7e]{8,63}", pw) or
                                               re.fullmatch(r"[0-9a-fA-F]{64}", pw))):
        raise bad("wifi.password")
    sec = w.get("sec") if w.get("sec") in ("", "psk", "sae") else ("psk" if pw else "")
    out = {"ssid": ssid, "password": pw, "hidden": bool(w.get("hidden")), "sec": sec,
           "name": ident.clean(p.get("name")) or "ANVE Box", "room": ident.clean(p.get("room")),
           "portal": None, "key": None}
    portal = p.get("portal")
    if portal:
        u, s = (portal.get("username"), portal.get("password")) if isinstance(portal, dict) else (None, None)
        ok = lambda v: isinstance(v, str) and 1 <= len(v) <= 128 and v.isprintable()  # noqa: E731
        if not (ok(u) and ok(s)):
            raise bad("portal")
        out["portal"] = (u, s)
    key = p.get("key")
    if key is not None:
        if not isinstance(key, str) or not re.fullmatch(r"[\x21-\x7e]{16,256}", key):
            raise bad("key")
        out["key"] = key
    elif need_key:
        raise bad("key")
    return out


class Provision:
    def __init__(self, net: Net, *, dry: bool = False, host: str = "0.0.0.0",
                 port: int = ident.SETUP_PORT, recovery: bool = False):
        self.net, self.dry, self.host, self.port = net, dry, host, port
        self.recovery = recovery
        self.id = ident.device_id()
        self.ssid = f"ANVE-{self.id.upper()}"
        self.box = Box()
        self.http = Server()
        self.mode = "starting"            # setup | joining | normal | recovery
        self.error = ""
        self.networks: list = []
        self.busy = False
        self.offline_since = None
        self.last_request = 0.0
        self.trial = False                # a hotspot test: nothing may be changed
        self.trial_got = False
        self.again = False                # "set up again": applied for real, with a way back
        self._peers: set = set()
        self.http.route("GET", "/api/hello", self.h_hello)
        self.http.route("GET", "/api/networks", self.h_networks)
        self.http.route("POST", "/api/provision", self.h_provision)
        self.http.route("POST", "/api/wifi", self.h_wifi)
        # the setup page (see the top of this file)
        self.error_net = ""               # the Wi-Fi the last error is about
        self._held = None                 # a setup the page sent, waiting for "connect now"
        self._files = None                # the page's files, read once
        self._askers: set = set()
        self.alive_at = 0.0               # when an open setup page last asked "are you there?"
        self.window_end = None            # when a hotspot test / set-up-again window closes
        self.http.route("GET", "/api/status", self.h_status)
        self.http.route("GET", "/api/alive", self.h_alive)
        self.http.route("POST", "/api/portal/connect", self.h_portal)
        self.http.route("POST", "/api/portal/go", self.h_go)
        self.http.route("POST", "/api/connect", self.h_unsealed)
        self.http.fallback = self.h_page

    # ---------- files ----------

    def token(self) -> str:
        p = etc_dir() / "app-token"
        try:
            t = p.read_text().strip()
            if re.fullmatch(r"[0-9a-f]{64}", t):
                return t
        except OSError:
            pass
        t = new_token()
        _secret_write(p, t)
        return t

    def _store(self, w: dict, *, full: bool):
        etc = etc_dir()
        if w["portal"]:
            _secret_write(etc / "wifi-login", f"{w['portal'][0]}\n{w['portal'][1]}\n")
        elif full and not self.again:
            (etc / "wifi-login").unlink(missing_ok=True)
        if not full:
            return
        if w["key"]:
            if self.again and (etc / "key").exists():
                # the key that worked until now stays beside the new one, root-only
                _secret_write(etc / "key.before-resetup", (etc / "key").read_text())
            _secret_write(etc / "key", w["key"])
        ident.save(w["name"], w["room"])
        # the speaker (an ordinary user) renames the box later: the file is its to write
        st = os.stat(ident.state_dir())
        if hasattr(os, "chown"):                             # (not on Windows: --dry-run only)
            os.chown(ident.state_dir() / "device.json", st.st_uid, st.st_gid)
        (ident.state_dir() / "provisioned").write_text(time.strftime("%Y-%m-%d %H:%M:%S\n"))

    # ---------- the hotspot ----------

    async def _open(self, mode: str) -> bool:
        if not await self.net.ap_active():
            self.networks = await self.net.scan(self.ssid)
            if not await self.net.ap_up(self.ssid):
                return False
        if not self.http.running:
            await self.http.start(self.host, self.port)
        self.mode, self.last_request = mode, time.monotonic()
        log.info("%s hotspot \"%s\" is open (%d Wi-Fi networks seen)", mode, self.ssid,
                 len(self.networks))
        return True

    async def _close(self):
        await self.http.stop()
        await self.net.ap_down()

    def _guard(self, q):
        """Only the hotspot may talk to this server, never the home network."""
        if not self.dry and q.local != ident.SETUP_ADDR:
            raise HttpError(403, "setup_hotspot_only")
        self.last_request = time.monotonic()
        if q.peer not in self._peers:
            self._peers.add(q.peer)
            log.info("a phone is talking to the setup server (%s)", q.peer)

    # ---------- routes ----------

    async def h_hello(self, q):
        self._guard(q)
        d = ident.load()
        return {"ok": True, "id": self.id, "name": d["name"], "version": ident.VERSION,
                "setup": self.mode == "setup", "recovery": self.mode == "recovery",
                "busy": self.busy, "error": self.error, "trial": self.trial, "pub": b64(self.box.public),
                "has_key": (etc_dir() / "key").exists()}

    async def h_networks(self, q):
        self._guard(q)
        log.info("the phone asked for the Wi-Fi list (%d networks)", len(self.networks))
        # "bars" (1-4) is what the setup page draws; the app reads "signal" as before
        return {"ok": True, "networks": [dict({k: n[k] for k in ("ssid", "signal", "secure", "sec", "supported")},
                                              bars=1 + (n["signal"] >= 35) + (n["signal"] >= 55) + (n["signal"] >= 75))
                                         for n in self.networks]}

    def _sealed(self, q, mode: str):
        self._guard(q)
        if self.mode != mode:
            raise HttpError(409, "not_in_" + mode)
        if self.busy:
            raise HttpError(409, "busy")
        try:
            return self.box.open(q.json())
        except BadMessage:
            raise HttpError(400, "bad_message") from None

    async def h_provision(self, q):
        p, key = self._sealed(q, "setup")
        try:
            w = _check(p, need_key=not (etc_dir() / "key").exists())
        except HttpError as e:
            log.info("the phone's settings were refused: %s", e.body.get("field") or e.body.get("error"))
            raise
        if self.trial:
            # a test: the message was opened and every field is valid, and that is all
            self.trial_got = True
            log.info("hotspot test: a phone sent complete, valid settings (Wi-Fi \"%s\", %s password, "
                     "%s key); nothing was changed", w["ssid"], "with a" if w["password"] else "no",
                     "with an ANVE" if w["key"] else "no")
            raise HttpError(409, "trial")
        self.busy, self.error = True, ""
        asyncio.create_task(self._apply(w, full=True))
        return Box.reply(key, {"ok": True, "id": self.id, "token": self.token(),
                               "host": f"anve-{self.id}.local", "port": ident.PORT})

    async def h_wifi(self, q):
        """Recovery: a paired phone gives a set-up box a new Wi-Fi."""
        p, key = self._sealed(q, "recovery")
        want = hmac.new(bytes.fromhex(self.token()), b"anve-recovery|" + str(q.json().get("k")).encode(),
                        hashlib.sha256).hexdigest()
        if not hmac.compare_digest(want, str(p.get("proof") or "")):
            await asyncio.sleep(1.0)
            raise HttpError(403, "not_paired")
        w = _check(p, need_key=False)
        self.busy, self.error = True, ""
        asyncio.create_task(self._apply(w, full=False))
        return Box.reply(key, {"ok": True, "id": self.id})

    # ---------- the setup page ----------

    def _cloud_box(self) -> dict | None:
        """What this box is to the ANVE Box cloud: its ID, whether it has an owner, and the
        link that makes someone its owner. The link is SECRET: it is never logged and only
        ever leaves inside a sealed answer. None when the box has no identity for that cloud."""
        try:
            d = json.loads(BOX_IDENTITY.read_text(encoding="utf-8"))
            box_id, first = str(d["device_id"]), str(d["claim_secret"])
        except (OSError, ValueError, KeyError, TypeError):
            return None
        claimed, secret = False, None
        try:
            s = json.loads((BOX_STATE / "state.json").read_text(encoding="utf-8"))
            claimed = s.get("claimed") is True
            secret = s.get("claim_secret") if isinstance(s.get("claim_secret"), str) else None
        except (OSError, ValueError, AttributeError):
            pass
        try:
            from . import config as C                        # the same setting boxlink.py reads
            cloud = str(C.load()["boxlink"]["cloud"]).strip().rstrip("/")
        except Exception:                                    # noqa: BLE001
            cloud = ""
        # a factory reset the link has not taken in yet: the secret is about to change
        waiting = (BOX_STATE / "reset-requested").exists()
        link = f"{cloud}/claim#{box_id}.{secret or first}" if cloud and not claimed and not waiting else None
        return {"id": box_id, "claimed": claimed and not waiting, "cloud": cloud, "link": link}

    def _static(self) -> dict:
        if self._files is None:
            files = {}
            try:
                for p in sorted(PORTAL_DIR.rglob("*")):
                    rel = p.relative_to(PORTAL_DIR).as_posix()
                    if p.is_file() and p.suffix in TYPES and not rel.startswith("src/"):
                        files["/" + rel] = (TYPES[p.suffix], p.read_bytes())
            except OSError:
                log.warning("the setup page's files could not be read")
            if "/index.html" in files:
                files["/"] = files["/index.html"]
            self._files = files
        return self._files

    def _home(self) -> str:
        return f"http://127.0.0.1:{self.port}/" if self.dry else f"http://{ident.SETUP_ADDR}/"

    async def h_page(self, q):
        """Every GET that is not one of the API calls: the page's own files, and everything
        else (a computer asking "am I online?", a person typing any address) is sent to the
        page. That redirect is what makes the page open by itself."""
        self._guard(q)
        host = (q.headers.get("host") or "").rsplit(":", 1)[0].strip("[]").lower()
        ours = host in ("", ident.SETUP_ADDR) or (self.dry and host in ("127.0.0.1", "localhost"))
        if ours:
            f = self._static().get(q.path)
            if f:
                return Raw(200, f[1], f[0])
            if q.path.startswith("/api/") or q.path.startswith("/assets/"):
                raise HttpError(404, "not_found")
        elif host not in self._askers and len(self._askers) < 64:
            self._askers.add(host)
            log.info("a device asked for %s%s: sent to the setup page", ident.clean(host, 60), ident.clean(q.path, 40))
        home = self._home()
        return Raw(302, f'<!doctype html><title>ANVE Box</title><a href="{home}">Set up your ANVE Box</a>'.encode(),
                   "text/html; charset=utf-8", location=home)

    async def h_status(self, q):
        """What the setup page shows, in the shape of the ANVE Box team's page
        (anve-box: device/anvebox_agent/portal_app.py), plus a "portal" part of our own.
        Nothing secret: `claimLink` is always null here, the link travels sealed."""
        self._guard(q)
        d, box = ident.load(), self._cloud_box()
        lights = {}
        try:
            from anvebox_agent.lights import as_json
            lights = {"online": as_json("solid" if box and box["claimed"] else "double_blink"),
                      "blocked": as_json("triple_blink"), "setup": as_json("setup_pulse")}
        except Exception:                                    # noqa: BLE001
            pass
        return {"box": box["id"] if box else self.id.upper(), "setupWifi": self.ssid,
                "mode": "SETUP" if self.mode in ("setup", "recovery", "starting") else "CONNECTING",
                "error": {"code": PAGE_REASONS.get(self.error, self.error), "network": self.error_net} if self.error else None,
                "claimLink": None, "lights": lights,
                "portal": {"sealed": True, "pub": b64(self.box.public), "name": d["name"], "room": d["room"],
                           "open": self.mode == "setup", "recovery": self.mode == "recovery",
                           "trial": self.trial, "busy": self.busy, "held": self._held is not None,
                           "voice": (etc_dir() / "key").exists(), "cloud": box["cloud"] if box else "",
                           "claimable": bool(box and box["link"]), "owned": bool(box and box["claimed"]),
                           "again": self.again, "left": self._left(),
                           "version": ident.VERSION}}

    def _left(self):
        """Seconds until a hotspot test or a set-up-again window closes (None: no window)."""
        return None if self.window_end is None else max(0, int(self.window_end - time.monotonic()))

    async def h_alive(self, q):
        """The open setup page asks this every few seconds, so it can say "the box has left"
        instead of sitting there (19 Nov 2026: a test closed under an open page). It is
        deliberately not _guard(): it must not count as someone using the hotspot (the
        recovery timer reads last_request). It does keep a hotspot test from closing
        early while a page is still open (run_trial)."""
        if not self.dry and q.local != ident.SETUP_ADDR:
            raise HttpError(403, "setup_hotspot_only")
        self.alive_at = time.monotonic()
        return {"ok": True, "trial": self.trial, "again": self.again, "busy": self.busy, "left": self._left()}

    async def h_unsealed(self, q):
        self._guard(q)
        log.info("an unsealed Wi-Fi password was refused (this hotspot is an open network)")
        raise HttpError(400, "sealed_only")

    async def h_portal(self, q):
        """The setup page sends what the app sends, sealed the same way; the device key is
        optional here (without it the box is online and in the ANVE Box cloud, with no voice
        assistant yet)."""
        p, key = self._sealed(q, "setup")
        try:
            w = _check(p, need_key=False)
        except HttpError as e:
            log.info("the setup page's details were refused: %s", e.body.get("field") or e.body.get("error"))
            raise
        if self.trial:
            self.trial_got = True
            log.info("hotspot test: the setup page sent complete, valid details (Wi-Fi \"%s\"); nothing was changed", w["ssid"])
            return Box.reply(key, {"ok": True, "trial": True, "network": w["ssid"]})
        box = self._cloud_box()
        link = box["link"] if box else None
        ticket, wait = os.urandom(16).hex(), hold_s() if link else 0
        self.busy, self.error = True, ""
        self._held = {"ticket": ticket, "go": asyncio.Event()}
        log.info("the setup page sent Wi-Fi \"%s\" (%s password, %s voice key)%s", w["ssid"],
                 "with a" if w["password"] else "no", "with a" if w["key"] else "no",
                 f"; waiting up to {wait} s for the person to keep their link" if wait else "")
        asyncio.create_task(self._hold_then_apply(w, wait))
        return Box.reply(key, {"ok": True, "id": self.id, "box": box["id"] if box else self.id.upper(),
                               "network": w["ssid"], "claimLink": link, "cloud": box["cloud"] if box else "",
                               "ticket": ticket, "hold": wait,
                               "voice": bool(w["key"]) or (etc_dir() / "key").exists(),
                               "host": f"anve-{self.id}.local"})

    async def h_go(self, q):
        """"I have the link: join the Wi-Fi now." The ticket came in the sealed answer."""
        self._guard(q)
        held, t = self._held, str(q.json().get("ticket") or "")
        if not held or not hmac.compare_digest(t.encode(), held["ticket"].encode()):
            raise HttpError(409, "nothing_waiting")
        held["go"].set()
        return {"ok": True}

    async def _hold_then_apply(self, w: dict, seconds: int):
        held = self._held
        try:
            if seconds and held:
                try:
                    await asyncio.wait_for(held["go"].wait(), seconds)
                except asyncio.TimeoutError:
                    log.info("nobody pressed \"connect now\" for %d s: joining the Wi-Fi anyway", seconds)
        finally:
            self._held = None
        await self._apply(w, full=True)

    async def _apply(self, w: dict, *, full: bool):
        back_to = self.mode
        try:
            await asyncio.sleep(0.3 if self.dry else 2.5)    # let the answer reach the phone
            self.mode = "joining"
            log.info("joining Wi-Fi \"%s\"", w["ssid"])
            await self._close()
            ok, why = await self.net.join(w["ssid"], w["password"], w["hidden"], w["sec"])
            if not ok:
                log.warning("could not join \"%s\": %s", w["ssid"], why)
                await self.net.forget()
                self.error, self.error_net = why, w["ssid"]
                await self._open(back_to)
                return
            self._store(w, full=full)
            log.info("joined \"%s\"%s", w["ssid"], "; the box is set up" if full else "")
            if w["portal"]:
                await self.net.unit("start", "anve-wifi-login.service")
            await self.net.unit("restart", "anve-device.service")
            self.mode, self.offline_since = "normal", None
        except Exception:                                    # noqa: BLE001
            log.exception("setup failed")
            self.error = "internal"
            try:
                await self._open(back_to)
            except Exception:                                # noqa: BLE001
                log.exception("could not reopen the hotspot")
        finally:
            self.busy = False

    # ---------- factory reset (asked for by the speaker, for the app) ----------

    async def reset(self):
        await asyncio.sleep(0.3 if self.dry else 2.0)        # let the app get its answer
        log.warning("factory reset: forgetting the key, the Wi-Fi and the paired phones")
        self.busy = True
        try:
            await self.net.unit("stop", "anve-device.service")
            etc = etc_dir()
            for name in ("key", "key.before-resetup", "wifi-login", "app-token", WIFI_KEPT):
                (etc / name).unlink(missing_ok=True)
            self.token()                                     # a fresh one: old phones are out
            (ident.state_dir() / "provisioned").unlink(missing_ok=True)
            (ident.state_dir() / "device.json").unlink(missing_ok=True)
            # the ANVE Box cloud must hear of the reset too, or the old owner keeps the box
            # there: the link (boxlink.py) finds this note, drops the owner and makes a new
            # claim secret, and tells the cloud in its next hello
            try:
                note = Path("/var/lib/anvebox") if not self.dry else ident.state_dir()
                if note.is_dir():
                    (note / "reset-requested").write_text(time.strftime("%Y-%m-%d %H:%M:%S") + "\n")
            except OSError:
                log.warning("could not leave the reset note for the box link")
            self.box, self.error = Box(), ""
            # From here on the box is "not set up": if the hotspot does not come up at the
            # first try, the loop in run() keeps trying every 20 s. (Before, the mode stayed
            # "normal" and a hotspot that failed once was never tried again: on the real
            # box, 19 Nov, that left it with no Wi-Fi and no hotspot until a power cycle.)
            self.mode = "starting"
            # The hotspot first, the old Wi-Fi's profile after: deleting the profile in use
            # first leaves the radio idle for a moment, NetworkManager starts joining some
            # other saved network, and the hotspot then has to fight it.
            if not await self._open("setup"):
                log.error("factory reset: the hotspot did not come up; trying again shortly")
            await self.net.forget()
        finally:
            self.busy = False

    async def run_again(self, seconds: int):
        """Set up again, for real, on a box that is set up: the hotspot for up to `seconds`.
        What a phone sends is applied (see _apply). If nothing has been applied when the
        time is up, the box goes back to the Wi-Fi it was on, with the key it had."""
        await asyncio.sleep(0.3 if self.dry else 3.0)
        before = await self.net.connected()
        self.again, self.error, self._peers = True, "", set()
        log.warning("set up again, for up to %d s: leaving Wi-Fi \"%s\"", seconds, before)
        applied = False
        try:
            if not await self._open("setup"):
                log.error("set up again: the hotspot did not come up")
            else:
                end = self.window_end = time.monotonic() + seconds
                while time.monotonic() < end or self.busy:
                    await asyncio.sleep(0.2 if self.dry else 1)
                    if self.mode == "normal" and not self.busy:      # _apply finished: joined and stored
                        applied = True
                        break
        except Exception:                                    # noqa: BLE001
            log.exception("set up again failed")
        finally:
            self.window_end = None
            if applied:
                self.net.drop_kept()
                log.warning("set up again: done, the box uses the new settings")
            else:
                self.mode = "normal"
                try:
                    await self._close()
                    await self.net.forget()
                except Exception:                            # noqa: BLE001
                    log.exception("could not close the hotspot")
                try:
                    if await self.net.put_back():
                        log.info("set up again: the Wi-Fi profile the box was on is back in place")
                except Exception:                            # noqa: BLE001
                    log.exception("could not put the Wi-Fi profile back")
                ok = await self.net.restore(before)
                log.warning("set up again: nothing was applied (%s); back on Wi-Fi \"%s\": %s",
                            self.error or f"{len(self._peers)} phone(s) reached the box", before, "yes" if ok else "NO")
            self.again, self.offline_since = False, None

    async def run_trial(self, seconds: int):
        """A hotspot test on a box that is set up: the hotspot for `seconds`, then back to
        the Wi-Fi it was on. The key, the saved Wi-Fi and the "set up" mark are not touched."""
        await asyncio.sleep(0.3 if self.dry else 3.0)        # let whoever asked get the answer
        before = await self.net.connected()
        self.trial, self.trial_got, self.error, self._peers = True, False, "", set()
        log.warning("hotspot test for %d s: leaving Wi-Fi \"%s\"; key and settings are not touched",
                    seconds, before)
        try:
            if not await self._open("setup"):
                log.error("hotspot test: the hotspot did not come up")
            else:
                end = self.window_end = time.monotonic() + seconds
                while time.monotonic() < end:
                    await asyncio.sleep(0.2 if self.dry else 1)
                    # a phone went through the whole flow: stop early, once it has gone quiet.
                    # A setup page that is still open (it asks /api/alive) is not "quiet":
                    # closing under it left the person looking at a dead page.
                    now = time.monotonic()
                    if (self.trial_got and now - self.last_request > (1 if self.dry else 30)
                            and now - self.alive_at > (1 if self.dry else 15)):
                        break
        except Exception:                                    # noqa: BLE001
            log.exception("hotspot test failed")
        finally:
            self.window_end = None
            self.mode = "normal"
            try:
                await self._close()
            except Exception:                                # noqa: BLE001
                log.exception("could not close the hotspot")
            ok = await self.net.restore(before)
            log.warning("hotspot test over: %s; back on Wi-Fi \"%s\": %s",
                        "a phone completed the whole flow" if self.trial_got else f"{len(self._peers)} phone(s) reached the box",
                        before, "yes" if ok else "NO")
            self.trial, self.offline_since = False, None

    async def _helper(self, r: asyncio.StreamReader, w: asyncio.StreamWriter):
        try:
            sock = w.get_extra_info("socket")
            _, uid, _ = struct.unpack("3i", sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED,
                                                            struct.calcsize("3i")))
            allowed = {0, int(os.environ.get("ANVE_USER_UID", "1000"))} | ({os.getuid()} if self.dry else set())
            msg = json.loads(await asyncio.wait_for(r.readline(), 5) or b"{}")
            if uid not in allowed:
                out = {"ok": False, "error": "forbidden"}
            elif msg.get("cmd") == "reset":
                if self.busy:
                    out = {"ok": False, "error": "busy"}
                else:
                    asyncio.create_task(self.reset())
                    out = {"ok": True}
            elif msg.get("cmd") in ("trial", "again"):
                if self.busy or self.trial or self.again or self.mode != "normal":
                    out = {"ok": False, "error": "busy" if (self.busy or self.trial or self.again) else "not_set_up"}
                elif (msg["cmd"] == "again" and await self.net.connected() == WIFI_CON
                      and not self.net.keep()):
                    # without a copy of the profile the box is on there is no way back
                    out = {"ok": False, "error": "wifi_not_kept"}
                else:
                    try:
                        secs = max(2 if self.dry else 30, min(900, int(msg.get("seconds") or 300)))
                    except (TypeError, ValueError):
                        secs = 300
                    asyncio.create_task(self.run_trial(secs) if msg["cmd"] == "trial" else self.run_again(secs))
                    out = {"ok": True, "seconds": secs, "ssid": self.ssid, "applies": msg["cmd"] == "again"}
            else:
                out = {"ok": True, "mode": self.mode, "error": self.error, "ssid": self.ssid, "trial": self.trial,
                       "wifi": await self.net.connected(), "wifi_kept": self.net.kept()}
            w.write(json.dumps(out).encode() + b"\n")
            await w.drain()
        except (OSError, ValueError, asyncio.TimeoutError, struct.error):
            pass
        finally:
            w.close()

    # ---------- run ----------

    async def _recovery_tick(self):
        now = time.monotonic()
        if self.mode == "normal":
            if await self.net.connected():
                self.offline_since = None
            elif self.offline_since is None:
                self.offline_since = now
            elif now - self.offline_since >= 180:
                log.warning("no Wi-Fi for 3 minutes: opening the hotspot so a paired phone "
                            "can give this box a new Wi-Fi")
                await self._open("recovery")
        elif self.mode == "recovery" and not self.busy and now - self.last_request >= 240:
            log.info("nobody used the hotspot: trying the saved Wi-Fi again")
            await self._close()
            self.mode = "normal"
            self.offline_since = now - 105                   # look again in about 75 s

    async def run(self, sock_path: str = SOCK):
        try:
            os.unlink(sock_path)
        except OSError:
            pass
        if self.dry and not hasattr(asyncio, "start_unix_server"):
            srv = None                                       # --dry-run on Windows, to look at the setup page
        else:
            srv = await asyncio.start_unix_server(self._helper, path=sock_path)
            os.chmod(sock_path, 0o666)                       # the peer's user id is checked instead
        self.token()
        for _ in range(30):                                  # the Wi-Fi chip may not be up yet
            if await self.net.ready():
                break
            await asyncio.sleep(2)
        if ident.provisioned():
            self.mode = "normal"
            if await self.net.ap_active():                   # left over from a crash
                await self.net.ap_down()
            self.net.captive(False)                          # and so is this, if it was
            if self.net.kept():                              # a "set up again" was cut short (power, crash)
                ok = await self.net.put_back()
                log.warning("a set-up-again did not finish: the Wi-Fi profile the box was on is %s",
                            "back in place" if ok else "NOT back in place")
            log.info("this box is set up (id %s); waiting", self.id)
        else:
            log.info("this box is not set up yet (id %s)", self.id)
            await self._open("setup")
        try:
            while True:
                await asyncio.sleep(1 if self.dry else 20)
                if self.busy or self.trial or self.again:
                    continue
                if self.mode in ("starting", "setup") and not await self.net.ap_active():
                    await self._open("setup")                # it never came up, or it dropped
                elif self.recovery and self.mode in ("normal", "recovery"):
                    await self._recovery_tick()
        finally:
            if srv is not None:
                srv.close()


def main():
    ap = argparse.ArgumentParser(description="ANVE box: first-time setup over its own hotspot")
    ap.add_argument("--dry-run", action="store_true", help="change no network; for the tests")
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--sock", default=None)
    a = ap.parse_args()
    logging.basicConfig(level=os.environ.get("ANVE_LOG_LEVEL", "INFO").upper(),
                        format="%(levelname)s %(name)s: %(message)s")
    if a.dry_run:
        p = Provision(FakeNet(), dry=True, host=a.host or "127.0.0.1", port=a.port or 18080)
        sock = a.sock or str(ident.state_dir() / "provision.sock")
    else:
        if os.geteuid() != 0:
            raise SystemExit("this must run as root (it changes the network)")
        p = Provision(Net(), host=a.host or "0.0.0.0", port=a.port or ident.SETUP_PORT,
                      recovery=os.environ.get("ANVE_RECOVERY") == "1")
        sock = a.sock or SOCK
    asyncio.run(p.run(sock))


if __name__ == "__main__":
    main()
