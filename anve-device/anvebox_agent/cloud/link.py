"""The box's side of the box link: one connection to the cloud, kept open.

Implements part A of docs/box-cloud-agreement.md. To the agent it is a
CloudLink: check() answers whether ANVE can be reached, through the listener.
It also carries the box's status and events up to the cloud, and the cloud's
commands and owner changes down to the box, with the claim secret's hash going
up whenever the box makes a new one (milestone 2.2).
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable, Protocol

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidURI

from ..identity import Identity

log = logging.getLogger("anvebox.link")

PROTOCOL = 1
MAX_MESSAGE = 16 * 1024
UNKNOWN_BOX = 4401


@dataclass(frozen=True)
class LinkTimings:
    first_retry: float = 1.0
    max_retry: float = 60.0
    dead_after: float = 90.0  # no message from the cloud for this long: the line is dead
    open_timeout: float = 10.0
    poll: float = 1.0  # how often the open link re-checks the network gate and the dead timer
    nudge_spacing: float = 5.0  # a check() can bring a retry forward, but no more often than this


class AgentPort(Protocol):
    """What the link needs from the agent."""

    def status(self) -> dict: ...
    def run_command(self, name: str, args: dict) -> tuple[bool, str | None]: ...
    def add_observer(self, observer: Callable[[str, dict], None]) -> None: ...


class ClaimPort(Protocol):
    """What the link needs from the claim layer (CloudClaim)."""

    def hello_fields(self) -> dict: ...
    def on_welcome(self, sent: dict) -> None: ...
    def on_owner(self, claimed: bool, rotate: bool) -> str | None: ...


class LinkProtocolError(Exception):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _printable(reason: str) -> str:
    """The reason the cloud gave for closing, safe to put in one log line."""
    return "".join(ch if ch.isprintable() else " " for ch in reason)[:123]


class WebSocketLink:
    def __init__(
        self,
        *,
        url: str,
        identity: Identity,
        software: str,
        gate: Callable[[], bool] = lambda: True,
        timings: LinkTimings = LinkTimings(),
        on_unknown_box: Callable[[], Awaitable[bool]] | None = None,
    ) -> None:
        """gate() says whether the box's own network can reach the cloud at all.
        On a real box it is always true. The simulator uses it so the real link
        follows the pretend network (router off, network blocking ANVE)."""
        self.url = url
        self.identity = identity
        self.software = software
        self.t = timings
        self._gate = gate
        self._on_unknown_box = on_unknown_box
        self._listener: Callable[[bool], None] = lambda reachable: None
        self._claim: ClaimPort | None = None
        self._agent: AgentPort | None = None
        self._task: asyncio.Task | None = None
        self._wake: asyncio.Event | None = None
        self._outbox: asyncio.Queue | None = None
        self._connected = False
        self._redo_hello = False
        self._reported: bool | None = None
        self._last_status: dict | None = None
        self._retry = timings.first_retry
        self._last_attempt = float("-inf")
        self.state = "stopped"  # stopped | waiting | connecting | connected
        self.last_problem: str | None = None

    # ------------------------------------------------------------ CloudLink, for the agent

    def listen(self, listener: Callable[[bool], None]) -> None:
        self._listener = listener

    def check(self) -> None:
        """Answer at once from what the link knows. When it is down, also bring
        the next attempt forward (the network may just have come back)."""
        loop = asyncio.get_running_loop()
        loop.call_soon(self._tell_listener, self._connected)
        if not self._connected and self._wake is not None and self._gate():
            self._wake.set()

    def cancel(self) -> None:
        pass  # answers are immediate; nothing is ever left pending

    # ------------------------------------------------------------ wiring

    def attach_claim(self, claim: ClaimPort) -> None:
        self._claim = claim

    def reconnect(self) -> None:
        """Close the open connection and make a new one, so the cloud hears the
        box's news (a factory reset) in a fresh hello. Harmless when down."""
        self._redo_hello = True

    def attach(self, agent: AgentPort) -> None:
        self._agent = agent
        agent.add_observer(self._on_agent)

    def start(self) -> None:
        if self._task is None:
            self._wake = asyncio.Event()
            self.state = "waiting"
            self._task = asyncio.get_running_loop().create_task(self._run())

    def kill(self) -> None:
        """Stop for good (power cut in the simulator). The old agent hears nothing more."""
        self._listener = lambda reachable: None
        self._claim = None
        self._agent = None
        if self._task is not None:
            self._task.cancel()
            self._task = None
        self.state = "stopped"

    async def stop(self) -> None:
        task = self._task
        self.kill()
        if task is not None:
            try:
                await task
            except asyncio.CancelledError:
                pass

    # ------------------------------------------------------------ the loop

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        delay = 0.0
        while True:
            try:
                if delay > 0:
                    await self._wait(delay)
                if not self._gate():
                    self.state = "waiting"
                    delay = self.t.poll
                    continue
                self.state = "connecting"
                self._last_attempt = loop.time()
                outcome = await self._session()
            except Exception:
                # A mistake here, or in what the link calls (the agent, the claim
                # layer, a hardware slot), must never stop the link for good: it
                # would sit silently offline until the program restarts. Say so
                # in the log, and try again as after any failed attempt.
                log.exception("the link to the cloud hit an unexpected error; it will try again")
                self.last_problem = "an unexpected error in the box software (see its log)"
                outcome = "failed"
            self.state = "waiting"
            if outcome == "registered":
                delay = 0.0
                continue
            if outcome == "ended":  # it was up, then dropped: try again soon
                self._retry = self.t.first_retry
                delay = self.t.first_retry
            else:
                delay = self._retry
                self._retry = min(self._retry * 2, self.t.max_retry)
            delay *= random.uniform(0.8, 1.2)

    async def _wait(self, seconds: float) -> None:
        """Sleep, waking early when nudged, but never sooner than nudge_spacing
        after the last attempt."""
        loop = asyncio.get_running_loop()
        end = loop.time() + seconds
        while True:
            remaining = end - loop.time()
            if remaining <= 0:
                return
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                return
            earliest = self._last_attempt + self.t.nudge_spacing
            if loop.time() >= earliest:
                self._retry = self.t.first_retry
                return
            end = min(end, earliest)

    async def _session(self) -> str:
        """One connection, from opening to closing. Returns what happened:
        "ended" (it was up), "failed", or "registered" (the cloud didn't know
        the box, and it has now been registered)."""
        try:
            async with connect(
                self.url,
                open_timeout=self.t.open_timeout,
                ping_interval=None,  # protocol 1 has its own ping and pong
                close_timeout=2,
                max_size=MAX_MESSAGE,
            ) as ws:
                await self._talk(ws)
                return "ended"
        except ConnectionClosed as exc:
            code = exc.rcvd.code if exc.rcvd is not None else None
            # Since milestone 2.6 the cloud says why it closed, in plain words.
            reason = _printable(exc.rcvd.reason) if exc.rcvd is not None else ""
            if self._connected:
                if reason:
                    log.info("the cloud closed the link (%s: %s)", code, reason)
                return "ended"
            if code == UNKNOWN_BOX:
                self.last_problem = (
                    f"the cloud refused this box: {reason}" if reason else "the cloud doesn't know this box"
                )
                log.warning("the cloud refused box %s: %s", self.identity.device_id, reason or "unknown box")
                if self._on_unknown_box is not None and await self._on_unknown_box():
                    log.info("box registered with the development cloud")
                    return "registered"
            else:
                said = f"{code}: {reason}" if reason else f"{code}"
                self.last_problem = f"the cloud closed the connection ({said})"
                if reason:
                    log.warning("the cloud closed the connection (%s)", said)
            return "failed"
        except (OSError, asyncio.TimeoutError, InvalidHandshake, InvalidURI, LinkProtocolError) as exc:
            self.last_problem = f"can't reach the cloud ({type(exc).__name__})"
            return "failed"
        finally:
            self._went_down()

    async def _talk(self, ws) -> None:
        challenge = await self._receive(ws, self.t.open_timeout)
        nonce = challenge.get("nonce")
        if challenge.get("type") != "challenge" or not isinstance(nonce, str):
            raise LinkProtocolError("expected a challenge")
        status = self._agent.status() if self._agent else {}
        self._redo_hello = False  # this hello carries the latest news
        claim_fields = self._claim.hello_fields() if self._claim else {}
        hello = {
            "type": "hello",
            "protocol": PROTOCOL,
            "device_id": self.identity.device_id,
            "signature": self.identity.sign_challenge(nonce),
            # Lets a cloud that doesn't know this box yet register it (decision D22).
            "public_key": self.identity.public_key,
            "software": self.software,
            "mode": status.get("mode"),
            "light": status.get("light"),
            "capabilities": ["light", "button"],
            **claim_fields,
        }
        await ws.send(json.dumps(hello))
        welcome = await self._receive(ws, self.t.open_timeout)
        if welcome.get("type") != "welcome":
            raise LinkProtocolError("expected a welcome")

        self._connected = True
        self.state = "connected"
        self.last_problem = None
        self._retry = self.t.first_retry
        self._outbox = asyncio.Queue()
        self._last_status = None
        log.info("connected to the cloud")
        owner = welcome.get("owner") if isinstance(welcome.get("owner"), dict) else {}
        try:
            if self._claim is not None:
                self._claim.on_welcome(claim_fields)
            # before "reachable", so the mode is right
            self._owner_news(bool(owner.get("claimed")), welcome.get("rotate_claim") is True)
        except Exception:
            # As with any message from the cloud: logged, and the link stays.
            log.exception("the box software failed on the cloud's welcome")
        self._tell_listener(True)
        try:
            self._queue_status()
        except Exception:
            log.exception("the box software couldn't say how the box is doing")

        sender = asyncio.create_task(self._send_all(ws))
        try:
            await self._receive_all(ws)
        finally:
            sender.cancel()

    async def _receive(self, ws, timeout: float) -> dict:
        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        message = self._parse(raw)
        if message is None:
            raise LinkProtocolError("not a message")
        return message

    async def _receive_all(self, ws) -> None:
        loop = asyncio.get_running_loop()
        last_heard = loop.time()
        while True:
            if self._redo_hello:
                log.info("reconnecting to tell the cloud the box's news")
                await ws.close()
                return
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=self.t.poll)
            except asyncio.TimeoutError:
                if not self._gate():
                    log.info("the box's network can't reach the cloud: closing the link")
                    await ws.close()
                    return
                if loop.time() - last_heard > self.t.dead_after:
                    log.warning("no word from the cloud for %.0f s: closing the link", self.t.dead_after)
                    await ws.close()
                    return
                continue
            last_heard = loop.time()
            message = self._parse(raw)
            if message is None:
                continue
            try:
                self._dispatch(message)
            except Exception:
                # The box software failed on one message: say so, and keep the link.
                log.exception("the box software failed on a %r message from the cloud", message.get("type"))

    def _dispatch(self, message: dict) -> None:
        kind = message.get("type")
        if kind == "ping":
            self._put({"type": "pong"})
        elif kind == "owner":
            self._owner_news(bool(message.get("claimed")), message.get("rotate_claim") is True)
        elif kind == "command":
            self._command(message)

    async def _send_all(self, ws) -> None:
        while True:
            message = await self._outbox.get()
            try:
                text = json.dumps(message)
            except (TypeError, ValueError):
                # Something the box software gave the link can't be written as JSON.
                # Drop that one message; the ones after it still go.
                log.exception("a %r message for the cloud couldn't be written, and was dropped", message.get("type"))
                continue
            try:
                await ws.send(text)
            except ConnectionClosed:
                return

    # ------------------------------------------------------------ messages

    @staticmethod
    def _parse(raw: object) -> dict | None:
        if not isinstance(raw, str) or len(raw) > MAX_MESSAGE:
            return None
        try:
            message = json.loads(raw)
        except ValueError:
            return None
        return message if isinstance(message, dict) else None

    def _put(self, message: dict) -> None:
        if self._connected and self._outbox is not None:
            self._outbox.put_nowait(message)

    def _queue_status(self) -> None:
        if self._agent is None:
            return
        status = self._agent.status()
        message = {
            "type": "status",
            "mode": status.get("mode"),
            "light": status.get("light"),
            "network": status.get("network") or {},
        }
        if message != self._last_status:
            self._last_status = message
            self._put(message)

    def _owner_news(self, claimed: bool, rotate: bool) -> None:
        """The cloud's word on the owner. When it asks for a new claim secret
        (the old one was used), the box makes one and sends up its hash."""
        if self._claim is None:
            return
        new_hash = self._claim.on_owner(claimed, rotate)
        if new_hash is not None:
            self._put({"type": "claim", "claim_hash": new_hash})

    def _command(self, message: dict) -> None:
        cid, name, args = message.get("id"), message.get("name"), message.get("args", {})
        if not isinstance(cid, str):
            return
        if not isinstance(name, str) or not isinstance(args, dict):
            ok, error = False, "bad_args"
        elif self._agent is None:
            ok, error = False, "busy"
        else:
            try:
                ok, error = self._agent.run_command(name, args)
            except Exception:
                # Answer at once, so the cloud isn't left waiting for its timeout.
                log.exception("the box failed while running the command %r", name)
                ok, error = False, "failed"
        if ok and name == "status.get":
            self._last_status = None
            self._queue_status()
        result = {"type": "result", "id": cid, "ok": ok}
        if not ok:
            result["error"] = error
        self._put(result)

    def _on_agent(self, kind: str, data: dict) -> None:
        if not self._connected:
            return  # events while offline are not kept in protocol 1
        if kind == "status":
            self._queue_status()
        elif kind == "event":
            self._put({"type": "event", "at": _now_iso(), **data})

    # ------------------------------------------------------------ reachability

    def _tell_listener(self, reachable: bool) -> None:
        self._reported = reachable
        try:
            self._listener(reachable)
        except Exception:
            # The box software's trouble with the news is its own: the link carries on.
            log.exception("the box software failed on news that the cloud is %s", "reachable" if reachable else "out of reach")

    def _went_down(self) -> None:
        was_connected = self._connected
        self._connected = False
        self._outbox = None
        if was_connected:
            log.info("link to the cloud closed")
            self._tell_listener(False)
