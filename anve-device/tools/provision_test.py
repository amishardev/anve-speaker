#!/usr/bin/env python3
"""First-time setup, end to end, with NO network change: starts the setup program in
--dry-run (a pretend Wi-Fi) on 127.0.0.1 and plays the phone app against it.

    python3 tools/provision_test.py

It checks the sealed channel, every refusal, a wrong Wi-Fi password, a successful setup
(what lands in which file, with which permissions) and a factory reset."""
import json
import os
import socket
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from anve_device import pairing  # noqa: E402

PORT = 18080
BASE = f"http://127.0.0.1:{PORT}"
results = []


def check(name, ok, note=""):
    results.append(ok)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}{' - ' + str(note) if note and not ok else ''}")


def call(method, path, body=None):
    req = urllib.request.Request(BASE + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def wait(pred, seconds=8):
    t = time.time()
    while time.time() - t < seconds:
        try:
            if pred():
                return True
        except (OSError, ValueError):
            pass
        time.sleep(0.2)
    return False


def main():
    final_checks = []
    tmp = Path(tempfile.mkdtemp(prefix="anve-prov-"))
    state, etc = tmp / "state", tmp / "etc"
    state.mkdir()
    etc.mkdir()
    # the setup page's side: a throwaway identity for the ANVE Box cloud, so the page has a
    # link to hand out, and a short wait for "I have the link"
    box_dir = tmp / "anvebox"
    box_dir.mkdir()
    from anvebox_agent.identity import new_identity, save as save_identity
    identity = new_identity()
    save_identity(identity, box_dir / "identity.json")
    env = dict(os.environ, ANVE_STATE_DIR=str(state), ANVE_ETC_DIR=str(etc), PYTHONUNBUFFERED="1",
               ANVEBOX_IDENTITY=str(box_dir / "identity.json"), ANVEBOX_STATE=str(box_dir),
               ANVE_PORTAL_HOLD_S="2")
    env.pop("ANVE_CAPTIVE", None)
    root = Path(__file__).resolve().parent.parent
    logf = open(tmp / "log", "w")
    proc = subprocess.Popen([sys.executable, "-m", "anve_device.provision", "--dry-run",
                             "--port", str(PORT)], cwd=root, env=env, stdout=logf, stderr=logf)
    try:
        check("the setup program starts and opens its hotspot",
              wait(lambda: call("GET", "/api/hello")[1].get("setup") is True))
        s, hello = call("GET", "/api/hello")
        pub = pairing.unb64(hello["pub"], 32)
        check("hello: id, public key, no key on the box yet",
              len(hello["id"]) == 4 and hello["has_key"] is False and hello["error"] == "")
        s, nets = call("GET", "/api/networks")
        names = [n["ssid"] for n in nets["networks"]]
        check("networks: strongest first, duplicates merged, ':' in a name survives",
              names == ["Home Wi-Fi", "Caf:e", "Open Campus", "Office"], str(names))
        office = next(n for n in nets["networks"] if n["ssid"] == "Office")
        check("an enterprise (802.1X) network is marked as not supported", office["supported"] is False)

        token_before = (etc / "app-token").read_text()
        good = {"wifi": {"ssid": "Home Wi-Fi", "password": "correct horse"}, "name": "Kitchen  speaker",
                "room": "Kitchen", "key": "anve_dev_" + "k" * 40}

        s, r = call("POST", "/api/provision", {"k": hello["pub"], "n": "AAAAAAAAAAAAAAAA", "c": "AAAA"})
        check("a message that is not sealed for this box is refused", s == 400 and r["error"] == "bad_message")
        msg, key = pairing.seal(pub, good)
        tampered = dict(msg, c=msg["c"][:-4] + ("AAAA" if not msg["c"].endswith("AAAA") else "BBBB"))
        s, r = call("POST", "/api/provision", tampered)
        check("a tampered message is refused", s == 400 and r["error"] == "bad_message")
        for field, payload in (
                ("wifi.password", dict(good, wifi={"ssid": "Home Wi-Fi", "password": "short"})),
                ("wifi.ssid", dict(good, wifi={"ssid": "x" * 33, "password": "correct horse"})),
                ("key", {k: v for k, v in good.items() if k != "key"}),
                ("key", dict(good, key="has a space in it......")),
                ("portal", dict(good, portal={"username": "u\nx", "password": "p"}))):
            s, r = call("POST", "/api/provision", pairing.seal(pub, payload)[0])
            check(f"refused: bad {field}", s == 400 and r.get("field") == field, str(r))
        check("nothing was stored by the refused attempts",
              not (etc / "key").exists() and not (state / "provisioned").exists())

        # a wrong Wi-Fi password: the hotspot must come back and say why
        msg, key = pairing.seal(pub, dict(good, wifi={"ssid": "Home Wi-Fi", "password": "wrong-password"}))
        s, r = call("POST", "/api/provision", msg)
        rep = pairing.open_reply(key, r) if s == 200 else {}
        check("the box answers before it leaves the hotspot, with the app token (sealed)",
              rep.get("ok") is True and rep.get("token") == token_before and "token" not in r)
        check("wrong password: the hotspot is back and says so",
              wait(lambda: call("GET", "/api/hello")[1].get("error") == "wrong_password"))
        check("wrong password: nothing stored, still in setup",
              not (etc / "key").exists() and not (state / "provisioned").exists()
              and call("GET", "/api/hello")[1]["setup"] is True)

        page_before_setup(pub, hello, identity, box_dir, etc, state, tmp)

        # the real thing, with a Wi-Fi sign-in page
        msg, key = pairing.seal(pub, dict(good, portal={"username": "student1", "password": "pa ss"}))
        s, r = call("POST", "/api/provision", msg)
        check("setup accepted", s == 200 and pairing.open_reply(key, r).get("host", "").endswith(".local"))
        check("the box is marked as set up", wait(lambda: (state / "provisioned").exists()))
        mode = lambda p: stat.S_IMODE(os.stat(p).st_mode)                       # noqa: E731
        check("the device key is stored, readable by its owner only",
              (etc / "key").read_text() == good["key"] and mode(etc / "key") == 0o600)
        check("the Wi-Fi sign-in is stored the way wifi_login.py reads it, owner only",
              (etc / "wifi-login").read_text() == "student1\npa ss\n" and mode(etc / "wifi-login") == 0o600)
        check("name and room are stored, tidied",
              json.loads((state / "device.json").read_text()) == {"name": "Kitchen speaker", "room": "Kitchen"})
        check("the setup server is closed once the box is set up",
              wait(lambda: _closed()))
        log = (tmp / "log").read_text()
        check("no password, key or token in the log",
              not any(x in log for x in ("correct horse", "wrong-password", good["key"], token_before, "pa ss")))
        final_checks.append(lambda: not any(x in (tmp / "log").read_text() for x in ("wrong-again", "n" * 40)))

        # factory reset, the way the speaker asks for it
        s = socket.socket(socket.AF_UNIX)
        s.connect(str(state / "provision.sock"))
        s.sendall(b'{"cmd":"status"}\n')
        st = json.loads(s.makefile().readline())
        s.close()
        check("status over the local socket", st.get("mode") == "normal")

        # a hotspot test on the set-up box: everything can be tried, nothing changes
        before = {n: (etc / n).read_text() for n in ("key", "wifi-login", "app-token")}
        s = socket.socket(socket.AF_UNIX)
        s.connect(str(state / "provision.sock"))
        s.sendall(b'{"cmd":"trial","seconds":9}\n')
        ans = json.loads(s.makefile().readline())
        s.close()
        check("hotspot test accepted on a set-up box", ans.get("ok") is True, ans)
        check("hotspot test: the hotspot opens and says it is a test",
              wait(lambda: call("GET", "/api/hello")[1].get("trial") is True and call("GET", "/api/hello")[1].get("setup") is True))
        pub2 = pairing.unb64(call("GET", "/api/hello")[1]["pub"], 32)
        check("hotspot test: the Wi-Fi list is served", len(call("GET", "/api/networks")[1]["networks"]) == 4)
        msg, key = pairing.seal(pub2, dict(good, wifi={"ssid": "Other Wi-Fi", "password": "another password"}, key="anve_dev_" + "z" * 40))
        s_, r_ = call("POST", "/api/provision", msg)
        check("hotspot test: valid settings are answered with 'trial', not applied", s_ == 409 and r_.get("error") == "trial", (s_, r_))
        s_, r_ = call("POST", "/api/provision", pairing.seal(pub2, dict(good, wifi={"ssid": "x", "password": "short"}))[0])
        check("hotspot test: invalid settings are still refused field by field", s_ == 400 and r_.get("field") == "wifi.password")
        msg, key = pairing.seal(pub2, {"wifi": {"ssid": "Other Wi-Fi", "password": "another password"}, "name": "T"})
        s_, r_ = call("POST", "/api/portal/connect", msg)
        rep = pairing.open_reply(key, r_) if s_ == 200 else {}
        check("hotspot test: the setup page is told it was a test, and gets no link",
              rep.get("trial") is True and "claimLink" not in rep and call("GET", "/api/status")[1]["portal"]["trial"] is True, (s_, rep))
        # the open page asks "are you there?" every few seconds. A finished test used to
        # close under it (the real box, 19 Nov); now it stays while the page is open.
        s_, a_ = call("GET", "/api/alive")
        check("hotspot test: the open page is told it is a test and how long it has left",
              s_ == 200 and a_.get("trial") is True and a_.get("again") is False and 0 < a_.get("left", 0) <= 9
              and call("GET", "/api/status")[1]["portal"].get("left") is not None, (s_, a_))
        t0, still = time.time(), True
        while time.time() - t0 < 3:                          # (alone, the box would close after 1 s of quiet)
            still = still and call("GET", "/api/alive")[0] == 200
            time.sleep(0.3)
        check("hotspot test: it stays open while a setup page is open", still)
        check("hotspot test: it ends by itself and the setup server closes", wait(lambda: _closed(), 12))
        check("hotspot test: key, Wi-Fi sign-in, token and the set-up mark are untouched",
              all((etc / n).read_text() == v for n, v in before.items()) and (state / "provisioned").exists())
        s = socket.socket(socket.AF_UNIX)
        s.connect(str(state / "provision.sock"))
        s.sendall(b'{"cmd":"status"}\n')
        st = json.loads(s.makefile().readline())
        s.close()
        check("hotspot test: afterwards the box is in normal mode again", st.get("mode") == "normal" and st.get("trial") is False, st)

        # set up again, for real, on the set-up box
        def helper(cmd):
            s = socket.socket(socket.AF_UNIX)
            s.connect(str(state / "provision.sock"))
            s.sendall(json.dumps(cmd).encode() + b"\n")
            a = json.loads(s.makefile().readline())
            s.close()
            return a
        old_key = (etc / "key").read_text()
        check("set up again: accepted", helper({"cmd": "again", "seconds": 4}).get("applies") is True)
        check("set up again: the hotspot opens as a real setup", wait(lambda: call("GET", "/api/hello")[1].get("setup") is True and call("GET", "/api/hello")[1].get("trial") is False))
        a_ = call("GET", "/api/alive")[1]
        check("set up again: the page is told so, and how long the window has left",
              a_.get("again") is True and a_.get("trial") is False and 0 < a_.get("left", 0) <= 4
              and call("GET", "/api/status")[1]["portal"].get("again") is True, a_)
        check("set up again: with nothing sent, it ends and the box is as before",
              wait(lambda: _closed(), 10) and (etc / "key").read_text() == old_key and helper({"cmd": "status"}).get("mode") == "normal")
        # The box was on the profile "anve-wifi", and joining another Wi-Fi deletes exactly
        # that profile. It must be back afterwards, or the box has no way home.
        st = helper({"cmd": "status"})
        check("set up again: with nothing sent, the box is back on the Wi-Fi profile it was on",
              st.get("wifi") == "anve-wifi" and st.get("wifi_kept") is False, st)
        new_key = "anve_dev_" + "n" * 40
        check("set up again: a window that only gets a wrong password", helper({"cmd": "again", "seconds": 4}).get("ok") is True
              and wait(lambda: call("GET", "/api/hello")[1].get("setup") is True))
        pubw = pairing.unb64(call("GET", "/api/hello")[1]["pub"], 32)
        call("POST", "/api/provision", pairing.seal(pubw, {"wifi": {"ssid": "Home Wi-Fi", "password": "wrong-again"}, "name": "X", "room": "", "key": new_key})[0])
        check("set up again: the wrong password is reported", wait(lambda: call("GET", "/api/hello")[1].get("error") == "wrong_password"))
        check("set up again: the window ends", wait(lambda: _closed(), 12) and wait(lambda: helper({"cmd": "status"}).get("mode") == "normal"))
        st = helper({"cmd": "status"})
        check("set up again: after only a wrong password, the box is back on the Wi-Fi profile it was on, with its key",
              st.get("wifi") == "anve-wifi" and st.get("wifi_kept") is False and (etc / "key").read_text() == old_key, st)
        check("set up again: second window", helper({"cmd": "again", "seconds": 8}).get("ok") is True and wait(lambda: call("GET", "/api/hello")[1].get("setup") is True))
        pub3 = pairing.unb64(call("GET", "/api/hello")[1]["pub"], 32)
        msg, key = pairing.seal(pub3, {"wifi": {"ssid": "Home Wi-Fi", "password": "wrong-again"}, "name": "X", "room": "", "key": new_key})
        call("POST", "/api/provision", msg)
        check("set up again: a wrong Wi-Fi password brings the hotspot back with the reason, key unchanged",
              wait(lambda: call("GET", "/api/hello")[1].get("error") == "wrong_password") and (etc / "key").read_text() == old_key)
        msg, key = pairing.seal(pub3, {"wifi": {"ssid": "Home Wi-Fi", "password": "correct horse"}, "name": "Bedroom box", "room": "Bedroom", "key": new_key})
        s_, r_ = call("POST", "/api/provision", msg)
        check("set up again: good settings are accepted", s_ == 200 and pairing.open_reply(key, r_).get("ok") is True, (s_, r_))
        check("set up again: the new key and name are stored, the old key is kept beside it, the Wi-Fi sign-in is kept",
              wait(lambda: (etc / "key").read_text() == new_key) and (etc / "key.before-resetup").read_text() == old_key
              and json.loads((state / "device.json").read_text())["name"] == "Bedroom box" and (etc / "wifi-login").exists()
              and stat.S_IMODE(os.stat(etc / "key.before-resetup").st_mode) == 0o600)
        check("set up again: afterwards normal mode, setup server closed",
              wait(lambda: _closed() and helper({"cmd": "status"}).get("mode") == "normal", 10))
        check("set up again: on the new Wi-Fi, and the copy of the old profile is gone",
              wait(lambda: helper({"cmd": "status"}).get("wifi_kept") is False, 5)
              and helper({"cmd": "status"}).get("wifi") == "anve-wifi", helper({"cmd": "status"}))
        s = socket.socket(socket.AF_UNIX)
        s.connect(str(state / "provision.sock"))
        s.sendall(b'{"cmd":"reset"}\n')
        ans = json.loads(s.makefile().readline())
        s.close()
        check("reset accepted", ans.get("ok") is True)
        check("after a reset the hotspot is open again",
              wait(lambda: call("GET", "/api/hello")[1].get("setup") is True))
        check("after a reset: no key, no sign-in, not set up, and a NEW app token",
              not (etc / "key").exists() and not (etc / "wifi-login").exists()
              and not (state / "provisioned").exists() and (etc / "app-token").read_text() != token_before)
        check("after a reset the box has a new key pair",
              call("GET", "/api/hello")[1]["pub"] != hello["pub"])
        page_only_setup(identity, etc, state)
        final_checks.append(lambda: not any(x in (tmp / "log").read_text() for x in (
            identity.claim_secret.reveal(), "page-only password", "wrong-from-page")))
        check("no secret from the later steps in the log either", all(f() for f in final_checks))
    finally:
        proc.terminate()
        proc.wait(5)
        logf.close()
    print(f"\n{sum(results)}/{len(results)} passed")
    if not all(results):
        print("--- setup program log ---")
        print((tmp / "log").read_text()[-3000:])
    return 0 if all(results) else 1


def raw(method, path, host=None):
    """One request with no redirect followed and any Host: -> (status, headers, body)."""
    import http.client
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=10)
    c.putrequest(method, path, skip_host=True)
    c.putheader("Host", host or f"127.0.0.1:{PORT}")
    c.endheaders()
    r = c.getresponse()
    out = (r.status, {k.lower(): v for k, v in r.getheaders()}, r.read())
    c.close()
    return out


