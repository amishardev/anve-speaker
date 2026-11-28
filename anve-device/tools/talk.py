"""Talks to ANVE through a box's voice connection from this computer: a recorded
question goes in as the box's microphone, and ANVE's spoken answer is kept as
a sound file (docs/box-cloud-agreement.md, part C). It is for trying a cloud's
side before a real box uses it, and an example for the box's own code, which
does the same with its microphone and speaker.

    uv run --project device python tools/box-voice/talk.py \\
        --cloud https://box.anveai.com --identity <identity.json> \\
        --say question.wav --out answer.wav

The question is a WAV file of 16 kHz, mono, 16-bit sound. The box must be one
the cloud knows (it connected on the box link once) and added to an account:
the talk is that account's, like a talk at the box itself.
"""

from __future__ import annotations

import argparse
import asyncio
import time
import wave
from pathlib import Path

from anvebox_agent.cloud.voice import IN_RATE, OUT_RATE, VoiceTalk
from anvebox_agent.identity import load

PIECE_S = 0.04  # 40 ms of sound a message, as a microphone sends it
ANSWER_WAIT_S = 20.0  # at most this long for ANVE's answer after the question
QUIET_END_S = 1.5  # an answer is over once nothing more has come for this long


def read_question(path: Path) -> bytes:
    with wave.open(str(path), "rb") as sound:
        if (sound.getframerate(), sound.getnchannels(), sound.getsampwidth()) != (IN_RATE, 1, 2):
            raise SystemExit(f"{path} must be a WAV file of 16 kHz, mono, 16-bit sound")
        return sound.readframes(sound.getnframes())


def write_answer(path: Path, pcm: bytes) -> None:
    with wave.open(str(path), "wb") as sound:
        sound.setframerate(OUT_RATE)
        sound.setnchannels(1)
        sound.setsampwidth(2)
        sound.writeframes(pcm)


class Listener:
    """Shows the talk as it goes: the words of both sides a turn at a time."""

    def __init__(self) -> None:
        self.heard = self.said = ""
        self.answered = False  # ANVE has finished a turn in which it said something
        self.last = time.monotonic()  # the last sound or message from the cloud
        self.sound = bytearray()

    def on_sound(self, piece: bytes) -> None:
        self.sound.extend(piece)
        self.last = time.monotonic()

    def on_event(self, event: dict) -> None:
        self.last = time.monotonic()
        kind = event.get("type")
        if kind == "ready":
            print(f"ANVE is listening (AI: {event.get('ai')}, up to {event.get('limit')} s)")
        elif kind == "heard":
            self.heard += event.get("text", "")
        elif kind == "said":
            self.said += event.get("text", "")
        elif kind == "action":
            print(f"  ANVE did: {event['action']['summary']}")
        elif kind == "rule":
            print(f"  ANVE proposed a rule: {event['rule']['summary']}")
        elif kind == "interrupted":
            print("  (the person cut in)")
        elif kind == "turn":
            if self.heard.strip():
                print(f"  you: {self.heard.strip()}")
            if self.said.strip():
                print(f"  ANVE: {self.said.strip()}")
                self.answered = True
            self.heard = self.said = ""
        elif kind == "ended":
            print(f"ended: {event.get('reason')}. {event.get('text') or ''}".rstrip(". ").rstrip())


async def talk(cloud: str, identity_path: Path, question: Path, out: Path) -> int:
    identity = load(identity_path)
    pcm = read_question(question)
    listener = Listener()
    voice = VoiceTalk(cloud, identity, on_sound=listener.on_sound, on_event=listener.on_event)
    task = asyncio.create_task(voice.run())
    ready = asyncio.create_task(voice.ready.wait())
    await asyncio.wait({task, ready}, return_when=asyncio.FIRST_COMPLETED)
    if task.done():  # it ended before ANVE listened: refused, unclaimed, unreachable...
        ready.cancel()
        ended = task.result()
        if ended.get("reason") in ("refused", "unreachable", "failed", "dropped"):
            print(f"ended: {ended['reason']}. {ended.get('text', '')}")
        return 1

    # The question, then quiet, in real time, as a microphone sends them.
    step = int(IN_RATE * PIECE_S) * 2
    started, sent = time.monotonic(), 0

    async def send(piece: bytes) -> None:
        nonlocal sent
        await voice.send_sound(piece)
        sent += 1
        await asyncio.sleep(max(0.0, started + sent * PIECE_S - time.monotonic()))

    for at in range(0, len(pcm), step):
        await send(pcm[at:at + step])
    asked = time.monotonic()
    quiet = bytes(step)
    while not task.done() and time.monotonic() - asked < ANSWER_WAIT_S:
        if listener.answered and time.monotonic() - listener.last > QUIET_END_S:
            break
        await send(quiet)

    await voice.end()
    ended = await asyncio.wait_for(task, 15)
    if listener.sound:
        write_answer(out, bytes(listener.sound))
        print(f"ANVE's voice: {len(listener.sound) / 2 / OUT_RATE:.1f} s, in {out}")
    return 0 if ended.get("reason") == "hung_up" and listener.answered else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--cloud", required=True, help="the cloud's address, like https://box.anveai.com")
    parser.add_argument("--identity", required=True, type=Path, help="the box's identity.json (secret: keep it private)")
    parser.add_argument("--say", required=True, type=Path, help="the question: a 16 kHz, mono, 16-bit WAV file")
    parser.add_argument("--out", type=Path, default=Path("answer.wav"), help="where ANVE's spoken answer goes")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(talk(args.cloud, args.identity, args.say, args.out)))


if __name__ == "__main__":
    main()
