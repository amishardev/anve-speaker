"""The speaker's API for the phone app, on the local network (port 8738).

    GET  /api/hello               open: who this box is, and its public key
    POST /api/pair                open, sealed: a phone joins with the 6-digit code from
                                  tools/pair_app.py (for the 5 minutes that code is valid)
    everything below is signed and encrypted with the token the phone got at setup or
    pairing (pairing.py: Channel)
    GET  /api/state               what the speaker is doing, what is playing, the volume
    GET  /api/events?since=N&wait=25    waits until something changes (long poll)
    POST /api/volume  {level}     0..100
    POST /api/media   {do}        pause | resume | next
    POST /api/play    {query} or {radio}
    POST /api/mute    {on}
    POST /api/stop                stop talking / stop an alarm
    POST /api/name    {name, room}
    GET  /api/info                Wi-Fi, address, temperature, version
    POST /api/reset               factory reset: back to first-time setup

Approvals are NOT answered here. A room speaker must never approve anything by itself, so
this API only says "something is waiting" (no read-back text: that stays on ANVE's own
HTTPS). The app shows the notification and the person approves in the app with their own
ANVE sign-in."""
import asyncio
import json
import logging
import os
import re
import subprocess
import time
from collections import deque
from pathlib import Path

from . import ident
from .httpd import HttpError, Server
from .pairing import BadMessage, Box, Channel, b64
from .playback import tone

log = logging.getLogger("anve.app")
HELPER_SOCK = "/run/anve-provision.sock"


def pair_file() -> Path:
    return Path(os.environ.get("RUNTIME_DIRECTORY", "/run/anve").split(":")[0]) / "pair-code"


