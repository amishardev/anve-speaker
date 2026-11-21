"""The ANVE session socket, as a device speaks it (docs/assistant-protocol.md, version 1).

    wss://<host>/api/assistant/ws?engine=..&mode=..&speak=..   Authorization: Bearer <key>

What this class owns: connecting, `hello` (Opus if the server has it, else PCM16),
`ready`, the heartbeat, reconnecting with the documented backoff, and never reconnecting
after a `closing` whose reason needs a person. Everything else is handed to the caller:

    on_json(msg)      every server JSON message (ready, state, earcon, notice, ...)
    on_audio(pcm)     reply audio, always 24 kHz mono PCM16 (Opus is decoded here)
    on_link(up, why)  the link came up (after `ready`) or went down; `why` is the reason

The device key is read from a file at connect time and is never logged.
"""
import asyncio
import json
import logging
import time
from pathlib import Path
from urllib.parse import urlencode

import websockets

from . import opus

log = logging.getLogger("anve.client")

BACKOFF_S = (1, 2, 4, 8, 15, 30)
#: `closing` reasons after which a device must not reconnect by itself
NEEDS_PERSON = frozenset({"unauthorised", "origin", "rate", "concurrent"})
IN_RATE, OUT_RATE = 16000, 24000
FRAME_BYTES = 1280              # 40 ms of 16 kHz PCM16: the server's VAD counts 40 ms a frame


