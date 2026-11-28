#!/usr/bin/env python3
"""meetings.MeetingRecorder with a stand-in for the team's MeetingRecording: no network,
no identity, no microphone. Checks the cutting (by sample count, no gap, no overlap), the
order pieces reach the card in, marks, the command clip, stopping and the leftovers.

    python tools/meeting_test.py
"""
import asyncio
import io
import sys
import tempfile
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from anve_device import meetings  # noqa: E402

FRAME_N = 640                                              # samples in 40 ms


class FakeRecording:
    made = []

    def __init__(self, cloud, identity, folder, *, meeting=None, on_event=None):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.meeting, self.on_event = meeting, on_event
        self.started = asyncio.Event()
        self.pieces, self.marks, self.commands, self.stopped = [], [], [], False
        self._done = asyncio.Event()
        FakeRecording.made.append(self)

    def add_piece(self, seq, start_ms, duration_ms, sound, format="wav"):
        self.pieces.append((seq, start_ms, duration_ms, format, sound))

    def mark(self, at_ms):
        self.marks.append(at_ms)

    async def command(self, at_ms, clip, format="wav"):
        self.commands.append((at_ms, clip))
        return "mark"

    def stop(self):
        self.stopped = True
        self._done.set()

    async def run(self):
        self.started.set()
        await self._done.wait()
        return {"type": "ended", "reason": "stopped", "text": ""}


def frame(i: int) -> bytes:
    """A frame whose every sample says which frame it is, so order and gaps show."""
    return (i % 30000).to_bytes(2, "little", signed=True) * FRAME_N


def slow_wav(pcm: bytes, want: str):
    """An encoder that is slower for the first piece: order must still hold."""
    import time
    time.sleep(0.15 if pcm[:2] == b"\x00\x00" else 0.01)
    return "wav", meetings.wav_bytes(pcm)


async def main():
    bad = []

    def check(name, ok):
        print(("ok    " if ok else "WRONG ") + name)
        if not ok:
            bad.append(name)

    root = Path(tempfile.mkdtemp())
    # pieces of 2 s (50 frames) so the test is quick; 5.3 pieces' worth of sound
    rec = meetings.MeetingRecorder("https://cloud.example", None, root, meeting="m1", piece_s=2.0,
                                   recording_factory=FakeRecording, encoder=slow_wav)
    fake = FakeRecording.made[-1]
    run = asyncio.create_task(rec.run())
    n = 265
    for i in range(n):
        rec.feed(frame(i))
        if i == 120:
            at = rec.mark()
            check("a mark is where the microphone's samples say (4.84 s)", at == 121 * 40)
        if i % 25 == 0:
            await asyncio.sleep(0)
    cmd = asyncio.create_task(rec.command(200 * FRAME_N))  # the wake word ended at frame 200
    await asyncio.sleep(0.05)
    for i in range(n, n + 80):                             # the 3 s after it arrive
        rec.feed(frame(i))
    check("the wake word during a meeting -> the cloud's answer", await cmd == "mark")
    at_ms, clip = fake.commands[0]
    with wave.open(io.BytesIO(clip)) as w:
        pcm = w.readframes(w.getnframes())
    first = int.from_bytes(pcm[:2], "little", signed=True)
    check("the command clip is the 3 s from the wake word (frames 200..274), a copy",
          at_ms == 8000 and first == 200 and len(pcm) == 75 * FRAME_N * 2)
    await rec.stop()
    rec.feed(frame(999))                                   # after stop: not taken
    ended = await run
    seqs = [p[0] for p in fake.pieces]
    check("pieces reach the card in order though the first was slowest to encode", seqs == list(range(len(seqs))))
    check("every piece but the last is exactly 2 s", all(p[2] == 2000 for p in fake.pieces[:-1]))
    starts_ok = all(fake.pieces[k][1] == sum(p[2] for p in fake.pieces[:k]) for k in range(len(fake.pieces)))
    check("no gap and no overlap: each start is the sum of what came before", starts_ok)
    total = sum(p[2] for p in fake.pieces)
    check("all the sound fed is in the pieces, and nothing after stop()", total == (n + 80) * 40)
    all_pcm = b""
    for p in fake.pieces:
        with wave.open(io.BytesIO(p[4])) as w:
            all_pcm += w.readframes(w.getnframes())
    ids = [int.from_bytes(all_pcm[i:i + 2], "little", signed=True) for i in range(0, len(all_pcm), FRAME_N * 2)]
    check("the samples are the ones fed, in order", ids == list(range(n + 80)))
    check("stop() tells the team's class, and run() returns the cloud's end", fake.stopped and ended["reason"] == "stopped")
    check("the meeting id from the voice talk is passed on", fake.meeting == "m1")

    # Ogg Opus through ffmpeg, if this computer has it (the Pi does)
    import shutil
    if shutil.which("ffmpeg"):
        import math, struct
        tone = b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / 16000))) for i in range(16000 * 5))
        fmt, sound = meetings.encode(tone, "ogg")
        check("ffmpeg makes Ogg Opus, much smaller than WAV", fmt == "ogg" and sound[:4] == b"OggS" and len(sound) < len(tone) / 4)
    else:
        print("skip  ffmpeg is not on this computer: Ogg Opus is checked on the Pi")

    # leftovers after a power cut
    root2 = Path(tempfile.mkdtemp())
    (root2 / "old").mkdir()
    (root2 / "old" / "piece-00000.wav").write_bytes(b"x")
    (root2 / "empty").mkdir()
    found = await meetings.leftovers("https://cloud.example", None, root2, recording_factory=FakeRecording)
    await asyncio.sleep(0.05)
    check("a folder with sound left is handed back and told to stop; an empty one is removed",
          found == 1 and FakeRecording.made[-1].stopped and not (root2 / "empty").exists())
    ok, free, need = meetings.room_for(root2, "ogg")
    check("room on the card is measured in MB", isinstance(free, int) and need > 40)
    print("ALL OK" if not bad else f"{len(bad)} WRONG")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
