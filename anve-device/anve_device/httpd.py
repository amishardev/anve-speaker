"""A very small HTTP/1.1 server on asyncio, for the two JSON APIs the phone app talks to
(appapi.py in the running speaker, provision.py during first-time setup).

It is deliberately narrow, because it faces the local network: fixed routes, JSON in and
out, small size limits, one request per connection, a cap on open connections and a
timeout on everything a client sends.

The setup program also serves its setup page with it (provision.py): for that, a handler
may return a `Raw` (a file, a page, a redirect) instead of a dict, and a server may have
one `fallback` handler for GET/HEAD requests that match no route. A server without a
fallback (the running speaker's API) answers exactly as before."""
import asyncio
import json
import logging
from urllib.parse import parse_qsl, urlsplit

log = logging.getLogger("anve.http")

MAX_HEAD = 8192
MAX_BODY = 32768
MAX_OPEN = 48
REASONS = {200: "OK", 202: "Accepted", 204: "No Content", 302: "Found", 400: "Bad Request", 401: "Unauthorized",
           403: "Forbidden", 404: "Not Found", 405: "Method Not Allowed", 409: "Conflict",
           413: "Payload Too Large", 429: "Too Many Requests", 500: "Internal Server Error",
           503: "Service Unavailable"}


class HttpError(Exception):
    def __init__(self, status: int, error: str, **extra):
        super().__init__(error)
        self.status, self.body = status, {"ok": False, "error": error, **extra}


class Raw:
    """An answer that is not JSON: a page, a file, or a redirect (`location`)."""
    __slots__ = ("status", "body", "ctype", "location")

    def __init__(self, status: int = 200, body: bytes = b"", ctype: str = "text/plain; charset=utf-8",
                 location: str = ""):
        self.status, self.body, self.ctype, self.location = status, body, ctype, location


class Request:
    __slots__ = ("method", "target", "path", "query", "headers", "body", "peer", "local")

    def json(self) -> dict:
        if not self.body:
            return {}
        try:
            obj = json.loads(self.body)
        except ValueError:
            raise HttpError(400, "bad_json") from None
        if not isinstance(obj, dict):
            raise HttpError(400, "bad_json")
        return obj


class Server:
    def __init__(self):
        self.routes: dict = {}
        self.fallback = None              # optional: for GET/HEAD requests that match no route
        self._srv = None
        self._open = 0

    def route(self, method: str, path: str, handler):
        """`handler(request)` is a coroutine that returns a dict (sent as 200) or
        `(status, dict)`, or raises HttpError."""
        self.routes[(method, path)] = handler

    async def start(self, host: str, port: int):
        self._srv = await asyncio.start_server(self._conn, host, port, reuse_address=True,
                                               limit=MAX_HEAD)

    @property
    def running(self) -> bool:
        return self._srv is not None

    async def stop(self):
        s, self._srv = self._srv, None
        if s:
            s.close()
            try:
                await asyncio.wait_for(s.wait_closed(), 2)
            except (asyncio.TimeoutError, OSError):
                pass

    async def _read(self, r, peer, local) -> Request:
        head = await r.readuntil(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        parts = lines[0].split(" ")
        if len(parts) != 3 or not parts[2].startswith("HTTP/1."):
            raise HttpError(400, "bad_request")
        q = Request()
        q.method, q.target = parts[0].upper(), parts[1]
        u = urlsplit(q.target)
        q.path, q.query = u.path, dict(parse_qsl(u.query))
        q.headers = {}
        for line in lines[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                q.headers[k.strip().lower()] = v.strip()
        q.peer, q.local = peer, local
        try:
            n = int(q.headers.get("content-length") or 0)
        except ValueError:
            raise HttpError(400, "bad_request") from None
        if n < 0 or n > MAX_BODY:
            raise HttpError(413, "too_large")
        q.body = await r.readexactly(n) if n else b""
        return q

    async def _conn(self, r: asyncio.StreamReader, w: asyncio.StreamWriter):
        if self._open >= MAX_OPEN:
            w.close()
            return
        self._open += 1
        status, body = 500, {"ok": False, "error": "internal"}
        head_only = False
        try:
            peer =(w.get_extra_info("peername") or ("?", 0))[0]
            local = (w.get_extra_info("sockname") or ("?", 0))[0]
            try:
                q = await asyncio.wait_for(self._read(r, peer, local), 10)
                h = self.routes.get((q.method, q.path))
                if h is None and self.fallback is not None and q.method in ("GET", "HEAD"):
                    h, head_only = self.fallback, q.method == "HEAD"
                if h is None:
                    known = any(p == q.path for _, p in self.routes)
                    raise HttpError(405 if known else 404, "method" if known else "not_found")
                out = await h(q)
                status, body = out if isinstance(out, tuple) else (200, out)
            except HttpError as e:
                status, body = e.status, e.body
            except (asyncio.IncompleteReadError, asyncio.LimitOverrunError,
                    asyncio.TimeoutError, UnicodeError):
                return
            except Exception:                                # noqa: BLE001
                log.exception("request failed")
                status, body = 500, {"ok": False, "error": "internal"}
            if isinstance(body, Raw):
                # a page, a file or a redirect. nosniff: a file is only ever what we say it is
                status, data, ctype = body.status, body.body, body.ctype
                extra = "X-Content-Type-Options: nosniff\r\n"
                if body.location:
                    extra += f"Location: {body.location}\r\n"
            else:
                data = b"" if status == 204 else json.dumps(body, separators=(",", ":")).encode()
                ctype, extra = "application/json", ""
            head = (f"HTTP/1.1 {status} {REASONS.get(status, 'OK')}\r\n"
                    f"Content-Type: {ctype}\r\n"
                    f"Content-Length: {len(data)}\r\n{extra}"
                    "Cache-Control: no-store\r\nConnection: close\r\n\r\n").encode()
            w.write(head if head_only else head + data)
            await asyncio.wait_for(w.drain(), 10)
            # Let the client close first (it does, once it has read Content-Length bytes):
            # the polite order, and harmless. It was added while chasing answers that
            # arrived one byte short in the Android emulator (18 Nov 2026), and did NOT
            # cure that: a control against an unrelated server on the laptop lost the same
            # byte, so that fault is the emulator's network path, not this server. From a
            # computer on the same Wi-Fi, 20 of 20 answers were complete before and after.
            try:
                await asyncio.wait_for(r.read(1), 1.5)
            except (asyncio.TimeoutError, asyncio.LimitOverrunError):
                pass
        except (ConnectionError, asyncio.TimeoutError, OSError):
            pass
        finally:
            self._open -= 1
            try:
                w.close()
            except Exception:                                # noqa: BLE001
                pass