def page_before_setup(pub, hello, identity, box_dir, etc, state, tmp):
    """The setup page (a computer, no app), on a box that is not set up yet."""
    print("the setup page")
    home = f"http://127.0.0.1:{PORT}/"
    s, h, body = raw("GET", "/")
    check("the page is served at /", s == 200 and h["content-type"].startswith("text/html") and b"Set up your ANVE Box" in body
          and h.get("x-content-type-options") == "nosniff")
    import re
    refs = set(re.findall(rb'(?:href|src)="(assets/[^"#]+)', body))
    got = {r: raw("GET", "/" + r.decode()) for r in refs}
    check(f"everything the page asks for is there ({len(refs)} files), with the right type",
          len(refs) >= 5 and all(v[0] == 200 and len(v[2]) > 100 for v in got.values())
          and got[b"assets/portal.js"][1]["content-type"].startswith("text/javascript")
          and got[b"assets/seal/seal.mjs"][1]["content-type"].startswith("text/javascript")
          and got[b"assets/portal.css"][1]["content-type"].startswith("text/css"), str({k: v[0] for k, v in got.items()}))
    seal_js = got[b"assets/seal/seal.mjs"][2].decode()
    mods = re.findall(r'from"(\./[^"]+)"', seal_js)
    check("the encryption modules the page loads are served too",
          len(mods) == 4 and all(raw("GET", "/assets/seal/" + m[2:])[0] == 200 for m in mods), str(mods))
    for host, path in (("captive.apple.com", "/hotspot-detect.html"), ("connectivitycheck.gstatic.com", "/generate_204"),
                       ("www.msftconnecttest.com", "/connecttest.txt"), ("www.msftconnecttest.com", "/redirect"),
                       ("detectportal.firefox.com", "/canonical.html"), ("example.com", "/")):
        s, h, body = raw("GET", path, host)
        check(f"\"am I online?\" from {host}{path} is sent to the page", s == 302 and h.get("location") == home, (s, h.get("location")))
    s, h, body = raw("GET", "/some/old/bookmark")
    check("an address the box does not know leads to the page too", s == 302 and h.get("location") == home)
    s, h, body = raw("HEAD", "/hotspot-detect.html", "captive.apple.com")
    check("HEAD is answered like GET, without a body", s == 302 and body == b"")
    for path in ("/assets/../../anve_device/provision.py", "/assets/..%2f..%2fconfig.toml", "/src/seal.mjs", "/assets/seal/NOTICE.txt"):
        s, h, body = raw("GET", path)
        check(f"not served: {path}", s in (302, 404) and b"anve_device" not in body and b"import " not in body, (s, body[:40]))
    s, r = call("GET", "/api/nothing")
    check("an unknown API call is still a plain 404 (the app relies on it)", s == 404 and r["error"] == "not_found")

    s, st = call("GET", "/api/status")
    secret = identity.claim_secret.reveal()
    check("status: in the team's shape, with the box's cloud ID",
          s == 200 and st["box"] == identity.device_id and st["setupWifi"].startswith("ANVE-") and st["mode"] == "SETUP"
          and st["error"] and st["error"]["code"] == "wrong_password" and st["error"]["network"] == "Home Wi-Fi"
          and set(st["lights"]) == {"online", "blocked", "setup"} and st["lights"]["online"]["label"] == "Two short blinks", str(st)[:300])
    check("status: never the link, never a secret", st["claimLink"] is None and secret not in json.dumps(st)
          and identity.device_key.reveal() not in json.dumps(st) and st["portal"]["claimable"] is True
          and st["portal"]["pub"] == hello["pub"] and st["portal"]["voice"] is False)
    nets = call("GET", "/api/networks")[1]["networks"]
    check("networks: bars for the page, and the app's fields untouched",
          [n["bars"] for n in nets] == [4, 3, 2, 1] and all({"ssid", "signal", "secure", "sec", "supported"} <= set(n) for n in nets), str(nets))
    s, r = call("POST", "/api/connect", {"ssid": "Home Wi-Fi", "password": "plain-text password"})
    check("a password sent in the clear is refused (the setup Wi-Fi is open)", s == 400 and r["error"] == "sealed_only")
    s, r = call("POST", "/api/portal/connect", {"ssid": "Home Wi-Fi", "password": "plain-text password"})
    check("and so is anything unsealed on the page's own call", s == 400 and r["error"] == "bad_message")
    s, r = call("POST", "/api/portal/connect", pairing.seal(pub, {"wifi": {"ssid": "Home Wi-Fi", "password": "short"}})[0])
    check("the page's details are checked field by field", s == 400 and r.get("field") == "wifi.password")

    # a wrong password from the page: the link is handed over sealed, the box waits, then says why
    msg, key = pairing.seal(pub, {"wifi": {"ssid": "Home Wi-Fi", "password": "wrong-from-page"}, "name": "Desk"})
    s, r = call("POST", "/api/portal/connect", msg)
    rep = pairing.open_reply(key, r) if s == 200 else {}
    # the same link boxlink.claim_link() makes (tools/boxlink_test.py pins its form); built
    # here by hand because the setup program runs on the system's Python, without websockets
    want = f"{rep.get('cloud', '')}/claim#{identity.device_id}.{secret}"
    try:
        from anve_device import boxlink
        want_too = boxlink.claim_link(rep.get("cloud", ""), identity, boxlink.Disk(box_dir))
    except ImportError:
        want_too = want
    check("the setup program and the box link make the same link", want == want_too and rep.get("cloud", "").startswith("https://"))
    check("the page's answer is sealed and carries the link that adds the box to an account",
          s == 200 and set(r) == {"n", "c"} and rep.get("claimLink") == want and secret in want and rep["hold"] == 2
          and rep["voice"] is False and rep["network"] == "Home Wi-Fi", str({k: v for k, v in rep.items() if k != "claimLink"}))
    check("while the person copies the link the box stays on its hotspot",
          call("GET", "/api/status")[1]["portal"]["held"] is True and call("GET", "/api/hello")[1]["busy"] is True)
    s2, r2 = call("POST", "/api/portal/connect", pairing.seal(pub, {"wifi": {"ssid": "Home Wi-Fi", "password": "another one"}})[0])
    check("a second setup cannot cut in", s2 == 409 and r2["error"] == "busy")
    s2, r2 = call("POST", "/api/portal/go", {"ticket": "0" * 32})
    check("\"connect now\" needs the ticket from the sealed answer", s2 == 409)
    s2, r2 = call("POST", "/api/portal/go", {"ticket": rep["ticket"]})
    check("with it, the box goes", s2 == 200 and r2["ok"] is True)
    check("wrong password: the hotspot is back and the page is told which network and why",
          wait(lambda: (call("GET", "/api/status")[1]["error"] or {}).get("code") == "wrong_password"
               and call("GET", "/api/status")[1]["portal"]["busy"] is False))
    check("nothing was stored by any of this", not (etc / "key").exists() and not (state / "provisioned").exists()
          and not (state / "device.json").exists())

    # the switch, and the one file it writes (in a folder of the test's own)
    from anve_device import provision
    conf = tmp / "dnsmasq-shared.d" / "anve-captive.conf"
    old = provision.CAPTIVE_CONF
    provision.CAPTIVE_CONF = conf
    try:
        net = provision.Net()
        net.captive(True)
        made = conf.read_text() if conf.exists() else ""
        net.captive(False)
        gone = not conf.exists()
        os.environ["ANVE_CAPTIVE"] = "0"
        net.captive(True)
        off = not conf.exists()
    finally:
        os.environ.pop("ANVE_CAPTIVE", None)
        provision.CAPTIVE_CONF = old
    check("opening by itself: one line while the hotspot is up, gone after, and a switch to turn it off",
          "address=/#/10.42.0.1\n" in made and gone and off, made)


