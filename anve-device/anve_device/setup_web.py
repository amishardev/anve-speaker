"""ANVE speaker setup page: a small web app served by the Pi itself.

    .venv/bin/python -m anve_device.setup_web            http://127.0.0.1:8737

What it does:
  * checks every part of the device (power, network, microphone, speaker, device key,
    service, link to ANVE) and says in plain words what is wrong;
  * "Set up everything" works through the failing checks BY ITSELF: it runs the fix for
    each one, checks again, and reports what it did and what only a person can do
    (paste the key, switch the speaker on, plug the microphone in);
  * takes the device key in a form and stores it root-only on the Pi;
  * pairs a Bluetooth speaker, plays a test sound, measures the microphone, runs a
    text turn or the read-only capability sweep against ANVE, shows the service log.

Security: it listens on 127.0.0.1 ONLY. The campus Wi-Fi is an open network, so the page
(and the key typed into it) must not cross it in clear text. Reach it from a laptop
through SSH:   ssh -L 8737:127.0.0.1:8737 <user>@<pi>   then open http://localhost:8737
Changes need a same-origin request with a custom header, so another web page open in the
same browser cannot trigger them. The key is never logged, echoed or returned.
"""
import argparse
import json
import os
import re
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import config as C
from .audio import find_node, nodes

ROOT = Path(__file__).resolve().parent.parent
UNIT = "anve-device.service"
KEY_FILE = "/etc/anve-device/key"
KEY_RE = re.compile(r"^anve_dev_[A-Za-z0-9_\-]{30,190}$")
MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
JOB = {"running": False, "title": "", "log": [], "done": ""}
JOB_LOCK = threading.Lock()


