"""Recording a meeting through the box (5.3: part D of
docs/box-cloud-agreement.md, decisions D95 to D98).

The box cuts its microphone into pieces of about 55 seconds and hands each to
`MeetingRecording`, which keeps it in a folder on the card until the cloud
answers "got" (D95). If the network drops, or the box restarts, pieces wait
there and go up when the connection is back: a new `MeetingRecording` on the
same folder carries on with the same meeting. Nothing is lost.

    recording = MeetingRecording(cloud_url, identity, folder, meeting=from_voice, on_event=show)
    task = asyncio.create_task(recording.run())   # ends with the cloud's "ended" message
    await recording.started.wait()                # the cloud has the meeting
    recording.add_piece(0, 0, 55_000, wav_bytes)  # as each piece is cut
    recording.mark(23_100)                        # a tap: a mark 23.1 s into the meeting
    command = await recording.command(61_000, clip)  # after the wake word: "mark", "stop" or "nothing"
    recording.stop()                              # after the last piece; run() then returns "ended"

`meeting` is the id from box voice's "ended" message after "ANVE, record this
meeting" (start_meeting, D97), or None for a new meeting (a long press).
Times are milliseconds from the start of the meeting, counted from the
microphone's samples, not the box's clock.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from pathlib import Path
from typing import Any, Callable

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidURI

from ..identity import Identity

log = logging.getLogger("anvebox.meeting")

PROTOCOL = 1
FORMATS = ("ogg", "wav")
PIECE_MAX = 4 * 1024 * 1024  # bytes in a piece: the cloud's limit
COMMAND_MAX = 512 * 1024  # bytes in a command clip: a few seconds of WAV
REFUSED = (4400, 4401)  # the cloud turned the box away (the reason says why)
RETRY = (1.0, 2.0, 5.0, 10.0, 30.0)  # seconds between tries after a dropped connection
COMMAND_WAIT = 10.0  # seconds to wait for the cloud to understand a command


def meeting_url(cloud_url: str) -> str:
    """https://cloud.example -> wss://cloud.example/box/v1/meeting"""
    base = cloud_url.strip().rstrip("/")
    if base.startswith("https://"):
        base = "wss://" + base[len("https://"):]
    elif base.startswith("http://"):
        base = "ws://" + base[len("http://"):]
    return base + "/box/v1/meeting"


async def _call(callback: Callable | None, value: Any) -> None:
    if callback is None:
        return
    result = callback(value)
    if inspect.isawaitable(result):
        await result


