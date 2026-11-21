#!/usr/bin/env python3
"""Protocol self-test: runs anve_device.client.Client against tools/mock_server.py on
loopback and checks the behaviour docs/assistant-protocol.md asks of a device.
No microphone, speaker or ANVE account is involved.

    .venv/bin/python tools/client_selftest.py [--codec opus|pcm16]
"""
import argparse
import asyncio
import math
import os
import struct
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from anve_device.client import Client, FRAME_BYTES  # noqa: E402

PORT = 8791
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}{('  (' + detail + ')') if detail else ''}", flush=True)


def frame(loud: bool, i: int) -> bytes:
    if not loud:
        return bytes(FRAME_BYTES)
    return b"".join(struct.pack("<h", int(9000 * math.sin(2 * math.pi * 300 * (i * 640 + n) / 16000)))
                    for n in range(640))


class Rig:
    def __init__(self, key: str, codec: str):
        self.msgs, self.audio, self.links = [], 0, []
        fd, self.key_file = tempfile.mkstemp()
        os.write(fd, key.encode())
        os.close(fd)
        self.c = Client(f"ws://127.0.0.1:{PORT}/api/assistant/ws", self.key_file, mode="wake",
                        codec=codec, on_json=self.on_json, on_audio=self.on_audio,
                        on_link=self.on_link)
        self.task = None

    async def on_json(self, m):
        self.msgs.append(m)

    async def on_audio(self, pcm):
        self.audio += len(pcm)

    async def on_link(self, up, why):
        self.links.append((up, why))

    def start(self):
        self.task = asyncio.create_task(self.c.run())

    def types(self, since=0):
        return [m["type"] for m in self.msgs[since:]]

    async def wait(self, pred, timeout=8.0):
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            if pred():
                return True
            await asyncio.sleep(0.05)
        return False

    async def stop(self):
        await self.c.close()
        self.task.cancel()
        os.unlink(self.key_file)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--codec", default="opus")
    a = ap.parse_args()
    mock = await asyncio.create_subprocess_exec(
        sys.executable, "-u", str(ROOT / "tools" / "mock_server.py"), "--port", str(PORT),
        "--key", "anve_dev_selftest", "--heartbeat", "2", "--think", "0.2", "--strict",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT)
    mock_out = []

    async def pump():
        async for line in mock.stdout:
            mock_out.append(line.decode(errors="replace").rstrip())
    pump_task = asyncio.create_task(pump())
    await asyncio.sleep(1.0)

    async def cmd(s):
        mock.stdin.write((s + "\n").encode())
        await mock.stdin.drain()

    try:
        # 1. a wrong key: refused, and the device must not try again by itself
        bad = Rig("anve_dev_wrong", a.codec)
        bad.start()
        await bad.wait(lambda: bad.c.halted != "", 5)
        n = bad.c.sessions_opened
        await asyncio.sleep(2.5)
        check("wrong key -> halted on 'unauthorised', no reconnect",
              bad.c.halted == "unauthorised" and bad.c.sessions_opened == n == 1,
              f"halted={bad.c.halted!r} connects={bad.c.sessions_opened}")
        await bad.stop()

        # 2. a good key: ready, codec, heartbeat
        r = Rig("anve_dev_selftest", a.codec)
        r.start()
        ok = await r.wait(lambda: r.c.up and r.c.codec_known.is_set())
        check("connect -> hello -> ready", ok and r.links[:1] == [(True, "")],
              f"codec={r.c.codec} wake={(r.c.ready or {}).get('wake')}")
        check(f"codec negotiated as requested ({a.codec})", r.c.codec == a.codec)
        check("first state is idle", "state" in r.types() and
              next(m for m in r.msgs if m["type"] == "state")["s"] == "idle")
        await asyncio.sleep(5)
        check("heartbeat keeps the link up (3 beats)", r.c.up and r.c.sessions_opened == 1)

        # 3. a wake, a spoken request, the reply, the follow-up window
        mark = len(r.msgs)
        r.audio = 0
        await r.c.send({"type": "wake", "word": "hey_anve", "score": 0.9})
        for i in range(75):                         # 1.6 s "speech" + 1.4 s silence
            await r.c.send_audio(frame(i < 40, i))
            await asyncio.sleep(0.01)               # faster than real time, like a burst
        ok = await r.wait(lambda: "listening" in r.types(mark), 10)
        seq = r.types(mark)
        states = [m["s"] + (":cloud" if m.get("cloud") else "") for m in r.msgs[mark:]
                  if m["type"] == "state"]
        check("wake -> earcon(wake)", seq[:1] == ["earcon"] and r.msgs[mark]["kind"] == "wake")
        check("states: listening, listening:cloud, thinking, speaking, listening",
              states == ["listening", "listening:cloud", "thinking", "speaking", "listening"],
              " ".join(states))
        check("reply audio arrived as 24 kHz PCM", r.audio >= 24000 * 2 * 2.5,
              f"{r.audio / 48000:.2f} s")
        check("audio_end then listening{until_s}", ok and seq.index("audio_end") < seq.index("listening"))
        check("mock heard the request in audio time",
              any("request:" in s and 1.4 <= float(s.split("heard,")[1].split()[0]) <= 1.8
                  for s in mock_out), next(
                  (s.strip() for s in mock_out if "request:" in s), "no request line"))

        # 4. barge-in during a reply: `interrupt` -> stop_audio
        mark = len(r.msgs)
        await cmd("say")
        await r.wait(lambda: any(m["type"] == "state" and m["s"] == "speaking" for m in r.msgs[mark:]))
        await r.c.send({"type": "interrupt"})
        ok = await r.wait(lambda: "stop_audio" in r.types(mark), 3)
        check("interrupt -> stop_audio", ok)

        # 5. push-to-talk
        mark = len(r.msgs)
        await r.c.send({"type": "ptt.start"})
        for i in range(25):
            await r.c.send_audio(frame(True, i))
        await r.c.send({"type": "ptt.end"})
        ok = await r.wait(lambda: "audio_end" in r.types(mark), 10)
        check("ptt.start / frames / ptt.end -> reply", ok and any("25 frames" in s for s in mock_out))

        # 6. alarm, job, confirm.request, recording
        mark = len(r.msgs)
        await cmd("alarm")
        ok = await r.wait(lambda: "audio_end" in r.types(mark), 10)
        got = r.msgs[mark:mark + 2]
        check("alarm: earcon(alarm) then notice{announce, spoken}", ok and
              got[0].get("kind") == "alarm" and got[1].get("announce") is True)
        mark = len(r.msgs)
        await cmd("confirm")
        await cmd("rec on")
        ok = await r.wait(lambda: any(m["type"] == "state" and m.get("rec") for m in r.msgs[mark:]), 5)
        check("confirm.request and recording start (state.rec = 1)", ok and
              "confirm.request" in r.types(mark) and "confirm.result" in r.types(mark))
        for i in range(50):
            await r.c.send_audio(frame(True, i))
        await r.c.send({"type": "record.stop"})
        ok = await r.wait(lambda: "recording" in r.types(mark), 5)
        check("record.stop -> recording{processing}; frames went to the recorder", ok and
              any("recording stopped: 2.0 s" in s for s in mock_out))

        # 7. the link drops without a close: reconnect after the first backoff step (1 s)
        t0 = time.monotonic()
        await cmd("drop")
        await r.wait(lambda: not r.c.up, 5)
        ok = await r.wait(lambda: r.c.up and r.c.sessions_opened == 2, 6)
        check("dropped link -> reconnect with backoff", ok, f"{time.monotonic() - t0:.1f} s, "
              f"down reason {next((w for u, w in r.links if not u), '')!r}")

        # 8. a silent link (no pong, no frames): detected by the heartbeat and torn down
        mock.send_signal(19)                         # SIGSTOP the mock
        t0 = time.monotonic()
        ok = await r.wait(lambda: not r.c.up, 20)
        check("silent server -> link declared dead by the heartbeat", ok,
              f"after {time.monotonic() - t0:.1f} s (heartbeat 2 s)")
        mock.send_signal(18)                         # SIGCONT
        ok = await r.wait(lambda: r.c.up, 40)
        check("server back -> recovers by itself", ok, f"connects={r.c.sessions_opened}")

        # 9. `closing` with a reason that needs a person: stay down
        await cmd("close rate")
        await r.wait(lambda: r.c.halted != "", 5)
        n = r.c.sessions_opened
        await asyncio.sleep(3)
        check("closing{rate} -> halted, no reconnect", r.c.halted == "rate" and
              r.c.sessions_opened == n)
        r.c.resume()
        ok = await r.wait(lambda: r.c.up, 5)
        check("resume() (a person's action) -> connects again", ok)
        await r.stop()
    finally:
        mock.terminate()
        pump_task.cancel()
    strict = [s for s in mock_out if "!!" in s]
    check("no audio was sent while idle (mode=wake rule)", not strict, f"{len(strict)} violations")
    print(f"\n{sum(RESULTS)}/{len(RESULTS)} checks passed")
    if not all(RESULTS):
        print("--- mock server output ---")
        print("\n".join(mock_out[-40:]))
    return 0 if all(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