class AppApi:
    def __init__(self, dev, cfg: dict):
        self.dev, self.cfg = dev, cfg
        self.http = Server()
        self.box = Box()
        self.ident = ident.load()
        self.chan = None
        try:
            self.chan = Channel(Path(cfg["app"]["token_file"]).read_text())
        except (OSError, ValueError):
            log.warning("no app token: the phone app cannot connect (run tools/install_service.sh)")
        self.events: deque = deque(maxlen=200)
        self.seq = 0
        self.cond = asyncio.Condition()
        self.waiting: dict = {}                       # approvals waiting: id -> expires_at
        self._last_key = None
        self._fails: dict = {}                        # peer -> [count, window start]
        self._pair_fails = 0
        r, a = self.http.route, self._auth
        r("GET", "/api/hello", self.h_hello)
        r("POST", "/api/pair", self.h_pair)
        r("GET", "/api/state", a(self.h_state))
        r("GET", "/api/events", a(self.h_events))
        r("POST", "/api/volume", a(self.h_volume))
        r("POST", "/api/media", a(self.h_media))
        r("POST", "/api/play", a(self.h_play))
        r("POST", "/api/mute", a(self.h_mute))
        r("POST", "/api/stop", a(self.h_stop))
        r("POST", "/api/name", a(self.h_name))
        r("GET", "/api/info", a(self.h_info))
        r("POST", "/api/reset", a(self.h_reset))

    # ---------- what the speaker tells the app ----------

    def snapshot(self) -> dict:
        d = self.dev
        now = time.time()
        for k in [k for k, exp in self.waiting.items() if exp and exp < now]:
            del self.waiting[k]
        return {**self.ident, "version": ident.VERSION,
                "online": bool(d.link), "halted": bool(d.client.halted),
                "state": getattr(d, "_look", "idle"),
                "muted": bool(d.muted), "recording": bool(d.recording),
                "volume": int(round(d.cfg["audio"]["speaker_volume"] * 100)),
                "speaker": bool(d.speaker_ok), "mic": bool(d.mic_ok),
                "playing": d.media.now() if d.media else None,
                "approvals": len(self.waiting)}

    async def emit(self, kind: str, **data):
        self.seq += 1
        self.events.append({"seq": self.seq, "t": int(time.time()), "kind": kind, **data})
        async with self.cond:
            self.cond.notify_all()

    def on_server(self, m: dict):
        """Called for every message from ANVE: pass on what a phone should know about."""
        t = m.get("type")
        ev = None
        if t == "confirm.request" and not m.get("voice_ok"):
            exp = m.get("expires_at")
            self.waiting[str(m.get("id"))] = float(exp) if isinstance(exp, (int, float)) else time.time() + 300
            ev = ("approval", {"id": str(m.get("id")), "action": str(m.get("action") or "")[:60],
                               "expires_at": exp})
        elif t == "confirm.result":
            self.waiting.pop(str(m.get("id")), None)
            ev = ("approval_done", {"id": str(m.get("id")), "status": str(m.get("status") or "")})
        elif t == "notice":
            ev = ("notice", {"level": str(m.get("level") or ""), "text": str(m.get("text") or "")[:240]})
        elif t == "notices":
            n = len(m.get("items") or [])
            if n:
                ev = ("waiting", {"count": n})
        elif t == "job":
            ev = ("job", {"job": str(m.get("kind") or ""), "status": str(m.get("status") or "")})
        if ev:
            asyncio.create_task(self.emit(ev[0], **ev[1]))

    async def run(self):
        a = self.cfg["app"]
        while True:
            try:
                await self.http.start(a["host"], a["port"])
                break
            except OSError as e:
                log.warning("the app API cannot listen on port %s yet (%s)", a["port"], e)
                await asyncio.sleep(10)
        log.info("app API on port %s (box %s, %s)", a["port"], self.ident["id"],
                 "paired channel ready" if self.chan else "no token")
        while True:
            await asyncio.sleep(1)
            s = self.snapshot()
            p = s["playing"] or {}
            key = json.dumps({**s, "playing": {k: v for k, v in p.items() if k != "position"}},
                             sort_keys=True)
            if key != self._last_key:
                self._last_key = key
                await self.emit("state")

    # ---------- open routes ----------

    def _pair_code(self) -> str:
        try:
            d = json.loads(pair_file().read_text())
            if float(d["until"]) > time.time() and re.fullmatch(r"\d{6}", str(d["code"])):
                return str(d["code"])
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return ""

    async def h_hello(self, q):
        return {"ok": True, **self.ident, "version": ident.VERSION, "setup": False,
                "paired": self.chan is not None, "pairing": bool(self._pair_code()),
                "pub": b64(self.box.public), "port": self.cfg["app"]["port"]}

    async def h_pair(self, q):
        code = self._pair_code()
        if not code or not self.chan:
            raise HttpError(403, "pairing_closed")
        try:
            p, key = self.box.open(q.json())
        except BadMessage:
            raise HttpError(400, "bad_message") from None
        if str(p.get("code") or "") != code:
            self._pair_fails += 1
            if self._pair_fails >= 5:                 # five wrong codes close the window
                pair_file().unlink(missing_ok=True)
                self._pair_fails = 0
            await asyncio.sleep(1.0)
            raise HttpError(403, "wrong_code")
        self._pair_fails = 0
        # The code stays valid until its 5 minutes are over (or `pair_app.py --close`):
        # if this answer is lost on the way, the phone can ask again with the same code.
        log.info("a phone was paired (%s)", ident.clean(p.get("phone"), 30) or "unnamed")
        return Box.reply(key, {"ok": True, "token": self.chan.token.hex(), **self.ident,
                               "port": self.cfg["app"]["port"]})

    # ---------- the paired channel ----------

    def _auth(self, handler):
        async def h(q):
            if not self.chan:
                raise HttpError(503, "not_paired")
            f = self._fails.get(q.peer)
            if f and f[0] >= 20 and time.monotonic() - f[1] < 60:
                raise HttpError(429, "slow_down")
            nonce = self.chan.check(q.headers.get("x-anve-auth", ""), q.method, q.target, q.body)
            if not nonce:
                if not f or time.monotonic() - f[1] >= 60:
                    f = self._fails[q.peer] = [0, time.monotonic()]
                f[0] += 1
                if len(self._fails) > 500:
                    self._fails.clear()
                # `now` lets a phone with a wrong clock correct its timestamps
                raise HttpError(401, "auth", now=int(time.time()))
            try:
                args = self.chan.open(q.body, nonce)
            except BadMessage:
                raise HttpError(400, "bad_body") from None
            return self.chan.seal(await handler(args, q), nonce)
        return h

    async def h_state(self, a, q):
        return {"ok": True, "seq": self.seq, "state": self.snapshot()}

    async def h_events(self, a, q):
        try:
            since = int(q.query.get("since", "0"))
            wait = max(0.0, min(30.0, float(q.query.get("wait", "25"))))
        except ValueError:
            raise HttpError(400, "bad_query") from None
        if since > self.seq:                          # the speaker restarted: start over
            return {"ok": True, "seq": self.seq, "reset": True, "events": [],
                    "state": self.snapshot()}
        if since == self.seq and wait:
            try:
                async with self.cond:
                    await asyncio.wait_for(self.cond.wait_for(lambda: self.seq > since), wait)
            except asyncio.TimeoutError:
                pass
        return {"ok": True, "seq": self.seq, "state": self.snapshot(),
                "events": [e for e in self.events if e["seq"] > since and e["kind"] != "state"]}

    async def h_volume(self, a, q):
        try:
            level = int(a.get("level"))
        except (TypeError, ValueError):
            raise HttpError(400, "bad_level") from None
        level = max(0, min(100, level))
        d = self.dev
        d.cfg["audio"]["speaker_volume"] = level / 100
        await asyncio.to_thread(d._set_volume)
        await asyncio.to_thread(d._remember_volume, level / 100)
        if level and not d._media_on() and not d.playback.active:
            d.playback.play(tone((880.0,), ring=0.14, gain=0.14))     # heard at the new volume
        return {"ok": True, "volume": level}

    async def h_media(self, a, q):
        d, do = self.dev, str(a.get("do") or "")
        if do == "stop":
            do = "pause"
        if not d.media or do not in ("pause", "resume", "next"):
            raise HttpError(400, "bad_action")
        say = await d.media.run({"do": do})
        if not d.media.ducked:
            await d.media.refresh()
        return {"ok": not say, "note": say, "playing": d.media.now()}

    async def h_play(self, a, q):
        d = self.dev
        if not d.media:
            raise HttpError(409, "no_media")
        if a.get("radio"):
            intent = {"do": "radio", "station": ident.clean(a.get("radio"), 30).lower()}
        else:
            query = ident.clean(a.get("query"), 120)
            if not query:
                raise HttpError(400, "bad_query")
            intent = {"do": "youtube", "query": query}

        async def go():
            try:
                await d.media.run(intent)
            except Exception:                                # noqa: BLE001
                log.exception("play from the app failed")
        asyncio.create_task(go())
        return {"ok": True}

    async def h_mute(self, a, q):
        d = self.dev
        if bool(a.get("on")) != d.muted:
            await d.press_mute()
        return {"ok": True, "muted": d.muted}

    async def h_stop(self, a, q):
        d = self.dev
        speaking = time.monotonic() < d.reply_until
        d.alarm_until = d.reply_until = 0.0
        d.playback.flush()
        if speaking:
            await d.client.send({"type": "interrupt"})
        return {"ok": True}

    async def h_name(self, a, q):
        try:
            await asyncio.to_thread(ident.save, a.get("name"), a.get("room"))
        except OSError:
            raise HttpError(500, "not_saved") from None
        self.ident = ident.load()
        return {"ok": True, **self.ident}

    @staticmethod
    def _info() -> dict:
        def run(*argv):
            try:
                return subprocess.run(argv, capture_output=True, text=True, timeout=5).stdout
            except (OSError, subprocess.SubprocessError):
                return ""
        out = {"version": ident.VERSION, "wifi": "", "address": "", "signal": None,
               "temperature": None, "power_ok": None, "uptime_s": None}
        for line in run("nmcli", "-t", "-f", "GENERAL.CONNECTION,IP4.ADDRESS", "dev", "show", "wlan0").splitlines():
            k, _, v = line.partition(":")
            if k == "GENERAL.CONNECTION":
                out["wifi"] = v
            elif k.startswith("IP4.ADDRESS") and not out["address"]:
                out["address"] = v.split("/")[0]
        # the network's own name, not the name of the saved profile ("anve-wifi")
        for line in run("nmcli", "-t", "-f", "IN-USE,SIGNAL,SSID", "dev", "wifi", "list", "ifname", "wlan0",
                        "--rescan", "no").splitlines():
            if line.startswith("*:"):
                signal, _, ssid = line[2:].partition(":")
                out["signal"] = int(signal) if signal.isdigit() else None
                if ssid:
                    out["wifi"] = ssid.replace("\\:", ":")
        m = re.search(r"([\d.]+)", run("vcgencmd", "measure_temp"))
        out["temperature"] = float(m.group(1)) if m else None
        m = re.search(r"0x([0-9a-fA-F]+)", run("vcgencmd", "get_throttled"))
        out["power_ok"] = (int(m.group(1), 16) & 0x1) == 0 if m else None
        try:
            out["uptime_s"] = int(float(open("/proc/uptime").read().split()[0]))
        except (OSError, ValueError):
            pass
        return out

    async def h_info(self, a, q):
        return {"ok": True, **self.ident, **await asyncio.to_thread(self._info)}

    async def h_reset(self, a, q):
        if a.get("confirm") != "reset":
            raise HttpError(400, "confirm_needed")
        try:
            r, w = await asyncio.wait_for(asyncio.open_unix_connection(HELPER_SOCK), 3)
            w.write(b'{"cmd":"reset"}\n')
            await w.drain()
            ans = json.loads(await asyncio.wait_for(r.readline(), 5) or b"{}")
            w.close()
        except (OSError, ValueError, asyncio.TimeoutError):
            raise HttpError(503, "setup_service_unreachable") from None
        if not ans.get("ok"):
            raise HttpError(503, "refused")
        log.warning("factory reset requested from the app")
        return {"ok": True}
