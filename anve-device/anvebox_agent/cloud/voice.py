"""Talking out loud through the box (box voice: part C of
docs/box-cloud-agreement.md, decision D39).

One talk is one WebSocket to the cloud, opened when the person starts talking
to the box (after its wake word, say) and closed when the conversation is over.
The box proves which box it is with its own key, as on the box link, and the
cloud answers as ANVE for the box's owner: the same AI, tools and saved chats
as talking out loud in the web app.

    talk = VoiceTalk(cloud_url, identity, on_sound=play, on_event=show)
    task = asyncio.create_task(talk.run())   # ends with the cloud's "ended" message
    await talk.ready.wait()                  # ANVE is listening
    await talk.send_sound(piece)             # the microphone, as it's recorded
    ...
    await talk.end()                         # hang up; run() then returns "ended"

Sound both ways is raw PCM: mono, 16-bit, little-endian. 16 kHz goes from the
box, 24 kHz comes back. Send the microphone as it is recorded, in pieces of
20 ms to 1 s: the cloud doesn't pass on sound that runs more than 2 s ahead of
the clock.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from typing import Any, Callable

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidURI

from ..identity import Identity

log = logging.getLogger("anvebox.voice")

PROTOCOL = 1
IN_RATE = 16_000  # the box's microphone, as the cloud takes it
OUT_RATE = 24_000  # ANVE's voice, as the cloud sends it
PIECE_MAX = 32_000  # bytes of sound in one message: one second at 16 kHz
REFUSED = (4400, 4401)  # the cloud turned the box away (the reason says why)


def voice_url(cloud_url: str) -> str:
    """https://cloud.example -> wss://cloud.example/box/v1/voice"""
    base = cloud_url.strip().rstrip("/")
    if base.startswith("https://"):
        base = "wss://" + base[len("https://"):]
    elif base.startswith("http://"):
        base = "ws://" + base[len("http://"):]
    return base + "/box/v1/voice"


def _ended(reason: str, text: str, **extra: Any) -> dict:
    """An "ended" message made by the box itself, shaped like the cloud's."""
    return {"type": "ended", "reason": reason, "text": text, "seconds": 0, "chat": None, **extra}


async def _call(callback: Callable | None, value: Any) -> None:
    if callback is None:
        return
    result = callback(value)
    if inspect.isawaitable(result):
        await result


class VoiceTalk:
    """One talk with ANVE through the box.

    on_sound(bytes): a piece of ANVE's voice, 24 kHz PCM, to play at once.
    on_event(dict): every message from the cloud after the hello: "ready",
    "heard" and "said" (the words of both sides, as they come), "action",
    "rule", "interrupted" (stop playing: the person cut in), "turn", "warning",
    "chat" and finally "ended"."""

    def __init__(
        self,
        cloud_url: str,
        identity: Identity,
        *,
        on_sound: Callable[[bytes], Any] | None = None,
        on_event: Callable[[dict], Any] | None = None,
        open_timeout: float = 15.0,
    ) -> None:
        self.url = voice_url(cloud_url)
        self.identity = identity
        self.on_sound = on_sound
        self.on_event = on_event
        self.open_timeout = open_timeout
        self.ready = asyncio.Event()  # set once the cloud says ANVE is listening
        self.ended: dict | None = None
        self._ws = None

    async def run(self) -> dict:
        """The whole talk. Returns the cloud's "ended" message, or one made here
        when the talk couldn't start ("refused", "unreachable", "failed") or
        the connection dropped ("dropped")."""
        try:
            async with connect(self.url, open_timeout=self.open_timeout, ping_interval=None, max_size=2**20) as ws:
                self._ws = ws
                first = json.loads(await asyncio.wait_for(ws.recv(), self.open_timeout))
                if not isinstance(first, dict):
                    return self._finish(_ended("failed", "the cloud didn't start with a challenge"))
                if first.get("type") == "ended":  # talking out loud is off on this cloud
                    return self._finish(first)
                nonce = first.get("nonce")
                if first.get("type") != "challenge" or not isinstance(nonce, str):
                    return self._finish(_ended("failed", "the cloud didn't start with a challenge"))
                await ws.send(json.dumps({
                    "type": "hello",
                    "protocol": PROTOCOL,
                    "device_id": self.identity.device_id,
                    "signature": self.identity.sign_voice_challenge(nonce),
                }))
                async for message in ws:
                    if isinstance(message, bytes):
                        await _call(self.on_sound, message)
                        continue
                    try:
                        event = json.loads(message)
                    except ValueError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    if event.get("type") == "ready":
                        log.info("talking to ANVE")
                        self.ready.set()
                    await _call(self.on_event, event)
                    if event.get("type") == "ended":
                        return self._finish(event)
        except ConnectionClosed as closed:
            code = closed.rcvd.code if closed.rcvd is not None else None
            reason = closed.rcvd.reason if closed.rcvd is not None else ""
            if code in REFUSED:
                # The cloud says why in plain words (as on the box link): log it, act on the code.
                log.warning("the cloud refused the voice talk (%s: %s)", code, reason)
                return self._finish(_ended("refused", reason, code=code))
            return self._finish(_ended("dropped", f"the connection closed ({code})"))
        except (OSError, asyncio.TimeoutError, InvalidHandshake, InvalidURI, ValueError) as error:
            return self._finish(_ended("unreachable", f"can't reach the cloud ({type(error).__name__})"))
        finally:
            self._ws = None
        return self._finish(_ended("dropped", "the connection closed"))

    async def send_sound(self, piece: bytes) -> bool:
        """One piece of the microphone: 16 kHz mono 16-bit PCM, 20 ms to 1 s of
        it. False (and nothing sent) before "ready", after the end, or for a
        piece the cloud wouldn't take."""
        ws = self._ws
        if ws is None or not self.ready.is_set() or self.ended is not None:
            return False
        if not 0 < len(piece) <= PIECE_MAX or len(piece) % 2:
            return False
        try:
            await ws.send(piece)
        except ConnectionClosed:
            return False
        return True

    async def end(self) -> None:
        """Hang up. The cloud keeps the words, then sends "ended"."""
        ws = self._ws
        if ws is None:
            return
        try:
            await ws.send(json.dumps({"type": "end"}))
        except ConnectionClosed:
            pass

    def _finish(self, ended: dict) -> dict:
        self.ended = ended
        return ended
