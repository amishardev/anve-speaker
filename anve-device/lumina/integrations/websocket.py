"""The dashboard's server: a few files, a small REST API and a WebSocket, in one place.

    GET  /                      the dashboard
    GET  /api/status            everything about the current state
    POST /api/event             {"name": "ai.thinking", "meta": {}}     a real event
    POST /api/state             {"state": "listening"}                  a real state
    POST /api/demo              {"state": "SPEAKING", "seconds": 20} or {"off": true}
    POST /api/settings          {"brightness": 0.1, "theme": "cyberpunk", ...}
    GET  /ws                    WebSocket: live frames and status; accepts the same
                                messages as the POSTs ({"type": "event", ...})

Everything but the dashboard's own files needs the key (header X-Lumina-Key, or the first
WebSocket message {"type": "auth", "key": ...}); programs on this machine itself do not.
Written on asyncio streams with nothing but the standard library.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import struct
import time
import logging
from pathlib import Path

log = logging.getLogger("lumina")
WEB = Path(__file__).resolve().parent.parent / "dashboard"
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".svg": "image/svg+xml"}
_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
HEADERS = ("X-Content-Type-Options: nosniff\r\nCache-Control: no-store\r\nReferrer-Policy: no-referrer\r\n"
           "Content-Security-Policy: default-src 'self'; connect-src 'self' ws: wss:; style-src 'self' 'unsafe-inline'\r\n")


def ws_frame(text: str) -> bytes:
    data = text.encode("utf-8")
    n = len(data)
    head = bytes([0x81, n]) if n < 126 else bytes([0x81, 126]) + struct.pack(">H", n) if n < 65536 \
        else bytes([0x81, 127]) + struct.pack(">Q", n)
    return head + data


async def ws_read(reader) -> tuple:
    """(opcode, payload) of the next frame from a browser; raises on anything oversized."""
    b0, b1 = await reader.readexactly(2)
    n = b1 & 0x7F
    if n == 126:
        n = struct.unpack(">H", await reader.readexactly(2))[0]
    elif n == 127:
        n = struct.unpack(">Q", await reader.readexactly(8))[0]
    if n > 65536:
        raise ValueError("frame too large")
    mask = await reader.readexactly(4) if b1 & 0x80 else b""
    data = await reader.readexactly(n)
    if mask:
        data = bytes(c ^ mask[i % 4] for i, c in enumerate(data))
    return b0 & 0x0F, data


class Dashboard:
    def __init__(self, controller, key: str, host: str = "0.0.0.0", port: int = 8742):
        self.c, self.key, self.host, self.port = controller, key, host, port
        self.clients: set = set()
        self._server = None
        self._fails: dict = {}                 # address -> (count, locked until)
        self._sent_px = ""
        self._sent_status_at = 0.0
        self._sent_visual = None

    async def start(self):
        self._server = await asyncio.start_server(self._client, self.host, self.port)
        return self

    async def stop(self):
        if self._server:
            self._server.close()
            for w in list(self.clients):
                w.close()

    # ---------- who may ----------

    def _allowed(self, peer: str, key: str | None) -> bool:
        if peer in ("127.0.0.1", "::1"):
            return True
        count, until = self._fails.get(peer, (0, 0.0))
        now = time.monotonic()
        if now < until:
            return False
        if key and hmac.compare_digest(key.encode(), self.key.encode()):
            self._fails.pop(peer, None)
            return True
        if key:                                 # a wrong key: five of them lock that address out for a while
            count += 1
            self._fails[peer] = (0, now + 30.0) if count >= 5 else (count, 0.0)
            if len(self._fails) > 256:
                self._fails.clear()
        return False

    # ---------- commands (the same from POST and from the WebSocket) ----------

    def command(self, kind: str, body: dict) -> dict:
        c = self.c
        if kind == "event":
            ok = c.bus.publish({"type": "event", "name": str(body.get("name", "")), "source": "api",
                                "meta": body.get("meta") if isinstance(body.get("meta"), dict) else {}})
            return {"ok": ok}
        if kind == "state":
            return {"ok": c.bus.publish({"type": "state", "state": str(body.get("state", "")), "source": "api", "meta": {}})}
        if kind == "status":
            st = body.get("status")
            return {"ok": isinstance(st, dict) and c.bus.publish({"type": "status", "status": st, "source": "api"})}
        if kind == "demo":
            if body.get("off"):
                return {"ok": c.state.demo(None)}
            return {"ok": c.state.demo(str(body.get("state", "")), float(body.get("seconds", 20) or 20))}
        if kind == "settings":
            return {"ok": True, "changed": c.apply_settings(body)}
        return {"ok": False, "error": "unknown command"}

    # ---------- HTTP ----------

    async def _client(self, reader, writer):
        peer = (writer.get_extra_info("peername") or ("?",))[0]
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
            if len(head) > 16384:
                return
            lines = head.decode("latin-1").split("\r\n")
            method, target, _ = lines[0].split(" ", 2)
            hdr = {k.strip().lower(): v.strip() for k, v in (l.split(":", 1) for l in lines[1:] if ":" in l)}
            path = target.split("?", 1)[0]
            if path == "/ws" and hdr.get("upgrade", "").lower() == "websocket":
                # A web page from somewhere else must not be able to talk to LUMINA through
                # a browser on this machine (browsers always say where a page came from).
                origin = hdr.get("origin", "")
                if origin and origin.split("://", 1)[-1] != hdr.get("host", ""):
                    log.warning("dashboard: refused a WebSocket from another origin (%s, host %s)", origin[:80], hdr.get("host", "")[:80])
                    return await self._send(writer, 403, {"error": "wrong origin"})
                await self._websocket(reader, writer, peer, hdr)
                return
            body = b""
            n = int(hdr.get("content-length", "0") or 0)
            if n > 65536:
                return await self._send(writer, 413, {"error": "too large"})
            if n:
                body = await asyncio.wait_for(reader.readexactly(n), 10)
            await self._route(writer, peer, method, path, hdr, body)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError, ConnectionError, ValueError):
            pass
        finally:
            try:
                writer.close()
            except Exception:                        # noqa: BLE001
                pass

    async def _route(self, writer, peer, method, path, hdr, body):
        if method == "GET" and not path.startswith("/api/"):
            name = "index.html" if path == "/" else path.lstrip("/")
            f = WEB / name
            if "/" in name or "\\" in name or ".." in name or f.suffix not in TYPES or not f.is_file():
                return await self._send(writer, 404, {"error": "not found"})
            return await self._send(writer, 200, f.read_bytes(), TYPES[f.suffix])
        if not self._allowed(peer, hdr.get("x-lumina-key")):
            return await self._send(writer, 401, {"error": "key needed"})
        if method == "GET" and path == "/api/status":
            return await self._send(writer, 200, self.c.status())
        if method == "POST" and path.startswith("/api/"):
            # a JSON content type cannot be sent by a plain web form from another site
            if not hdr.get("content-type", "").lower().startswith("application/json"):
                return await self._send(writer, 415, {"error": "send JSON"})
            try:
                data = json.loads(body.decode("utf-8") or "{}")
                if not isinstance(data, dict):
                    raise ValueError
            except (ValueError, UnicodeDecodeError):
                return await self._send(writer, 400, {"error": "not JSON"})
            out = self.command(path[len("/api/"):], data)
            return await self._send(writer, 200 if out.get("ok") else 422, out)
        await self._send(writer, 404, {"error": "not found"})

    @staticmethod
    async def _send(writer, code: int, body, ctype: str = "application/json"):
        if not isinstance(body, (bytes, bytearray)):
            body = json.dumps(body).encode("utf-8")
        reason = {200: "OK", 400: "Bad Request", 401: "Unauthorized", 403: "Forbidden", 404: "Not Found", 413: "Too Large",
                  415: "Unsupported", 422: "Unprocessable"}.get(code, "OK")
        writer.write(f"HTTP/1.1 {code} {reason}\r\nContent-Type: {ctype}\r\nContent-Length: {len(body)}\r\n"
                     f"Connection: close\r\n{HEADERS}\r\n".encode("latin-1") + body)
        await writer.drain()

    # ---------- WebSocket ----------

    async def _websocket(self, reader, writer, peer, hdr):
        accept = base64.b64encode(hashlib.sha1((hdr.get("sec-websocket-key", "") + _GUID).encode()).digest()).decode()
        writer.write(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                      f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode("latin-1"))
        await writer.drain()
        authed = self._allowed(peer, None)
        if authed:
            self._join(writer)
        try:
            while True:
                op, data = await ws_read(reader)
                if op == 0x8:
                    break
                if op == 0x9:
                    writer.write(bytes([0x8A, len(data)]) + data if len(data) < 126 else b"")
                    continue
                if op != 0x1:
                    continue
                try:
                    m = json.loads(data.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    continue
                if not isinstance(m, dict):
                    continue
                if m.get("type") == "auth":
                    authed = self._allowed(peer, str(m.get("key", "")))
                    writer.write(ws_frame(json.dumps({"t": "auth", "ok": authed})))
                    if authed:
                        self._join(writer)
                elif authed:
                    out = self.command(str(m.get("type", "")), m)
                    writer.write(ws_frame(json.dumps(dict(out, t="reply", to=m.get("type")))))
        finally:
            self.clients.discard(writer)

    def _join(self, writer):
        self.clients.add(writer)
        writer.write(ws_frame(json.dumps(dict(self.c.status(), t="s"))))
        if self._sent_px:
            writer.write(ws_frame(json.dumps({"t": "f", "px": self._sent_px})))

    def broadcast(self, preview: str, now: float):
        """Called by the controller after each frame: sends what changed, at a sane rate."""
        if not self.clients:
            self._sent_px = preview
            return
        out = []
        visual = self.c.state.visual()
        if visual != self._sent_visual or now - self._sent_status_at >= 1.0:
            self._sent_visual, self._sent_status_at = visual, now
            out.append(ws_frame(json.dumps(dict(self.c.status(), t="s"))))
        if preview != self._sent_px:
            self._sent_px = preview
            out.append(ws_frame(json.dumps({"t": "f", "px": preview})))
        if not out:
            return
        data = b"".join(out)
        for w in list(self.clients):
            try:
                if w.transport.get_write_buffer_size() > 262144:     # a browser that stopped reading
                    w.close()
                    self.clients.discard(w)
                else:
                    w.write(data)
            except Exception:                    # noqa: BLE001
                self.clients.discard(w)