def sh(cmd, timeout=25, stdin=None) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True,
                           timeout=timeout, input=stdin)
        return r.returncode, (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired:
        return 124, "timed out"
    except Exception as e:                                   # noqa: BLE001
        return 1, f"{type(e).__name__}: {e}"


def cfg() -> dict:
    return C.load()


# ---------- checks ----------

def check_power():
    _, thr = sh("vcgencmd get_throttled")
    _, uv = sh("journalctl -k -b --no-pager | grep -ci 'undervoltage detected'")
    _, cap = sh("systemctl is-active anve-cpu-cap.service")
    _, khz = sh("cat /sys/devices/system/cpu/cpufreq/policy0/scaling_max_freq")
    capped = cap == "active"
    n = int(uv) if uv.isdigit() else 0
    detail = (f"CPU capped at {int(khz) / 1e6:.1f} GHz. " if khz.isdigit() else "") + \
        (f"{n} under-voltage event(s) since boot: the power supply is too weak for full speed."
         if n else "No under-voltage since boot.")
    return {"id": "power", "title": "Power", "ok": capped, "detail": detail,
            "fix": not capped, "human": ""}


def check_network():
    _, con = sh("nmcli -t -f GENERAL.CONNECTION dev show wlan0")
    con = con.split(":", 1)[-1] if ":" in con else ""
    host = cfg()["server"]["url"].split("//", 1)[-1].split("/", 1)[0]
    human = ""
    try:
        # a real TLS handshake, certificate checked: a Wi-Fi sign-in page that answers
        # for every site passes a plain connection test but fails this one
        import ssl
        t0 = time.monotonic()
        name = host.split(":")[0]
        with socket.create_connection((name, 443), timeout=6) as raw:
            with ssl.create_default_context().wrap_socket(raw, server_hostname=name):
                pass
        reach = f"{host} reachable in {int((time.monotonic() - t0) * 1000)} ms"
        ok = True
    except ssl.SSLCertVerificationError:
        reach, ok = f"{host} is being answered by a different site (the Wi-Fi sign-in page)", False
        human = ("The Wi-Fi wants a sign-in. Your ID and password are stored and the Pi retries "
                 "every 45 seconds; if this stays red, check them below." if wifi_stored() else
                 "The Wi-Fi wants a sign-in: enter your network ID and password under “Wi-Fi sign-in” below.")
    except OSError as e:
        reach, ok = f"{host} NOT reachable ({type(e).__name__})", False
        human = "Check the Wi-Fi and any campus sign-in page."
    return {"id": "network", "title": "Network", "ok": ok, "fix": False,
            "detail": f"Wi-Fi: {con or 'not connected'}. {reach}.", "human": human}


def check_mic():
    want = cfg()["audio"]["mic"]
    node = next((n for n in nodes("Audio/Source")
                 if want.lower() in (n["name"] + n["description"]).lower()
                 and not n["name"].startswith("anve_ec")), None)
    return {"id": "mic", "title": "Microphone", "ok": bool(node), "fix": False,
            "detail": f"Found: {node['description']}" if node else f"No microphone matching '{want}'.",
            "human": "" if node else "Plug the USB microphone into the Pi."}


def check_speaker():
    a = cfg()["audio"]
    mac, want = a.get("bluetooth_mac", ""), a["speaker"]
    node = find_node("Audio/Sink", want)
    bt = ""
    if mac:
        _, info = sh(["bluetoothctl", "info", mac])
        bt = "connected" if "Connected: yes" in info else "not connected"
        m = re.search(r"Battery Percentage: 0x\w+ \((\d+)\)", info)
        if m:
            bt += f", battery {m.group(1)} %"
    ok = bool(node)
    return {"id": "speaker", "title": "Speaker", "ok": ok, "fix": bool(mac) and not ok,
            "detail": (f"Output: {want}" if ok else f"No output matching '{want}'.")
            + (f" Bluetooth {mac}: {bt}." if mac else ""),
            "human": "" if ok else "Switch the speaker on and free it from your phone."}


def key_state() -> str:
    rc, _ = sh(["sudo", "-n", "test", "-f", KEY_FILE])
    if rc != 0:
        return "missing"
    _, out = sh(["sudo", "-n", "sh", "-c",
                 f'k=$(cat {KEY_FILE}); case "$k" in anve_dev_*) [ ${{#k}} -ge 40 ] && '
                 'echo ok || echo short;; *) echo bad;; esac'])
    return out if out in ("ok", "short", "bad") else "unreadable"


def check_key():
    st = key_state()
    return {"id": "key", "title": "Device key", "ok": st == "ok", "fix": False,
            "detail": {"ok": "Stored on the Pi (root only). It is never shown.",
                       "missing": "No device key on the Pi yet.",
                       "short": "The stored key is too short.", "bad": "The stored key is not a device key.",
                       "unreadable": "The key file could not be checked."}[st],
            "human": "" if st == "ok" else "Create a Room device in ANVE (Assistant page, Devices) and paste its key below."}


def service_lines(n=200) -> list[str]:
    _, out = sh(f"journalctl -u {UNIT} -n {n} --no-pager -o cat", timeout=10)
    return out.splitlines()


def check_service():
    _, en = sh(f"systemctl is-enabled {UNIT}")
    _, act = sh(f"systemctl is-active {UNIT}")
    _, lin = sh("loginctl show-user $(id -un) -p Linger --value")
    ok = act == "active"
    why = ""
    if not ok and key_state() != "ok":
        why = " It starts by itself once the device key is stored."
    return {"id": "service", "title": "Speaker service", "ok": ok,
            "fix": not ok and key_state() == "ok" or en != "enabled" or lin != "yes",
            "detail": f"{'Running' if ok else 'Not running'} ({en or 'not installed'}; starts at boot: "
                      f"{'yes' if lin == 'yes' and en == 'enabled' else 'no'}).{why}", "human": ""}


def check_link():
    _, act = sh(f"systemctl is-active {UNIT}")
    if act != "active":
        return {"id": "link", "title": "Link to ANVE", "ok": False, "fix": False,
                "detail": "The service is not running.", "human": ""}
    last, ready = "", ""
    for ln in service_lines():
        if "ready: session" in ln:
            ready = ln.split("ready:", 1)[1].strip()
        if any(k in ln for k in ("anve.device: connected", "link down", "not reconnecting",
                                 "server closing", "server error")):
            last = ln
    ok = "anve.device: connected" in last
    human = ""
    if "not reconnecting" in last:
        reason = re.search(r"said '(\w+)'", last)
        r = reason.group(1) if reason else ""
        human = {"unauthorised": "The key is wrong or revoked: create a new one and paste it below.",
                 "rate": "Too many sessions this hour. Wait, then press Restart.",
                 "concurrent": "Close the assistant in your browser, then press Restart."}.get(r, "")
    eng = re.search(r"engine=(\w+) cloud=(\w+)", ready)
    return {"id": "link", "title": "Link to ANVE", "ok": ok, "fix": False,
            "detail": (f"Connected (engine {eng.group(1)}, {'cloud' if eng.group(2) == 'True' else 'local'})."
                       if ok and eng else "Connected." if ok else
                       (re.sub(r'^\w+ anve\.\w+: ', '', last)[:160] or "No connection yet.")),
            "human": human}


CHECKS = (check_power, check_network, check_mic, check_speaker, check_key, check_service, check_link)


def status() -> dict:
    out = []
    for f in CHECKS:
        try:
            out.append(f())
        except Exception as e:                               # noqa: BLE001
            out.append({"id": f.__name__[6:], "title": f.__name__[6:], "ok": False, "fix": False,
                        "detail": f"check failed: {type(e).__name__}", "human": ""})
    a = cfg()
    return {"checks": out, "all_ok": all(c["ok"] for c in out), "time": time.strftime("%H:%M:%S"),
            "config": {"mic": a["audio"]["mic"], "speaker": a["audio"]["speaker"],
                       "bluetooth_mac": a["audio"].get("bluetooth_mac", ""),
                       "speaker_volume": a["audio"]["speaker_volume"],
                       "leds": a["leds"]["enabled"], "server": a["server"]["url"],
                       "engine": a["server"]["engine"]}}


# ---------- fixes: what the page can do by itself ----------

def fix_power(log):
    log("Enabling the CPU cap for the weak power supply")
    sh("sudo -n systemctl enable --now anve-cpu-cap.service")


def fix_speaker(log):
    a = cfg()["audio"]
    mac = a.get("bluetooth_mac", "")
    if not mac:
        log("No Bluetooth address in the config: pair a speaker below")
        return
    sh(["bluetoothctl", "trust", mac])
    for i in range(3):
        log(f"Connecting to the speaker {mac} (try {i + 1})")
        sh(["bluetoothctl", "--timeout", "12", "connect", mac], timeout=20)
        _, info = sh(["bluetoothctl", "info", mac])
        if "Connected: yes" in info:
            time.sleep(3)
            set_volume(a["speaker"], a["speaker_volume"])
            log(f"Speaker connected; volume set to {int(a['speaker_volume'] * 100)} %")
            return
        time.sleep(3)
    log("The speaker did not answer: it is off, out of range, or connected to another device")


def fix_service(log):
    log("Installing the service and enabling start at boot")
    rc, out = sh(["bash", str(ROOT / "tools" / "install_service.sh")], timeout=60)
    for ln in out.splitlines()[-3:]:
        log("  " + ln[:150])
    if key_state() == "ok":
        time.sleep(6)


FIXES = {"power": fix_power, "speaker": fix_speaker, "service": fix_service}


def set_volume(match: str, vol: float):
    for n in nodes("Audio/Sink"):
        if match.lower() in (n["name"] + n["description"]).lower():
            sh(["wpctl", "set-volume", str(n["id"]), f"{vol:.2f}"])
            sh(["wpctl", "set-mute", str(n["id"]), "0"])


def start_job(title, fn) -> bool:
    with JOB_LOCK:
        if JOB["running"]:
            return False
        JOB.update(running=True, title=title, log=[], done="")

    def log(msg):
        JOB["log"].append(f"{time.strftime('%H:%M:%S')}  {msg}")

    def run():
        try:
            JOB["done"] = fn(log) or "Finished."
        except Exception as e:                               # noqa: BLE001
            JOB["done"] = f"Stopped: {type(e).__name__}: {e}"
        finally:
            JOB["running"] = False
    threading.Thread(target=run, daemon=True).start()
    return True


def job_autosetup(log):
    """The agent: look, fix what a program can fix, look again, and say what is left."""
    left = []
    for attempt in (1, 2):
        st = status()
        bad = [c for c in st["checks"] if not c["ok"]]
        if not bad:
            break
        log(f"Pass {attempt}: {len(bad)} thing(s) not ready: " + ", ".join(c["title"] for c in bad))
        acted = False
        for c in bad:
            if c["fix"] and c["id"] in FIXES:
                log(f"Fixing: {c['title']}")
                FIXES[c["id"]](log)
                acted = True
        if not acted:
            break
        time.sleep(4)
    st = status()
    for c in st["checks"]:
        log(f"{'OK  ' if c['ok'] else 'TODO'} {c['title']}: {c['detail']}")
        if not c["ok"]:
            left.append(c)
    if not left:
        return "Everything is ready. Say “Hey ANVE”."
    # say what a person must do; things that only wait on those (the service waiting for
    # the key, the link waiting for the service) need no line of their own
    todo = list(dict.fromkeys(c["human"] for c in left if c["human"]))
    if not todo:
        todo = [f"{c['title']}: {c['detail']}" for c in left]
    return "Needs you: " + " | ".join(todo)


def job_server_test(log):
    if key_state() != "ok":
        return "No device key yet: paste it first."
    log("Opening one text-only session with ANVE (this uses 1 of the 30 sessions an hour)")
    rc, out = sh(["sudo", "-n", str(ROOT / ".venv/bin/python"), str(ROOT / "tools/ask.py"),
                  "--text", "Say hello in one short sentence."], timeout=90)
    for ln in out.splitlines():
        if ln.strip():
            log(ln[:220])
    return "ANVE answered." if rc == 0 and "turns answered" in out else "ANVE did not answer: see the lines above."


def job_suite(log):
    if key_state() != "ok":
        return "No device key yet: paste it first."
    log("Asking ANVE the 15 read-only questions by synthesized voice (about 4 minutes, 1 session)")
    rc, out = sh(["sudo", "-n", str(ROOT / ".venv/bin/python"), str(ROOT / "tools/ask.py"),
                  "--suite", "readonly"], timeout=900)
    for ln in out.splitlines():
        if ln.strip():
            log(ln[:220])
    return "Sweep finished." if rc == 0 else "Sweep stopped early: see the lines above."


def job_speaker_test(log):
    a = cfg()["audio"]
    sink = find_node("Audio/Sink", "anve_ec_sink") or find_node("Audio/Sink", a["speaker"])
    if not sink:
        return "No speaker output to play on."
    sh("sox -n -r 48000 -c 2 /tmp/anve_setup_chime.wav synth 0.3 sine 660 fade q 0.02 0.3 0.05 gain -16 "
       ": synth 0.4 sine 880 fade q 0.02 0.4 0.08 gain -16 pad 0.3 0.2")
    log(f"Playing two notes on {sink}")
    rc, out = sh(["pw-play", "--target", sink, "/tmp/anve_setup_chime.wav"], timeout=15)
    return "Played. Did you hear two notes?" if rc == 0 else f"Could not play: {out[:120]}"


def job_mic_test(log):
    import wave

    import numpy as np
    want = cfg()["audio"]["mic"]
    node = next((n["name"] for n in nodes("Audio/Source")
                 if want.lower() in (n["name"] + n["description"]).lower()
                 and not n["name"].startswith("anve_ec")), None)
    if not node:
        return "No microphone found."
    log("Recording 4 seconds: say something now")
    sh(["timeout", "5", "pw-record", "--target", node, "--rate", "16000", "--channels", "1",
        "--format", "s16", "/tmp/anve_setup_mic.wav"], timeout=10)
    w = wave.open("/tmp/anve_setup_mic.wav")
    x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    if not x.size:
        return "The recording is empty."
    fr = [20 * np.log10(max(float(np.sqrt(np.mean(x[i:i + 640] ** 2))), 1e-6))
          for i in range(0, x.size - 640, 640)]
    from .wake import SileroVAD
    vad = SileroVAD()
    b = (x * 32767).astype(np.int16).tobytes()
    probs = [vad.feed(b[i:i + 1280]) for i in range(0, len(b) - 1279, 1280)]
    log(f"Loudest moment {max(fr):.0f} dBFS, quietest {min(fr):.0f} dBFS")
    heard = max(probs) >= 0.5
    return ("The microphone hears you (speech detected)." if heard else
            "The microphone works, but no speech was detected in those 4 seconds.")


def job_pair(mac):
    def run(log):
        log(f"Pairing with {mac}: put the speaker in pairing mode")
        sh(["bluetoothctl", "--timeout", "10", "scan", "on"], timeout=15)
        for step in ("pair", "trust", "connect"):
            rc, out = sh(["bluetoothctl", "--timeout", "15", step, mac], timeout=25)
            log(f"{step}: {(out.splitlines() or ['done'])[-1][:120]}")
        _, info = sh(["bluetoothctl", "info", mac])
        if "Connected: yes" not in info:
            return "The speaker did not connect."
        name = (re.search(r"Name: (.+)", info) or [None, ""])[1].strip()
        update_config({"bluetooth_mac": mac, "speaker": name.split()[0] if name else cfg()["audio"]["speaker"]})
        sh(f"sudo -n systemctl try-restart {UNIT}")
        return f"Connected to {name or mac} and saved as the speaker."
    return run


def update_config(values: dict):
    """Change simple `key = value` lines in config.toml (the [audio] and [leds] ones)."""
    p = ROOT / "config.toml"
    if not p.exists():
        p.write_text((ROOT / "config.example.toml").read_text())
    s = p.read_text()
    for k, v in values.items():
        lit = ("true" if v else "false") if isinstance(v, bool) else \
            f"{v:.2f}" if isinstance(v, float) else '"' + str(v).replace('"', "") + '"'
        if k == "leds":
            s = re.sub(r"(\[leds\]\s*\nenabled\s*=\s*)\w+", lambda m: m.group(1) + lit, s)
        else:
            s, n = re.subn(rf"(?m)^({re.escape(k)}\s*=\s*)[^#\n]*?(\s*(#.*)?)$",
                           lambda m: m.group(1) + lit + ("   " + m.group(3) if m.group(3) else ""), s, count=1)
            if not n:
                s = re.sub(r"(?m)^\[audio\]\s*$", f"[audio]\n{k} = {lit}", s, count=1)
    p.write_text(s)


def store_key(key: str) -> str:
    key = re.sub(r"\s+", "", key or "")
    if not KEY_RE.match(key):
        return "That is not a device key: it must start with anve_dev_ and be about 52 characters."
    sh(["sudo", "-n", "install", "-d", "-m", "700", "-o", "root", "-g", "root", "/etc/anve-device"])
    rc, out = sh(["sudo", "-n", "install", "-m", "600", "-o", "root", "-g", "root", "/dev/stdin", KEY_FILE],
                 stdin=key)
    if rc != 0:
        return "The key could not be written."
    sh(f"sudo -n systemctl enable {UNIT}; sudo -n systemctl restart {UNIT}")
    return ""


WIFI_FILE = "/etc/anve-device/wifi-login"


def wifi_stored() -> bool:
    return sh(["sudo", "-n", "test", "-s", WIFI_FILE])[0] == 0


def store_wifi(user: str, password: str) -> tuple[bool, str]:
    """Store the Wi-Fi sign-in root-only on the Pi and try it at once. The values are
    never logged, echoed or returned."""
    user = (user or "").strip()
    if not user or not password or len(user) > 100 or len(password) > 200 or \
            any(c in user + password for c in "\r\n\0"):
        return False, "Enter both the ID and the password."
    sh(["sudo", "-n", "install", "-d", "-m", "700", "-o", "root", "-g", "root", "/etc/anve-device"])
    rc, _ = sh(["sudo", "-n", "install", "-m", "600", "-o", "root", "-g", "root", "/dev/stdin", WIFI_FILE],
               stdin=f"{user}\n{password}\n")
    if rc != 0:
        return False, "The sign-in could not be stored."
    sh("sudo -n systemctl enable --now anve-wifi-login.timer")
    sh("sudo -n systemctl start anve-wifi-login.service", timeout=40)
    _, last = sh("journalctl -u anve-wifi-login.service -n 1 --no-pager -o cat")
    _, st = sh(["python3", str(ROOT / "tools" / "wifi_login.py"), "--status"], timeout=15)
    ok = st.startswith("online")
    return ok, ("Signed in. The Pi will sign itself in again whenever the network asks."
                if ok else (last or st or "The sign-in did not go through.")[:200])


def bt_devices() -> list[dict]:
    sh(["bluetoothctl", "--timeout", "8", "scan", "on"], timeout=12)
    _, out = sh(["bluetoothctl", "devices"])
    _, paired = sh(["bluetoothctl", "devices", "Paired"])
    seen = []
    for ln in out.splitlines():
        m = re.match(r"Device ((?:[0-9A-F]{2}:){5}[0-9A-F]{2}) (.+)", ln)
        if m and not re.fullmatch(r"(?:[0-9A-F]{2}-){5}[0-9A-F]{2}", m.group(2)):
            seen.append({"mac": m.group(1), "name": m.group(2), "paired": m.group(1) in paired})
    return sorted(seen, key=lambda d: (not d["paired"], d["name"]))


# ---------- HTTP ----------

class Handler(BaseHTTPRequestHandler):
    server_version = "anve-setup"

    def log_message(self, fmt, *args):                      # no bodies, no query strings
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _local(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost", "[::1]")

    def do_GET(self):
        if not self._local():
            return self._json({"error": "local only"}, 403)
        path = self.path.split("?")[0]
        if path == "/":
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'self' 'unsafe-inline'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/status":
            self._json(status())
        elif path == "/api/job":
            self._json(JOB)
        elif path == "/api/logs":
            self._json({"lines": [ln[:240] for ln in service_lines(80)]})
        elif path == "/api/bluetooth":
            self._json({"devices": bt_devices()})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        if not self._local() or self.headers.get("X-ANVE-Setup") != "1":
            return self._json({"error": "refused"}, 403)
        n = min(int(self.headers.get("Content-Length") or 0), 4096)
        try:
            data = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            data = {}
        path = self.path.split("?")[0]
        jobs = {"/api/autosetup": ("Setting up", job_autosetup),
                "/api/test/server": ("Testing the link to ANVE", job_server_test),
                "/api/test/suite": ("Capability sweep (read-only)", job_suite),
                "/api/test/speaker": ("Speaker test", job_speaker_test),
                "/api/test/mic": ("Microphone test", job_mic_test)}
        if path in jobs:
            ok = start_job(*jobs[path])
            return self._json({"ok": ok, "error": "" if ok else "Something is already running."})
        if path == "/api/key":
            err = store_key(str(data.get("key", "")))
            return self._json({"ok": not err, "error": err})
        if path == "/api/wifi":
            ok, msg = store_wifi(str(data.get("user", "")), str(data.get("password", "")))
            return self._json({"ok": ok, "message": msg})
        if path == "/api/pair":
            mac = str(data.get("mac", "")).upper()
            if not MAC_RE.match(mac):
                return self._json({"ok": False, "error": "Not a Bluetooth address."})
            ok = start_job("Pairing the speaker", job_pair(mac))
            return self._json({"ok": ok, "error": "" if ok else "Something is already running."})
        if path == "/api/volume":
            try:
                v = max(0.05, min(1.0, float(data.get("volume"))))
            except (TypeError, ValueError):
                return self._json({"ok": False, "error": "Not a number."})
            update_config({"speaker_volume": v})
            set_volume(cfg()["audio"]["speaker"], v)
            sh(f"sudo -n systemctl try-restart {UNIT}")
            return self._json({"ok": True})
        if path == "/api/service":
            act = data.get("action")
            if act not in ("restart", "stop", "start"):
                return self._json({"ok": False, "error": "Unknown action."})
            rc, out = sh(f"sudo -n systemctl {act} {UNIT}")
            return self._json({"ok": rc == 0, "error": out[:200]})
        self._json({"error": "not found"}, 404)


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>ANVE speaker setup</title>
<style>
:root{--bg:#f4f5f7;--card:#fff;--ink:#16181d;--mut:#5d6470;--line:#dfe2e8;--ok:#177245;--bad:#b3261e;--acc:#1f4fd8;--accink:#fff;--warn:#8a5a00}
@media (prefers-color-scheme:dark){:root{--bg:#111318;--card:#1a1d24;--ink:#eceef2;--mut:#9aa1ad;--line:#2b2f39;--ok:#58c08a;--bad:#f08a80;--acc:#7da0ff;--accink:#0b1020;--warn:#e0b060}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,Segoe UI,Roboto,sans-serif}
main{max-width:860px;margin:0 auto;padding:20px 16px 60px}
h1{font-size:22px;margin:4px 0 2px}h2{font-size:15px;margin:0 0 10px;color:var(--mut);font-weight:600;text-transform:uppercase;letter-spacing:.04em}
.sub{color:var(--mut);margin:0 0 18px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;margin:0 0 14px}
.row{display:flex;gap:12px;align-items:flex-start;padding:10px 0;border-top:1px solid var(--line)}.row:first-of-type{border-top:0}
.dot{width:12px;height:12px;border-radius:50%;margin-top:5px;flex:none;background:var(--bad)}.dot.ok{background:var(--ok)}
.grow{flex:1;min-width:0}.t{font-weight:600}.d{color:var(--mut);overflow-wrap:anywhere}.h{color:var(--warn);margin-top:2px}
button{font:inherit;border:1px solid var(--line);background:var(--card);color:var(--ink);padding:8px 14px;border-radius:9px;cursor:pointer}
button.pri{background:var(--acc);color:var(--accink);border-color:var(--acc);font-weight:600}button:disabled{opacity:.5;cursor:default}
.btns{display:flex;flex-wrap:wrap;gap:8px}
input[type=password],input[type=text]{font:inherit;width:100%;padding:9px 11px;border:1px solid var(--line);border-radius:9px;background:var(--bg);color:var(--ink)}
.banner{padding:12px 14px;border-radius:10px;margin:0 0 14px;font-weight:600}.banner.ok{background:color-mix(in srgb,var(--ok) 16%,var(--card));color:var(--ok)}.banner.bad{background:color-mix(in srgb,var(--warn) 16%,var(--card));color:var(--warn)}
pre{background:var(--bg);border:1px solid var(--line);border-radius:9px;padding:10px;margin:10px 0 0;max-height:300px;overflow:auto;font:12.5px/1.45 ui-monospace,Consolas,monospace;white-space:pre-wrap;overflow-wrap:anywhere}
.msg{margin-top:8px;color:var(--mut)}.msg.err{color:var(--bad)}.inline{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
small{color:var(--mut)}
</style></head><body><main>
<h1>ANVE speaker setup</h1>
<p class="sub">This page runs on the Pi. It checks the device, fixes what it can by itself, and tells you what only you can do.</p>
<div id="banner" class="banner bad">Checking…</div>

<div class="card"><h2>Device check</h2><div id="checks"></div>
<div class="btns" style="margin-top:12px"><button class="pri" id="auto">Set up everything</button><button id="refresh">Check again</button></div>
<div class="msg" id="jobtitle"></div><pre id="joblog" hidden></pre></div>

<div class="card"><h2>Device key</h2>
<p class="d" style="margin:0 0 10px">In ANVE open the Assistant page, find the Devices card, add a device of kind <b>Room</b>, and paste the key it shows here. The key goes straight to a root-only file on the Pi and is never displayed again.</p>
<div class="inline"><input type="password" id="key" placeholder="anve_dev_…" autocomplete="off" spellcheck="false" style="flex:1;min-width:220px"><button class="pri" id="savekey">Store key</button></div>
<div class="msg" id="keymsg"></div></div>

<div class="card"><h2>Wi-Fi sign-in</h2>
<p class="d" style="margin:0 0 10px">If your Wi-Fi shows a login page (a campus or hostel network), enter that network ID and password here once. They are kept in a root-only file on the Pi, and the Pi signs itself in again whenever the network asks, within a minute.</p>
<div class="inline"><input type="text" id="wuser" placeholder="Network ID" autocomplete="off" spellcheck="false" style="flex:1;min-width:150px"><input type="password" id="wpass" placeholder="Password" autocomplete="off" style="flex:1;min-width:150px"><button class="pri" id="savewifi">Store and sign in</button></div>
<div class="msg" id="wifimsg"></div></div>

<div class="card"><h2>Tests</h2>
<div class="btns"><button data-job="/api/test/speaker">Play a test sound</button><button data-job="/api/test/mic">Test the microphone</button><button data-job="/api/test/server">Ask ANVE to say hello</button><button data-job="/api/test/suite">Run the read-only capability sweep</button></div>
<p class="d" style="margin:10px 0 0"><small>The last two open one ANVE session each (your account may open 30 an hour). The sweep only asks questions: it creates and sends nothing.</small></p></div>

<div class="card"><h2>Speaker</h2>
<div class="inline"><label for="vol">Volume</label><input type="range" id="vol" min="5" max="100" step="5" style="flex:1;min-width:160px"><span id="volv"></span><button id="setvol">Apply</button></div>
<p class="d" style="margin:8px 0 12px"><small>Above 40 % this speaker overloads the microphone and the device hears itself.</small></p>
<div class="btns"><button id="scan">Find Bluetooth speakers</button></div><div id="bt"></div></div>

<div class="card"><h2>Service</h2>
<div class="btns"><button data-svc="restart">Restart</button><button data-svc="stop">Stop</button><button id="showlog">Show the log</button></div><pre id="log" hidden></pre></div>
</main>
<script>
const $=s=>document.querySelector(s);
const post=(u,b)=>fetch(u,{method:"POST",headers:{"Content-Type":"application/json","X-ANVE-Setup":"1"},body:JSON.stringify(b||{})}).then(r=>r.json());
const esc=s=>String(s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
let volTouched=false;
async function refresh(){
  let s; try{s=await (await fetch("/api/status")).json()}catch(e){$("#banner").textContent="The setup page lost contact with the Pi.";$("#banner").className="banner bad";return}
  $("#checks").innerHTML=s.checks.map(c=>`<div class="row"><span class="dot ${c.ok?"ok":""}"></span><div class="grow"><div class="t">${esc(c.title)}</div><div class="d">${esc(c.detail)}</div>${c.ok||!c.human?"":`<div class="h">${esc(c.human)}</div>`}</div></div>`).join("");
  const bad=s.checks.filter(c=>!c.ok);
  $("#banner").textContent=s.all_ok?"Everything is ready. Say “Hey ANVE”.":`${bad.length} thing${bad.length>1?"s":""} not ready: ${bad.map(c=>c.title).join(", ")}`;
  $("#banner").className="banner "+(s.all_ok?"ok":"bad");
  if(!volTouched){$("#vol").value=Math.round(s.config.speaker_volume*100);volWarn()}
}
async function pollJob(){
  const j=await (await fetch("/api/job")).json();
  if(j.title){$("#jobtitle").textContent=j.running?j.title+"…":j.done;$("#joblog").hidden=!j.log.length;$("#joblog").textContent=j.log.join("\n");$("#joblog").scrollTop=1e6}
  document.querySelectorAll("button[data-job],#auto").forEach(b=>b.disabled=j.running);
  if(j.running)setTimeout(pollJob,1000);else refresh();
}
async function runJob(u,b){const r=await post(u,b);if(!r.ok){$("#jobtitle").textContent=r.error||"Could not start.";return}$("#jobtitle").scrollIntoView({block:"nearest"});pollJob()}
$("#auto").onclick=()=>runJob("/api/autosetup");
$("#refresh").onclick=refresh;
document.querySelectorAll("button[data-job]").forEach(b=>b.onclick=()=>runJob(b.dataset.job));
document.querySelectorAll("button[data-svc]").forEach(b=>b.onclick=async()=>{await post("/api/service",{action:b.dataset.svc});setTimeout(refresh,2500)});
$("#savekey").onclick=async()=>{const m=$("#keymsg");m.className="msg";m.textContent="Storing…";const r=await post("/api/key",{key:$("#key").value});$("#key").value="";
  if(r.ok){m.textContent="Stored. The service is starting; the checks update in a few seconds.";setTimeout(refresh,7000)}else{m.className="msg err";m.textContent=r.error}};
$("#savewifi").onclick=async()=>{const m=$("#wifimsg");m.className="msg";m.textContent="Storing and signing in… (up to 30 seconds)";
  const r=await post("/api/wifi",{user:$("#wuser").value,password:$("#wpass").value});$("#wpass").value="";
  m.className="msg"+(r.ok?"":" err");m.textContent=r.message||"No answer.";if(r.ok)$("#wuser").value="";setTimeout(refresh,3000)};
const volWarn=()=>{const v=+$("#vol").value;$("#volv").textContent=v+" %"+(v>40?" — the device will hear itself; “stop” and “Hey ANVE” work less well while it talks":"");$("#volv").style.color=v>40?"var(--warn)":""};
$("#vol").oninput=()=>{volTouched=true;volWarn()};
$("#setvol").onclick=async()=>{await post("/api/volume",{volume:$("#vol").value/100});volTouched=false;refresh()};
$("#scan").onclick=async()=>{$("#bt").innerHTML='<p class="d">Searching for 8 seconds…</p>';const r=await (await fetch("/api/bluetooth")).json();
  $("#bt").innerHTML=r.devices.length?r.devices.map(d=>`<div class="row"><div class="grow"><div class="t">${esc(d.name)}</div><div class="d">${esc(d.mac)}${d.paired?" · already paired":""}</div></div><button data-mac="${esc(d.mac)}">Use this speaker</button></div>`).join(""):'<p class="d">No named Bluetooth devices nearby.</p>';
  document.querySelectorAll("button[data-mac]").forEach(b=>b.onclick=()=>runJob("/api/pair",{mac:b.dataset.mac}))};
$("#showlog").onclick=async()=>{const r=await (await fetch("/api/logs")).json();$("#log").hidden=false;$("#log").textContent=r.lines.join("\n")||"No log yet.";$("#log").scrollTop=1e6};
refresh();pollJob();setInterval(refresh,8000);
</script></body></html>
"""


def main():
    ap = argparse.ArgumentParser(description="ANVE speaker setup page")
    ap.add_argument("--port", type=int, default=8737)
    a = ap.parse_args()
    os.environ.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(f"ANVE setup page on http://127.0.0.1:{a.port} (this machine only)", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