def page_only_setup(identity, etc, state):
    """A box set up from the page alone: on the Wi-Fi, set up, no voice key; nobody presses
    "connect now", so it goes by itself."""
    print("set up from the page alone")
    hello = call("GET", "/api/hello")[1]
    pub = pairing.unb64(hello["pub"], 32)
    msg, key = pairing.seal(pub, {"wifi": {"ssid": "Open Campus", "password": ""}, "name": " Study  box ", "room": "Study",
                                  "portal": {"username": "student1", "password": "page-only password"}})
    s, r = call("POST", "/api/portal/connect", msg)
    rep = pairing.open_reply(key, r) if s == 200 else {}
    check("accepted without a device key, with the link", s == 200 and identity.claim_secret.reveal() in (rep.get("claimLink") or "")
          and rep.get("voice") is False, (s, r))
    check("nobody presses \"connect now\": the box joins by itself and is set up",
          wait(lambda: (state / "provisioned").exists(), 10) and wait(lambda: _closed(), 10))
    check("name, room and the Wi-Fi sign-in are stored; there is no voice key, and none was invented",
          json.loads((state / "device.json").read_text()) == {"name": "Study box", "room": "Study"}
          and (etc / "wifi-login").read_text() == "student1\npage-only password\n" and not (etc / "key").exists())


def _closed() -> bool:
    try:
        call("GET", "/api/hello")
        return False
    except OSError:
        return True


if __name__ == "__main__":
    sys.exit(main())