class MeetingRecording:
    """One meeting recorded through the box.

    folder: where pieces wait on the card until the cloud has them. Give each
    meeting its own; a folder that still holds a meeting carries it on.
    on_event(dict): the cloud's messages: "started", "got", "heard",
    "warning", "refused" and finally "ended"."""

    def __init__(
        self,
        cloud_url: str,
        identity: Identity,
        folder: Path | str,
        *,
        meeting: str | None = None,
        on_event: Callable[[dict], Any] | None = None,
        open_timeout: float = 15.0,
        retry: tuple[float, ...] = RETRY,
    ) -> None:
        self.url = meeting_url(cloud_url)
        self.identity = identity
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        kept = self._read("meeting.json")
        self.meeting: str | None = kept.get("meeting") if kept else meeting
        self.on_event = on_event
        self.open_timeout = open_timeout
        self.retry = retry
        self.started = asyncio.Event()  # the cloud has the meeting
        self.ended: dict | None = None
        self._marks: list[int] = list((self._read("marks.json") or {}).get("marks", []))
        self._stopping = (self.folder / "stop").exists()
        self._wake = asyncio.Event()
        self._ws = None
        self._commands: dict[int, asyncio.Future] = {}

    # ------------------------------------------------------------ what the box hands it

    def add_piece(self, seq: int, start_ms: int, duration_ms: int, sound: bytes, format: str = "wav") -> None:
        """One piece of the meeting, kept on the card until the cloud has it.
        seq counts from 0; format is "ogg" (Ogg Opus) or "wav" (16-bit PCM)."""
        if format not in FORMATS or not 0 < len(sound) <= PIECE_MAX:
            raise ValueError("a piece is Ogg Opus or WAV, up to 4 MB")
        name = f"piece-{seq:05d}"
        (self.folder / f"{name}.{format}").write_bytes(sound)
        self._write(f"{name}.json", {"seq": seq, "start_ms": start_ms, "duration_ms": duration_ms, "format": format})
        self._wake.set()

    def mark(self, at_ms: int) -> None:
        """A tap: a mark this far into the meeting."""
        self._marks.append(int(at_ms))
        self._write("marks.json", {"marks": self._marks})
        self._wake.set()

    async def command(self, at_ms: int, clip: bytes, format: str = "wav") -> str:
        """The few seconds after the wake word, for the cloud to understand:
        "mark" (the cloud has added it), "stop" (send the last piece, then
        stop()), "nothing", or "unsent" when there's no connection just now
        (the button still works)."""
        ws = self._ws
        if ws is None or not self.started.is_set() or format not in FORMATS or not 0 < len(clip) <= COMMAND_MAX:
            return "unsent"
        answer = asyncio.get_running_loop().create_future()
        self._commands[int(at_ms)] = answer
        try:
            await ws.send(json.dumps({"type": "command", "at_ms": int(at_ms), "format": format}))
            await ws.send(clip)
            return await asyncio.wait_for(answer, COMMAND_WAIT)
        except (ConnectionClosed, asyncio.TimeoutError):
            return "unsent"
        finally:
            self._commands.pop(int(at_ms), None)

    def stop(self) -> None:
        """No more pieces: once the cloud has them all, it stops the meeting
        and makes the minutes. Kept on the card, so a restart still stops it."""
        self._stopping = True
        (self.folder / "stop").write_text("")
        self._wake.set()

    # ------------------------------------------------------------ the connection

    async def run(self) -> dict:
        """The whole meeting, reconnecting after a drop. Returns the cloud's
        "ended" message, or one made here when the cloud refused the box."""
        tries = 0
        while True:
            outcome = await self._connect()
            if outcome is not None:
                return self._finish(outcome)
            delay = self.retry[min(tries, len(self.retry) - 1)]
            tries += 1
            log.info("the meeting's connection dropped; trying again in %g s, its pieces wait on the card", delay)
            await asyncio.sleep(delay)

    async def _connect(self) -> dict | None:
        """One connection: the "ended" message, or None to try again."""
        try:
            async with connect(self.url, open_timeout=self.open_timeout, ping_interval=20, max_size=2**20) as ws:
                first = json.loads(await asyncio.wait_for(ws.recv(), self.open_timeout))
                nonce = first.get("nonce") if isinstance(first, dict) else None
                if not isinstance(first, dict) or first.get("type") != "challenge" or not isinstance(nonce, str):
                    return {"type": "ended", "reason": "failed", "text": "the cloud didn't start with a challenge",
                            "meeting": self.meeting}
                await ws.send(json.dumps({
                    "type": "hello",
                    "protocol": PROTOCOL,
                    "device_id": self.identity.device_id,
                    "signature": self.identity.sign_meeting_challenge(nonce),
                    "meeting": self.meeting,
                }))
                self._ws = ws
                sender = asyncio.create_task(self._send(ws))
                try:
                    return await self._receive(ws)
                finally:
                    sender.cancel()
                    await asyncio.gather(sender, return_exceptions=True)
        except ConnectionClosed as closed:
            code = closed.rcvd.code if closed.rcvd is not None else None
            if code in REFUSED:
                reason = closed.rcvd.reason if closed.rcvd is not None else ""
                log.warning("the cloud refused the meeting (%s: %s)", code, reason)
                return {"type": "ended", "reason": "refused", "text": reason, "code": code, "meeting": self.meeting}
            return None
        except (OSError, asyncio.TimeoutError, InvalidHandshake, InvalidURI, ValueError):
            return None
        finally:
            self._ws = None
            for answer in self._commands.values():
                if not answer.done():
                    answer.set_result("unsent")

    async def _receive(self, ws) -> dict | None:
        async for message in ws:
            if isinstance(message, bytes):
                continue
            try:
                event = json.loads(message)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            kind = event.get("type")
            if kind == "started":
                self.meeting = event.get("meeting")
                self._write("meeting.json", {"meeting": self.meeting})
                self.started.set()
                self._wake.set()
            elif kind == "got":
                self._forget(event.get("seq"))
            elif kind == "heard":
                answer = self._commands.get(event.get("at_ms"))
                if answer is not None and not answer.done():
                    answer.set_result(event.get("command") or "nothing")
            elif kind == "refused" and event.get("for") == "piece":
                # One the cloud will never take (too big, or past the meeting's end): not kept, so the stop can go.
                log.warning("the cloud wouldn't take a piece (%s)", event.get("error"))
                self._forget(event.get("seq"))
            elif kind == "refused" and event.get("for") == "command":
                answer = self._commands.get(event.get("at_ms"))
                if answer is not None and not answer.done():
                    answer.set_result("nothing")
            await _call(self.on_event, event)
            if kind == "ended":
                return event
        return None

    async def _send(self, ws) -> None:
        """Everything still on the card, oldest first, then stop once the cloud
        has every piece. Sent again in full after a reconnect."""
        sent: set[int] = set()
        stop_sent = False
        while True:
            await self.started.wait()
            for header in self._waiting():
                if header["seq"] in sent:
                    continue
                sound = (self.folder / f"piece-{header['seq']:05d}.{header['format']}").read_bytes()
                await ws.send(json.dumps({"type": "piece", **header}))
                await ws.send(sound)
                sent.add(header["seq"])
            while self._marks:
                await ws.send(json.dumps({"type": "mark", "at_ms": self._marks[0]}))
                self._marks.pop(0)
                self._write("marks.json", {"marks": self._marks})
            if self._stopping and not stop_sent and not self._waiting():
                await ws.send(json.dumps({"type": "stop"}))
                stop_sent = True
            self._wake.clear()
            await self._wake.wait()

    # ------------------------------------------------------------ the card

    def _waiting(self) -> list[dict]:
        headers = []
        for path in sorted(self.folder.glob("piece-*.json")):
            header = self._read(path.name)
            if header and (self.folder / f"{path.stem}.{header.get('format')}").exists():
                headers.append(header)
        return headers

    def _forget(self, seq: object) -> None:
        if not isinstance(seq, int):
            return
        for path in self.folder.glob(f"piece-{seq:05d}.*"):
            path.unlink(missing_ok=True)
        self._wake.set()  # a stop may now be due

    def _read(self, name: str) -> dict | None:
        try:
            data = json.loads((self.folder / name).read_text())
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def _write(self, name: str, data: dict) -> None:
        path = self.folder / name
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(data))
        temporary.replace(path)

    def _finish(self, ended: dict) -> dict:
        self.ended = ended
        if ended.get("reason") in ("stopped", "time_limit", "daily_limit"):
            # The cloud has everything it will take: the card can be cleared.
            for path in self.folder.iterdir():
                if path.is_file():
                    path.unlink(missing_ok=True)
        return ended
