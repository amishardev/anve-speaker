"""The line between the box link and the speaker (protocol 2, the team's P4 handover).

The box link (boxlink.py, service anve-boxlink) holds the connection to the ANVE Box cloud;
the speaker (main.py, service anve-device) has the microphone, the speaker, the buttons and
the talks. They are two programs on purpose: the box stays online and can be added to an
account even if the speaker breaks. Protocol 2 needs them to talk, so the box link opens a
local socket and the speaker connects to it.

    /run/anvebox/speaker.sock      the box link's RuntimeDirectory, this user only (0700)

One JSON object a line, both ways:

    box link -> speaker
        {"type": "command", "name": "listen" | "play" | "stop" | "record", "args": {...}}
        {"type": "tool", "name": "set_volume", "arguments": {...}}
    speaker -> box link
        {"type": "report", "activity": "idle", "volume": 70, "muted": false}
        {"type": "event", "name": "button.long" | "wake"}
        {"type": "tools", "tools": [MCP tool descriptions], "answers": {"name": "words {arg}"}}

Never sent on it: sound, what was said, keys. The address may also be "tcp:127.0.0.1:PORT",
for tests on a computer without Unix sockets.
"""
import asyncio
import contextlib
import json
import logging
import os
from pathlib import Path

log = logging.getLogger("anve.speakerlink")
SOCKET = "/run/anvebox/speaker.sock"
LINE_MAX = 64 * 1024


def _tcp(address: str):
    """("127.0.0.1", 8742) for "tcp:127.0.0.1:8742", else None."""
    if not address.startswith("tcp:"):
        return None
    host, _, port = address[4:].rpartition(":")
    return host or "127.0.0.1", int(port)


def _decode(line: bytes):
    try:
        message = json.loads(line)
    except ValueError:
        return None
    return message if isinstance(message, dict) and isinstance(message.get("type"), str) else None


class _Line:
    """One connection: write lines, read lines. Writing never waits and never raises."""

    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer

    def send(self, message: dict) -> bool:
        if self.writer.is_closing():
            return False
        try:
            self.writer.write(json.dumps(message, separators=(",", ":")).encode() + b"\n")
            return True
        except (OSError, RuntimeError):
            return False

    async def lines(self):
        while True:
            try:
                line = await self.reader.readuntil(b"\n")
            except asyncio.LimitOverrunError:
                log.warning("a line over %d bytes: the connection is closed", LINE_MAX)
                return
            except (asyncio.IncompleteReadError, ConnectionError, OSError):
                return
            message = _decode(line)
            if message is not None:
                yield message

    def close(self):
        with contextlib.suppress(Exception):
            self.writer.close()


class SpeakerPort:
    """The box link's end: waits for the speaker. One speaker at a time; a new connection
    replaces the old one (the speaker restarted)."""

    def __init__(self, address: str = SOCKET, on_message=None, on_change=None):
        self.address = address
        self.on_message = on_message or (lambda message: None)
        self.on_change = on_change or (lambda connected: None)
        self.server = None
        self.line: _Line | None = None
        self.port = 0                                      # the TCP port, for tests

    @property
    def connected(self) -> bool:
        return self.line is not None and not self.line.writer.is_closing()

    async def start(self):
        tcp = _tcp(self.address)
        if tcp:
            self.server = await asyncio.start_server(self._accept, tcp[0], tcp[1], limit=LINE_MAX)
            self.port = self.server.sockets[0].getsockname()[1]
        else:
            path = Path(self.address)
            path.parent.mkdir(parents=True, exist_ok=True)
            with contextlib.suppress(FileNotFoundError):
                path.unlink()                              # left by an earlier run
            old = os.umask(0o177)
            try:
                self.server = await asyncio.start_unix_server(self._accept, str(path), limit=LINE_MAX)
            finally:
                os.umask(old)
            os.chmod(path, 0o600)
        log.info("waiting for the speaker on %s", self.address if not tcp else f"port {self.port}")

    async def stop(self):
        if self.line:
            self.line.close()
        if self.server:
            self.server.close()
            with contextlib.suppress(Exception):
                await self.server.wait_closed()

    def send(self, message: dict) -> bool:
        return self.connected and self.line.send(message)

    async def _accept(self, reader, writer):
        if self.line:
            self.line.close()
        line = self.line = _Line(reader, writer)
        log.info("the speaker is connected")
        self._changed(True)
        try:
            async for message in line.lines():
                try:
                    self.on_message(message)
                except Exception:                          # noqa: BLE001  never breaks the link
                    log.exception("could not take the speaker's %s", message.get("type"))
        finally:
            line.close()
            if self.line is line:
                self.line = None
                log.info("the speaker is gone")
                self._changed(False)

    def _changed(self, connected: bool):
        try:
            self.on_change(connected)
        except Exception:                                  # noqa: BLE001
            log.exception("on_change failed")


class SpeakerLink:
    """The speaker's end: connects to the box link, and again whenever it restarts."""

    def __init__(self, address: str = SOCKET, on_message=None, on_change=None, retry_s: float = 2.0):
        self.address = address
        self.on_message = on_message or (lambda message: None)   # may be a coroutine function
        self.on_change = on_change or (lambda connected: None)
        self.retry_s = retry_s
        self.line: _Line | None = None
        self._said_missing = False
        self._last: dict = {}                              # resent on every new connection

    @property
    def connected(self) -> bool:
        return self.line is not None and not self.line.writer.is_closing()

    def send(self, message: dict) -> bool:
        if message.get("type") in ("report", "tools"):
            self._last[message["type"]] = message
        return self.connected and self.line.send(message)

    async def run(self):
        while True:
            try:
                tcp = _tcp(self.address)
                if tcp:
                    reader, writer = await asyncio.open_connection(tcp[0], tcp[1], limit=LINE_MAX)
                else:
                    reader, writer = await asyncio.open_unix_connection(self.address, limit=LINE_MAX)
            except (OSError, ConnectionError):
                if not self._said_missing:
                    log.info("the box link isn't there (%s): trying again every %.0f s", self.address, self.retry_s)
                    self._said_missing = True
                await asyncio.sleep(self.retry_s)
                continue
            self._said_missing = False
            line = self.line = _Line(reader, writer)
            log.info("connected to the box link: protocol 2 is on")
            for kind in ("tools", "report"):
                if kind in self._last:
                    line.send(self._last[kind])
            await self._changed(True)
            async for message in line.lines():
                try:
                    result = self.on_message(message)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:                          # noqa: BLE001
                    log.exception("could not do the box link's %s", message.get("type"))
            line.close()
            self.line = None
            log.info("the box link went away")
            await self._changed(False)
            await asyncio.sleep(self.retry_s)

    async def _changed(self, connected: bool):
        try:
            result = self.on_change(connected)
            if asyncio.iscoroutine(result):
                await result
        except Exception:                                  # noqa: BLE001
            log.exception("on_change failed")
