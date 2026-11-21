#!/usr/bin/env python3
"""The phone app's API on the RUNNING speaker, tested the way the app uses it.

    sudo python3 tools/appapi_test.py [--host 127.0.0.1]

sudo, because the app token is a root-only file; it is read here and never printed.
Nothing lasting is changed: the volume is set to what it already is, the name is set
and put back. It never sends a factory reset."""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from anve_device import ident, pairing  # noqa: E402

results = []


def check(name, ok, note=""):
    results.append(bool(ok))
    print(f"  {'ok  ' if ok else 'FAIL'} {name}{' - ' + str(note) if note and not ok else ''}")


class App:
    def __init__(self, base, token_hex):
        self.base, self.chan = base, pairing.Channel(token_hex)

    def raw(self, method, target, body=None, headers=None, timeout=40):
        req = urllib.request.Request(self.base + target, method=method, data=body, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)

    def call(self, method, target, obj=None, *, ts=None, nonce=None):
        nonce = nonce or os.urandom(9).hex()
        body = self.chan.seal_request(obj, nonce) if obj is not None else b""
        auth = pairing.sign(self.chan.token, method, target, body, ts=ts, nonce=nonce)
        s, r = self.raw(method, target, body or None, {"X-ANVE-Auth": auth})
        if s == 200:
            r = self.chan.open_reply(r, nonce)
        return s, r, auth


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--token-file", default="/etc/anve-device/app-token")
    a = ap.parse_args()
    try:
        token = Path(a.token_file).read_text().strip()
    except OSError as e:
        print(f"cannot read the app token ({e}); run with sudo")
        return 2
    app = App(f"http://{a.host}:{ident.PORT}", token)

    s, hello = app.raw("GET", "/api/hello")
    check("hello is open and says who the box is",
          s == 200 and hello.get("setup") is False and len(hello.get("id", "")) == 4 and hello.get("paired"), hello)
    check("hello gives away no token", "token" not in json.dumps(hello))

    s, r = app.raw("GET", "/api/state")
    check("state without a signature is refused", s == 401, (s, r))
    s, r = app.raw("GET", "/api/state", headers={"X-ANVE-Auth": pairing.sign(os.urandom(32), "GET", "/api/state")})
    check("a signature made with another token is refused", s == 401)
    s, r, auth = app.call("GET", "/api/state")
    st = r.get("state", {}) if s == 200 else {}
    check("signed state request is answered, and the answer is encrypted",
          s == 200 and "volume" in st and "state" in st, (s, r))
    print(f"       state: {st.get('state')}, online {st.get('online')}, volume {st.get('volume')} %, "
          f"playing {('yes' if st.get('playing') else 'nothing')}, speaker {st.get('speaker')}, mic {st.get('mic')}")
    s, r = app.raw("GET", "/api/state", headers={"X-ANVE-Auth": auth})
    check("the same signed request sent again (replay) is refused", s == 401)
    s, r, _ = app.call("GET", "/api/state", ts=int(time.time()) - 600)
    check("a request with a 10-minute-old time is refused, and the box tells its time",
          s == 401 and abs(r.get("now", 0) - time.time()) < 5)
    s, r, _ = app.call("GET", "/api/state?x=1")
    s2, r2 = app.raw("GET", "/api/state?x=2", headers={"X-ANVE-Auth": pairing.sign(app.chan.token, "GET", "/api/state?x=1")})
    check("a signature is bound to its exact address", s == 200 and s2 == 401)

    t0 = time.time()
    s, r, _ = app.call("GET", "/api/events?since=0&wait=5")
    seq = r.get("seq", 0)
    check("events: answers at once when there is news", s == 200 and time.time() - t0 < 2 and seq > 0, r)
    t0 = time.time()
    s, r, _ = app.call("GET", f"/api/events?since={seq}&wait=3")
    check("events: waits when there is none (long poll)", s == 200 and 0 <= time.time() - t0 <= 6, r)
    s, r, _ = app.call("GET", f"/api/events?since={seq + 1000}&wait=3")
    check("events: a stale counter gets 'reset'", s == 200 and r.get("reset") is True)

    vol = st.get("volume", 40)
    s, r, _ = app.call("POST", "/api/volume", {"level": vol})
    check(f"volume: set to its current value ({vol} %)", s == 200 and r.get("volume") == vol, r)
    s, r, _ = app.call("POST", "/api/volume", {"level": "loud"})
    check("volume: nonsense is refused", s == 400)
    body = json.dumps({"level": 100}).encode()
    s, r = app.raw("POST", "/api/volume", body, {"X-ANVE-Auth": pairing.sign(app.chan.token, "POST", "/api/volume", body)})
    check("volume: a signed but unencrypted body is refused", s == 400, (s, r))
    s, r, _ = app.call("GET", "/api/state")
    check("volume unchanged after the refused requests", r.get("state", {}).get("volume") == vol)

    s, r, _ = app.call("POST", "/api/media", {"do": "dance"})
    check("media: unknown action refused", s == 400)
    if not st.get("playing"):
        s, r, _ = app.call("POST", "/api/media", {"do": "resume"})
        check("media: 'resume' with nothing paused says so and starts nothing",
              s == 200 and r.get("ok") is False and r.get("playing") is None, r)

    name, room = st.get("name"), st.get("room")
    s, r, _ = app.call("POST", "/api/name", {"name": "  Test   box\n", "room": "Lab"})
    check("name: saved, tidied", s == 200 and r.get("name") == "Test box" and r.get("room") == "Lab", r)
    s, r, _ = app.call("POST", "/api/name", {"name": name, "room": room})
    check("name: put back", s == 200 and r.get("name") == name)

    s, r, _ = app.call("GET", "/api/info")
    check("info: Wi-Fi, address, temperature", s == 200 and r.get("address") and r.get("temperature"), r)
    print(f"       info: Wi-Fi '{r.get('wifi')}' signal {r.get('signal')}, {r.get('temperature')} C, "
          f"power ok {r.get('power_ok')}, up {r.get('uptime_s')} s")

    s, r, _ = app.call("POST", "/api/reset", {})
    check("reset without the confirmation word is refused (and no reset was sent)", s == 400)

    # pairing a second phone
    pub = pairing.unb64(hello["pub"], 32)
    user = os.environ.get("SUDO_USER") or "root"
    tool = str(Path(__file__).resolve().parent / "pair_app.py")
    as_user = (["sudo", "-u", user] if os.geteuid() == 0 and user != "root" else []) + [sys.executable, tool]
    subprocess.run(as_user + ["--close"], capture_output=True)
    msg, key = pairing.seal(pub, {"code": "000000"})
    s, r = app.raw("POST", "/api/pair", json.dumps(msg).encode())
    check("pairing is closed unless a code was asked for", s == 403 and r.get("error") == "pairing_closed", r)
    out = subprocess.run(as_user, capture_output=True, text=True).stdout
    code = "".join(ch for ch in out.split(":")[1].split("(")[0] if ch.isdigit()) if ":" in out else ""
    check("pair_app.py opens a pairing window", len(code) == 6, out)
    wrong = "000000" if code != "000000" else "111111"
    msg, key = pairing.seal(pub, {"code": wrong})
    s, r = app.raw("POST", "/api/pair", json.dumps(msg).encode())
    check("pairing: a wrong code is refused", s == 403 and r.get("error") == "wrong_code", r)
    msg, key = pairing.seal(pub, {"code": code, "phone": "test phone"})
    s, r = app.raw("POST", "/api/pair", json.dumps(msg).encode())
    rep = pairing.open_reply(key, r) if s == 200 else {}
    check("pairing: the right code returns the token, sealed", rep.get("token") == token and "token" not in r, (s, r))
    msg, key = pairing.seal(pub, {"code": code})
    s, r = app.raw("POST", "/api/pair", json.dumps(msg).encode())
    check("pairing: the code still works inside its 5 minutes (a lost answer can be asked for again)",
          s == 200 and pairing.open_reply(key, r).get("token") == token)
    subprocess.run(as_user + ["--close"], capture_output=True)
    msg, key = pairing.seal(pub, {"code": code})
    s, r = app.raw("POST", "/api/pair", json.dumps(msg).encode())
    check("pairing: closed with --close, the code is refused", s == 403, (s, r))

    print(f"\n{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
