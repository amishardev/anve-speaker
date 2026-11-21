#!/usr/bin/env python3
"""A stand-in for the ANVE platform, for testing the app's sign-in and Approvals tab
without touching a real account or real pending approvals.

    python scripts/mock_anve.py [--port 8790]

In the app (Sign in -> "change" under the password): ANVE address  http://10.0.2.2:8790
from the Android emulator, or (started with --host 0.0.0.0) http://<this computer's
address>:8790 from a phone.
Test account:  demo / demo-pass-1   (exists only in this script)

It serves the same routes, shapes and status codes the app uses on the real platform
(server/anve/routers/auth_r.py, assistant_r.py, agents_r.py):
    POST /api/auth/login, GET /api/auth/me, POST /api/auth/logout
    GET  /api/assistant/pending
    POST /api/assistant/confirmations/{id}      {"approved": bool}
    POST /api/agents/runs/{run_id}/decision     {"approved": bool}
    GET/POST /api/assistant/devices, DELETE /api/assistant/devices/{id}
Type a line + Enter here while it runs:   c = a new voice confirmation,  a = a new agent
approval,  x = expire the session (the app must ask for sign-in again)."""
import argparse
import json
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

USER = {"id": "u1", "username": "demo", "name": "Demo Person", "role": "user", "apps": ["agents"]}
PASSWORD = "demo-pass-1"
SESSIONS: set = set()
CONFIRMATIONS: dict = {}
APPROVALS: dict = {}
DEVICES: dict = {}
LOG: list = []
REHEARSAL = ""          # --setup-base: a stand-in box for rehearsing first-time setup


def add_confirmation():
    cid = secrets.token_hex(6)
    CONFIRMATIONS[cid] = {"id": cid, "action": "send_email", "status": "pending", "session_id": "s1",
                          "readback": "Send an email to Priya Nair with the subject \"Friday review\": "
                                      "\"Can we move the review to 4 pm?\"",
                          "created_at": time.time(), "expires_at": time.time() + 300}
    return cid


