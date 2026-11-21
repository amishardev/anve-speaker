#!/usr/bin/env python3
"""A stand-in for the ANVE assistant socket, for developing the device offline.

It speaks protocol version 1 the way server/anve/services/assistant does for a device in
mode=wake or ptt, closely enough to exercise every message a speaker must handle, and it
costs none of the real account's 30 sessions an hour. It is NOT the platform: it has no
model. Its "reply" is a WAV file (--reply, 24 kHz mono) or a short tone.

    python3 tools/mock_server.py --port 8765 --key anve_dev_mock [--reply reply.wav]

Lines typed on stdin trigger server-side events on every open session:
    alarm | chime          earcon + announcement notice + spoken reply
    rec on | rec off       a meeting recording starts / stops (state.rec, `recording`)
    job                    job started -> job done + notice
    confirm                a confirm.request (voice_ok false, as on a room device)
    close <reason>         closing {reason} then close (try: rate, concurrent, idle)
    drop                   cut the TCP connection without a close frame
    say                    speak the reply now (an unprompted announcement)
"""
import argparse
import asyncio
import json
import math
import struct
import sys
import time
import wave
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from anve_device import opus  # noqa: E402
from anve_device.wake import EnergyVAD  # noqa: E402

SESSIONS: set = set()
ARGS = None


def dbfs(pcm: bytes) -> float:
    n = len(pcm) // 2
    if not n:
        return -100.0
    s = struct.unpack(f"<{n}h", pcm[:n * 2])
    rms = math.sqrt(sum(v * v for v in s) / n) / 32768.0
    return 20 * math.log10(max(rms, 1e-6))


