"""Records a meeting through a box's meeting connection from this computer: a
recorded meeting goes in as the box's microphone, cut into 55-second pieces
the way the box cuts them (docs/box-cloud-agreement.md, part D; 5.3 in
docs/module-5-3-plan.md). It is for trying a cloud's side before a real box
uses it, and an example for the box's own code.

    uv run --project device python tools/box-meeting/record.py \\
        --cloud https://box.anveai.com --identity <identity.json> \\
        --meeting meeting.wav --mark 90 --command 300 --drop 600:120

The meeting is a WAV file of mono, 16-bit sound (16 kHz, as the box records).
The box must be one the cloud knows (it connected on the box link once),
added to an account that may record meetings: the meeting is that account's,
in its chats a few minutes after it ends.

--mark S       a tap S seconds in (repeat for more)
--command S    the 3 seconds from S, sent as what followed the wake word
               ("ANVE, mark that" or "ANVE, stop recording" spoken there)
--drop S:N     the connection is cut S seconds in, for N seconds of meeting:
               pieces wait on the card meanwhile, as on the box
--meeting-id   carry on a meeting box voice started ("ANVE, record this meeting")

Pieces go up as fast as the network takes them, not in real time.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import tempfile
import wave
from pathlib import Path

from anvebox_agent.cloud.meeting import MeetingRecording
from anvebox_agent.identity import load

PIECE_S = 55  # seconds a piece, as the box cuts them (D95)
COMMAND_S = 3  # seconds after the wake word a command clip holds (D96)


def read_meeting(path: Path) -> tuple[bytes, int]:
    with wave.open(str(path), "rb") as sound:
        if (sound.getnchannels(), sound.getsampwidth()) != (1, 2):
            raise SystemExit(f"{path} must be a WAV file of mono, 16-bit sound")
        return sound.readframes(sound.getnframes()), sound.getframerate()


def wav(pcm: bytes, rate: int) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as sound:
        sound.setframerate(rate)
        sound.setnchannels(1)
        sound.setsampwidth(2)
        sound.writeframes(pcm)
    return out.getvalue()


def show(event: dict) -> None:
    kind = event.get("type")
    if kind == "started":
        print(f"recording meeting {event['meeting']} (up to {event['limit']} s; {event['left']} s left today)")
    elif kind == "heard":
        print(f"  at {event['at_ms'] / 1000:.0f} s the cloud heard: {event['command']}")
    elif kind == "warning":
        print(f"  {event['left']} s left before a limit")
    elif kind == "refused":
        print(f"  the cloud refused a {event.get('for')}: {event.get('error')}")
    elif kind == "ended":
        print(f"ended: {event.get('reason')}. {event.get('text') or ''}".rstrip(". ").rstrip())


async def record(cloud: str, identity_path: Path, meeting: Path, marks: list[float], commands: list[float],
                 drop: tuple[float, float] | None, meeting_id: str | None) -> int:
    identity = load(identity_path)
    pcm, rate = read_meeting(meeting)
    per_second = rate * 2
    pieces = [(seq, at) for seq, at in enumerate(range(0, len(pcm), PIECE_S * per_second))]
    with tempfile.TemporaryDirectory(prefix="anve-meeting-") as card:
        recording = MeetingRecording(cloud, identity, card, meeting=meeting_id, on_event=show)
        task = asyncio.create_task(recording.run())
        started = asyncio.create_task(recording.started.wait())
        await asyncio.wait({task, started}, return_when=asyncio.FIRST_COMPLETED)
        if task.done():
            started.cancel()
            print(f"ended: {task.result().get('reason')}. {task.result().get('text', '')}")
            return 1
        print(f"{len(pieces)} pieces of {PIECE_S} s, {len(pcm) / per_second:.0f} s in all")

        stopping = False
        for seq, at in pieces:
            start_s = at / per_second
            if drop and start_s >= drop[0] and task is not None:
                print(f"  the connection drops at {start_s:.0f} s; pieces wait on the card")
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                task = None
            if drop and task is None and start_s >= drop[0] + drop[1]:
                print(f"  the connection is back at {start_s:.0f} s")
                recording = MeetingRecording(cloud, identity, card, on_event=show)
                task = asyncio.create_task(recording.run())
                await recording.started.wait()
            piece = pcm[at:at + PIECE_S * per_second]
            recording.add_piece(seq, int(start_s * 1000), int(len(piece) / per_second * 1000), wav(piece, rate))
            end_s = start_s + len(piece) / per_second
            for mark in [m for m in marks if start_s <= m < end_s]:
                recording.mark(int(mark * 1000))
                print(f"  a tap at {mark:.0f} s")
            for said in [c for c in commands if start_s <= c < end_s]:
                clip_at = int(said * per_second) // 2 * 2
                clip = wav(pcm[clip_at:clip_at + COMMAND_S * per_second], rate)
                if task is None:
                    print(f"  at {said:.0f} s the wake word, but no connection: the button still works")
                    continue
                if await recording.command(int(said * 1000), clip) == "stop":
                    print("  stopping after this piece")
                    stopping = True
            if stopping:
                break
        if task is None:
            recording = MeetingRecording(cloud, identity, card, on_event=show)
            task = asyncio.create_task(recording.run())
        recording.stop()
        ended = await asyncio.wait_for(task, 600)
    return 0 if ended.get("reason") == "stopped" else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--cloud", required=True, help="the cloud's address, like https://box.anveai.com")
    parser.add_argument("--identity", required=True, type=Path, help="the box's identity.json (secret: keep it private)")
    parser.add_argument("--meeting", required=True, type=Path, help="the meeting: a mono, 16-bit WAV file")
    parser.add_argument("--mark", type=float, action="append", default=[], help="a tap this many seconds in")
    parser.add_argument("--command", type=float, action="append", default=[],
                        help="the wake word this many seconds in: the next 3 s go to the cloud as a command")
    parser.add_argument("--drop", help="S:N, the connection cut S seconds in for N seconds")
    parser.add_argument("--meeting-id", help="a meeting box voice started, to carry on")
    args = parser.parse_args()
    drop = tuple(float(part) for part in args.drop.split(":")) if args.drop else None
    raise SystemExit(asyncio.run(record(args.cloud, args.identity, args.meeting, args.mark, args.command, drop,
                                        args.meeting_id)))


if __name__ == "__main__":
    main()