def add_approval():
    rid = secrets.token_hex(8)
    APPROVALS[rid] = {"run_id": rid, "agent": "Client compliance calendar", "step": "draft", "what": "Draft an email",
                      "message": "The agent wants to draft the monthly GST reminder for 14 clients.",
                      "args": json.dumps({"to": "clients-gst@example.com", "subject": "GST filing due on the 20th",
                                          "body": "Dear client, this is a reminder that ..."}, indent=2),
                      "args_sha256": "0" * 64, "mode": "live", "trigger": "schedule"}
    return rid


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, obj, cookie=None):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        if cookie is not None:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return {}

    def _signed_in(self):
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "anve_session" and v in SESSIONS:
                return True
        return False

    def do_GET(self):
        p = self.path.split("?")[0]
        if not self._signed_in():
            return self._send(401, {"detail": "Not signed in"})
        if p == "/api/auth/me":
            user = dict(USER, rehearsal_box=REHEARSAL) if REHEARSAL else USER
            return self._send(200, {"user": user, "apps": []})
        if p == "/api/assistant/pending":
            now = time.time()
            for c in CONFIRMATIONS.values():
                if c["status"] == "pending" and c["expires_at"] < now:
                    c["status"] = "expired"
            return self._send(200, {"confirmations": [c for c in CONFIRMATIONS.values() if c["status"] == "pending"],
                                    "approvals": list(APPROVALS.values())})
        if p == "/api/assistant/devices":
            return self._send(200, list(DEVICES.values()))
        self._send(404, {"detail": "Not found"})

    def do_POST(self):
        p, b = self.path.split("?")[0], self._body()
        if p == "/api/auth/login":
            if str(b.get("username", "")).strip().lower() == USER["username"] and b.get("password") == PASSWORD:
                sid = secrets.token_urlsafe(24)
                SESSIONS.add(sid)
                return self._send(200, {"ok": True}, f"anve_session={sid}; HttpOnly; Path=/; SameSite=lax; Max-Age=1209600")
            return self._send(401, {"detail": "Wrong username or password"})
        if p == "/_test/confirmation":          # for scripted tests: something new is waiting
            return self._send(200, {"id": add_confirmation()})
        if p == "/_test/approval":
            return self._send(200, {"id": add_approval()})
        if p == "/api/auth/logout":
            SESSIONS.clear()
            return self._send(200, {"ok": True}, "anve_session=; Path=/; Max-Age=0")
        if not self._signed_in():
            return self._send(401, {"detail": "Not signed in"})
        if p.startswith("/api/assistant/confirmations/"):
            c = CONFIRMATIONS.get(p.rsplit("/", 1)[1])
            if not c:
                return self._send(404, {"detail": "No such confirmation"})
            if c["status"] != "pending" or c["expires_at"] < time.time():
                return self._send(409, {"detail": f"This confirmation is already {c['status']}"})
            c["status"] = "done" if b.get("approved") else "cancelled"
            LOG.append((c["id"], c["status"]))
            print(f"  confirmation {c['id']}: {c['status']}", flush=True)
            return self._send(200, {"ok": True, "summary": "Email sent." if b.get("approved") else "Cancelled.", "status": c["status"]})
        if p.startswith("/api/agents/runs/") and p.endswith("/decision"):
            rid = p.split("/")[4]
            if rid not in APPROVALS:
                return self._send(409, {"detail": "This run is not waiting for approval"})
            a = APPROVALS.pop(rid)
            LOG.append((rid, "approved" if b.get("approved") else "declined"))
            print(f"  agent run {rid}: {'approved' if b.get('approved') else 'declined'}", flush=True)
            return self._send(200, {"id": rid, "status": "running" if b.get("approved") else "cancelled", "agent_name": a["agent"]})
        if p == "/api/assistant/devices":
            name, kind = str(b.get("name") or "").strip(), b.get("kind", "room")
            if not name or kind not in ("room", "personal"):
                return self._send(400, {"detail": "A device needs a name and a kind"})
            did = secrets.token_hex(6)
            DEVICES[did] = {"id": did, "owner_id": "u1", "name": name, "kind": kind, "prefix": "anvd_mock", "created_at": time.time(),
                            "last_seen_at": 0, "revoked_at": 0}
            print(f"  device created: {name}", flush=True)
            return self._send(200, {"device": DEVICES[did], "key": "anvd_mock_" + secrets.token_hex(20), "note": "Shown once."})
        self._send(404, {"detail": "Not found"})

    def do_DELETE(self):
        if not self._signed_in():
            return self._send(401, {"detail": "Not signed in"})
        did = self.path.rsplit("/", 1)[1]
        if DEVICES.pop(did, None) is None:
            return self._send(404, {"detail": "No such device, or it is already revoked"})
        print(f"  device revoked: {did}", flush=True)
        self._send(200, {"ok": True})


def keys():
    for line in sys.stdin:
        k = line.strip().lower()
        if k == "c":
            print("  new confirmation", add_confirmation(), flush=True)
        elif k == "a":
            print("  new agent approval", add_approval(), flush=True)
        elif k == "x":
            SESSIONS.clear()
            print("  all sessions ended", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--host", default="127.0.0.1", help="0.0.0.0 to reach it from a real phone")
    ap.add_argument("--empty", action="store_true", help="start with nothing waiting")
    ap.add_argument("--setup-base", default="", help="address of a stand-in box (provision.py --dry-run) for rehearsing setup")
    a = ap.parse_args()
    global REHEARSAL
    REHEARSAL = a.setup_base
    if not a.empty:
        add_confirmation()
        add_approval()
    threading.Thread(target=keys, daemon=True).start()
    print(f"mock ANVE on port {a.port}: sign in as demo / {PASSWORD}", flush=True)
    ThreadingHTTPServer((a.host, a.port), H).serve_forever()


if __name__ == "__main__":
    main()