def tone_reply(seconds: float = 3.0) -> bytes:
    out = bytearray()
    for i in range(int(24000 * seconds)):
        f = 440 if (i // 6000) % 2 == 0 else 660
        env = min(1.0, (i % 6000) / 600, (6000 - i % 6000) / 600)
        out += struct.pack("<h", int(6000 * env * math.sin(2 * math.pi * f * i / 24000)))
    return bytes(out)


def load_reply(path: str | None) -> bytes:
    if not path:
        return tone_reply()
    w = wave.open(path)
    if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (24000, 1, 2):
        raise SystemExit("--reply must be 24 kHz mono 16-bit")
    return w.readframes(w.getnframes())


class Mock:
    def __init__(self, ws, mode: str, speak: bool):
        self.ws, self.mode, self.speak = ws, mode, speak
        self.codec = "pcm16"
        self.enc = self.dec = None
        self.state = "wake"          # wake | await | listen | answer | followup | muted
        self.ptt = False
        self.rec = False
        self.muted = False
        self.deadline = 0.0
        self.voiced = self.silent = self.heard = 0.0
        self.frames = self.bytes_in = 0
        self.speaking: asyncio.Task | None = None
        self.last_led = None
        self.rec_bytes = 0
        self.vad = EnergyVAD(hang_frames=13)      # the server's own detector

    async def send(self, obj):
        await self.ws.send(json.dumps(obj))

    async def led(self, s, cloud=0, rec=0):
        msg = {"type": "state", "s": s, "cloud": cloud}
        if rec:
            msg["rec"] = 1
        if msg != self.last_led:
            self.last_led = msg
            await self.send(msg)

    async def rest(self):
        if self.muted:
            await self.led("muted")
        elif self.rec:
            await self.led("listening", rec=1)
        else:
            await self.led("idle")

    async def start(self):
        await self.send({"type": "ready", "session_id": f"mock-{int(time.time())}",
                         "engine": "mock", "cloud": True, "where": "this machine (mock)",
                         "mode": self.mode, "voice_confirm": False, "protocol": 1,
                         "limits": {"max_session_s": 900, "idle_timeout_s": 120},
                         "rates": {"in": 16000, "out": 24000}, "dnd": False,
                         "wake": {"wake": "device", "boxwake": "box"}.get(self.mode, ""),
                         "codec": self.codec, "heartbeat_s": ARGS.heartbeat})
        await self.led("idle")
        await self.send({"type": "notices", "items": []})

    # ---------- replies ----------

    async def reply(self, text="This is the mock ANVE server speaking.", followup=True):
        self.state = "answer"
        await self.led("thinking")
        await asyncio.sleep(ARGS.think)
        await self.send({"type": "transcript.out", "text": text})
        pcm = ARGS.reply_pcm if self.speak else b""
        if pcm:
            await self.led("speaking")
        t0, sent = time.monotonic(), 0
        step = 4800                                   # 100 ms of 24 kHz PCM16
        for i in range(0, len(pcm), step):
            chunk = pcm[i:i + step]
            if self.codec == "opus":
                for j in range(0, len(chunk) - 959, 960):
                    await self.ws.send(self.enc.encode(chunk[j:j + 960]))
            else:
                await self.ws.send(chunk)
            sent += len(chunk)
            # like the real engine: a little faster than real time, so the device buffers
            ahead = sent / 48000 - (time.monotonic() - t0)
            if ahead > 0.5:
                await asyncio.sleep(ahead - 0.5)
        left = max(0.0, len(pcm) / 48000 - (time.monotonic() - t0))
        await self.send({"type": "turn.done", "latency_ms": int(ARGS.think * 1000)})
        if pcm:
            await self.send({"type": "audio_end", "s": round(left, 2)})
        if followup and self.mode == "wake" and not self.muted and not self.rec:
            self.state, self.deadline = "followup", time.monotonic() + left + 8
            await self.led("listening")
            await self.send({"type": "listening", "until_s": round(left + 8, 1)})
        else:
            self.state = "wake"
            await self.rest()

    def speak_now(self, **kw):
        if self.speaking and not self.speaking.done():
            self.speaking.cancel()
        self.speaking = asyncio.create_task(self.reply(**kw))

    async def barge(self, by="voice"):
        if self.speaking and not self.speaking.done():
            self.speaking.cancel()
            await self.send({"type": "stop_audio", "by": by})

    # ---------- from the device ----------

    async def on_json(self, m):
        t = m.get("type")
        if t == "hello":
            if m.get("codec") == "opus" and opus.available() and not ARGS.no_opus:
                self.codec = "opus"
                self.enc, self.dec = opus.Encoder(24000), opus.Decoder(16000)
            await self.send({"type": "hello", "codec": self.codec,
                             "opus_available": opus.available() and not ARGS.no_opus})
        elif t == "ping":
            await self.send({"type": "pong"})
        elif t == "wake" and not self.muted:
            await self.barge()
            await self.send({"type": "earcon", "kind": "wake"})
            self.state, self.deadline = "await", time.monotonic() + 5
            self.voiced = self.silent = self.heard = 0.0
            await self.led("listening")
        elif t == "ptt.start" and not self.muted:
            await self.barge("button")
            self.ptt, self.frames = True, 0
            await self.led("listening", cloud=1)
        elif t == "ptt.end" and self.ptt:
            self.ptt = False
            await self.rest()
            print(f"  ptt turn: {self.frames} frames")
            self.speak_now(text=f"I heard {self.frames * 0.04:.1f} seconds in push to talk.",
                           followup=False)
        elif t == "interrupt":
            await self.barge("button")
            self.state = "wake"
            await self.rest()
        elif t == "mute":
            self.muted = bool(m.get("on"))
            await self.barge()
            self.state = "muted" if self.muted else "wake"
            await self.rest()
        elif t == "record.stop" and self.rec:
            await self.set_rec(False, "Stopped from the screen.")
        elif t == "text":
            await self.send({"type": "transcript.in", "text": m.get("text", ""), "typed": True})
            self.speak_now(text=f"You typed: {m.get('text', '')}", followup=False)
        elif t == "stop":
            await self.send({"type": "closing", "reason": "client_stop"})
            await self.ws.close()
        print(f"  <- {m}")

    async def on_audio(self, frame: bytes):
        if self.codec == "opus":
            frame = self.dec.decode(frame) if self.dec else b""
        self.bytes_in += len(frame)
        if self.muted or not frame or len(frame) % 2:
            return
        if self.rec and not self.ptt:
            self.rec_bytes += len(frame)
            return
        if self.ptt:
            self.frames += 1
            return
        now = time.monotonic()
        if self.state in ("await", "followup") and now > self.deadline:
            was, self.state = self.state, "wake"
            await self.led("idle")
            if was == "await":
                await self.send({"type": "earcon", "kind": "end"})
                await self.send({"type": "notice", "level": "info",
                                 "text": "I didn't catch that."})
            return
        ev = self.vad.feed(frame)                 # as handsfree.py does
        loud = self.vad.level > max(self.vad.floor + 10.0, -55.0)
        if self.state in ("await", "followup"):
            if ev == "start":
                self.state = "listen"
                self.voiced, self.silent, self.heard = 0.04, 0.0, 0.04
                await self.led("listening", cloud=1)
        elif self.state == "listen":
            self.heard += 0.04
            if loud:
                self.voiced, self.silent = self.voiced + 0.04, 0.0
            else:
                self.silent += 0.04
            if (self.voiced > 0.2 and self.silent >= 0.8) or self.heard > 15:
                print(f"  request: {self.heard:.2f} s heard, {self.voiced:.2f} s voiced")
                self.speak_now(text=f"I heard {self.voiced:.1f} seconds of speech.")
        elif self.state not in ("wake", "answer"):
            return
        elif self.state == "wake" and ARGS.strict:
            print("  !! audio received while idle (mode=wake forbids streaming before a wake)")

    async def set_rec(self, on: bool, note=""):
        self.rec = on
        if on:
            self.rec_bytes = 0
            await self.send({"type": "confirm.result", "id": "c1", "status": "done",
                             "summary": "Recording 'Mock meeting' now.",
                             "card": {"kind": "recording", "meeting_id": "m1"}})
        else:
            print(f"  recording stopped: {self.rec_bytes / 32000:.1f} s received")
            await self.send({"type": "recording", "state": "processing", "meeting_id": "m1",
                             "note": note})
        await self.rest()

    async def command(self, line: str):
        w = line.split()
        if not w:
            return
        if w[0] in ("alarm", "chime"):
            await self.send({"type": "earcon", "kind": w[0]})
            await self.send({"type": "notice", "level": "info", "announce": True,
                             "spoken": True, "text": "Your tea timer is done."})
            self.speak_now(text="Your tea timer is done.", followup=False)
        elif w[0] == "rec" and len(w) > 1:
            await self.set_rec(w[1] == "on", "Stopped by the mock.")
        elif w[0] == "job":
            await self.send({"type": "job", "kind": "deck", "id": "j1", "status": "started"})
            await asyncio.sleep(2)
            await self.send({"type": "job", "kind": "deck", "id": "j1", "status": "done"})
            await self.send({"type": "notice", "level": "info", "text": "The deck is ready."})
            self.speak_now(text="The deck is ready.", followup=False)
        elif w[0] == "confirm":
            await self.send({"type": "confirm.request", "id": "c2", "action": "save_note",
                             "readback": "Save a note: call the auditor on Monday.",
                             "expires_at": int(time.time()) + 300, "voice_ok": False})
        elif w[0] == "close":
            await self.send({"type": "closing", "reason": w[1] if len(w) > 1 else "idle"})
            await self.ws.close(code=4429 if len(w) > 1 and w[1] in ("rate", "concurrent") else 1000)
        elif w[0] == "drop":
            self.ws.transport.abort()
        elif w[0] == "say":
            self.speak_now(followup=False)


async def handler(ws):
    req = ws.request
    q = parse_qs(urlsplit(req.path).query)
    auth = req.headers.get("Authorization", "")
    key = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
    if urlsplit(req.path).path != "/api/assistant/ws" or key != ARGS.key:
        await ws.send(json.dumps({"type": "closing", "reason": "unauthorised",
                                  "message": "Unknown or revoked device key."}))
        await ws.close(code=4401)
        print("refused: bad path or key")
        return
    m = Mock(ws, (q.get("mode") or ["ptt"])[0], (q.get("speak") or ["1"])[0] != "0")
    SESSIONS.add(m)
    print(f"session open: mode={m.mode} speak={m.speak} origin={req.headers.get('Origin')}")
    try:
        await m.start()
        async for raw in ws:
            if isinstance(raw, (bytes, bytearray)):
                await m.on_audio(bytes(raw))
            else:
                try:
                    await m.on_json(json.loads(raw))
                except ValueError:
                    pass
    except websockets.ConnectionClosed:
        pass
    finally:
        SESSIONS.discard(m)
        if m.speaking:
            m.speaking.cancel()
        print(f"session closed: {m.bytes_in / 32000:.1f} s of audio received")


async def stdin_commands():
    loop = asyncio.get_running_loop()
    while True:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line:
            await asyncio.Event().wait()         # stdin closed: keep serving
        for m in list(SESSIONS):
            try:
                await m.command(line.strip())
            except Exception as e:               # noqa: BLE001
                print(f"command failed: {e!r}")


async def main():
    global ARGS
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--key", default="anve_dev_mock")
    ap.add_argument("--reply", help="24 kHz mono WAV to play as the reply")
    ap.add_argument("--think", type=float, default=0.4)
    ap.add_argument("--heartbeat", type=float, default=15)
    ap.add_argument("--vad-db", type=float, default=-45.0)
    ap.add_argument("--no-opus", action="store_true")
    ap.add_argument("--strict", action="store_true")
    ARGS = ap.parse_args()
    ARGS.reply_pcm = load_reply(ARGS.reply)
    async with websockets.serve(handler, ARGS.host, ARGS.port, ping_interval=None,
                                max_size=64 * 1024):
        print(f"mock ANVE socket on ws://{ARGS.host}:{ARGS.port}/api/assistant/ws", flush=True)
        await stdin_commands()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