class Client:
    def __init__(self, url: str, key_file: str, *, engine: str = "live", mode: str = "wake",
                 speak: bool = True, codec: str = "opus", name: str = "anve-pi-speaker",
                 on_json=None, on_audio=None, on_link=None):
        self.url, self.key_file = url.rstrip("?"), key_file
        self.engine, self.mode, self.speak = engine, mode, speak
        self.want_codec = codec if (codec == "opus" and opus.available()) else "pcm16"
        self.name = name
        self.on_json, self.on_audio, self.on_link = on_json, on_audio, on_link
        self.ws = None
        self.ready: dict | None = None
        self.codec = "pcm16"
        self.codec_known = asyncio.Event()
        self.halted = ""            # a reason that needs a person; no reconnect until resume()
        self._resume = asyncio.Event()
        self._enc = self._dec = None
        self._stop = False
        self.last_rx = 0.0
        self.sessions_opened = 0

    # ---------- public ----------

    @property
    def up(self) -> bool:
        return self.ws is not None and self.ready is not None

    def full_url(self) -> str:
        q = urlencode({"engine": self.engine, "mode": self.mode, "speak": 1 if self.speak else 0})
        return f"{self.url}?{q}"

    async def send(self, obj: dict) -> bool:
        ws = self.ws
        if ws is None:
            return False
        try:
            await ws.send(json.dumps(obj, separators=(",", ":")))
            return True
        except Exception:                                    # noqa: BLE001
            return False

    async def send_audio(self, pcm16: bytes) -> bool:
        """One 40 ms frame of 16 kHz mono PCM16 (1280 bytes)."""
        ws = self.ws
        if ws is None or not self.codec_known.is_set():
            return False
        try:
            if self.codec == "opus":
                if self._enc is None:
                    self._enc = opus.Encoder(IN_RATE)
                pcm16 = self._enc.encode(pcm16)
            await ws.send(pcm16)
            return True
        except Exception:                                    # noqa: BLE001
            return False

    def resume(self):
        """A person asked to try again after a halt (e.g. a long press on the button)."""
        self.halted = ""
        self._resume.set()

    async def close(self):
        self._stop = True
        self._resume.set()
        if self.ws is not None:
            await self.send({"type": "stop"})
            try:
                await self.ws.close()
            except Exception:                                # noqa: BLE001
                pass

    # ---------- the connection loop ----------

    async def run(self):
        attempt = 0
        while not self._stop:
            if self.halted:
                log.error("not reconnecting: the server said '%s'. This needs a person "
                          "(see README, 'When the light stays red').", self.halted)
                self._resume.clear()
                await self._resume.wait()
                attempt = 0
                continue
            why = await self._session()
            if self._stop:
                break
            if self.ready is not None:
                attempt = 0                 # the backoff resets after a `ready`
            self.ready = None
            if why in NEEDS_PERSON:
                self.halted = why
                await self._link(False, why)
                continue
            wait = BACKOFF_S[min(attempt, len(BACKOFF_S) - 1)]
            attempt += 1
            await self._link(False, why)
            log.warning("link down (%s); retrying in %d s", why, wait)
            await asyncio.sleep(wait)

    async def _link(self, up: bool, why: str = ""):
        if self.on_link:
            try:
                await self.on_link(up, why)
            except Exception:                                # noqa: BLE001
                log.exception("on_link failed")

    def _key(self) -> str:
        return Path(self.key_file).read_text().strip()

    async def _session(self) -> str:
        """One connection, start to end. Returns why it ended."""
        self.ready, self.codec = None, "pcm16"
        self.codec_known.clear()
        self._enc = self._dec = None
        try:
            key = self._key()
        except OSError as e:
            log.error("cannot read the device key file %s (%s)", self.key_file,
                      type(e).__name__)
            return "no_key"
        if not key:
            return "no_key"
        try:
            ws = await websockets.connect(
                self.full_url(), additional_headers={"Authorization": f"Bearer {key}"},
                open_timeout=15, ping_interval=None, max_size=4 * 1024 * 1024,
                user_agent_header=f"{self.name}/1")
        except Exception as e:                               # noqa: BLE001
            if "CertVerification" in type(e).__name__ or "certificate" in str(e).lower():
                # seen on campus Wi-Fi: the network answers for every site with its own
                # sign-in page's certificate once the login has expired
                log.error("the network answered for %s with another site's certificate. "
                          "The Wi-Fi sign-in page is probably in the way: sign this Pi in "
                          "to the Wi-Fi again.", self.url.split("//")[-1].split("/")[0])
            return f"connect_failed:{type(e).__name__}"
        finally:
            del key
        self.ws = ws
        self.sessions_opened += 1
        self.last_rx = time.monotonic()
        why = "closed"
        hb = None
        try:
            await self.send({"type": "hello", "client": self.name, "codec": self.want_codec})
            async for raw in ws:
                self.last_rx = time.monotonic()
                if isinstance(raw, (bytes, bytearray)):
                    await self._audio_in(bytes(raw))
                    continue
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                typ = msg.get("type")
                if typ == "pong":
                    continue
                if typ == "hello":
                    self.codec = "opus" if msg.get("codec") == "opus" else "pcm16"
                    self.codec_known.set()
                    log.info("codec: %s", self.codec)
                elif typ == "ready":
                    self.ready = msg
                    log.info("ready: session %s engine=%s cloud=%s mode=%s wake=%s "
                             "voice_confirm=%s", msg.get("session_id"), msg.get("engine"),
                             msg.get("cloud"), msg.get("mode"), msg.get("wake"),
                             msg.get("voice_confirm"))
                    if hb is None:
                        hb = asyncio.create_task(
                            self._heartbeat(float(msg.get("heartbeat_s") or 15)))
                    await self._link(True)
                elif typ == "closing":
                    why = str(msg.get("reason") or "closed")
                    log.warning("server closing: %s %s", why, msg.get("message") or "")
                elif typ == "error":
                    why = "engine_error"
                    log.error("server error: %s", msg.get("message"))
                if self.on_json:
                    try:
                        await self.on_json(msg)
                    except Exception:                        # noqa: BLE001
                        log.exception("handler failed for %s", typ)
        except websockets.ConnectionClosed as e:
            if why == "closed":
                why = f"dropped:{getattr(e.rcvd, 'code', None) or 'no_close'}"
        except Exception as e:                               # noqa: BLE001
            why = f"error:{type(e).__name__}"
        finally:
            if hb:
                hb.cancel()
            self.ws = None
            for c in (self._enc, self._dec):
                if c:
                    c.close()
            self._enc = self._dec = None
            try:
                await ws.close()
            except Exception:                                # noqa: BLE001
                pass
        return why

    async def _audio_in(self, data: bytes):
        if not self.on_audio or not data:
            return
        if self.codec == "opus":
            if self._dec is None:
                self._dec = opus.Decoder(OUT_RATE)
            data = self._dec.decode(data)
            if not data:
                return
        await self.on_audio(data)

    async def _heartbeat(self, every: float):
        """`ping` every `heartbeat_s`. A link that has been silent for three beats is
        treated as dead (Wi-Fi gone without a close) and torn down so `run` reconnects."""
        while True:
            await asyncio.sleep(every)
            if time.monotonic() - self.last_rx > 3 * every + 5:
                log.warning("no frame from the server for %.0f s: dropping the link",
                            time.monotonic() - self.last_rx)
                try:
                    await asyncio.wait_for(self.ws.close(), 2)
                except Exception:                            # noqa: BLE001
                    transport = getattr(self.ws, "transport", None)
                    if transport:
                        transport.abort()
                return
            await self.send({"type": "ping"})
