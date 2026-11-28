"""Recording a meeting in the room through the ANVE Box cloud (the team's handover of
27 Nov 2026, "meetings on the box", 5.3b; part D of the box-cloud agreement, D95 to D98).

The connection, the proof, keeping pieces on the card until the cloud has them, trying
again after a drop: all of that is the team's own code, vendored unchanged
(`anvebox_agent.cloud.meeting.MeetingRecording`). This module is the speaker's part:

  * `MeetingRecorder`: takes the microphone 40 ms at a time, cuts it into pieces of 55 s
    by SAMPLE COUNT (no overlap, no gap: `start_ms` is the samples before the piece), turns
    each piece into Ogg Opus (ffmpeg, 24 kbit/s, about 10 MB an hour; WAV if that fails,
    about 115 MB an hour) off the event loop, and hands it to `add_piece()` in order.
    It also keeps the last seconds of sound so that, after the wake word, the 3 s that
    followed can be sent as a command clip ("mark that", "stop recording"): a copy, the
    meeting itself is not touched.
  * `leftovers()`: at start-up, a meeting folder that still holds something (the power
    went, the network was down at the end) is given back to `MeetingRecording`, which
    sends what is there. No more sound will come for it, so it is told to stop.
  * `room_for()`: is there space on the card for a whole 3 hour meeting?

Rules kept here: the sound of a meeting goes to the ANVE Box cloud and nowhere else; it is
never logged; nothing of it stays on the card once the cloud has it (`MeetingRecording`
deletes each piece on "got" and clears the folder at the end).
"""
import asyncio
import io
import logging
import shutil
import subprocess
import time
import wave
from collections import deque
from pathlib import Path

from anvebox_agent.cloud.meeting import MeetingRecording

log = logging.getLogger("anve.meetings")
RATE = 16000
PIECE_S = 55                       # seconds a piece, as the cloud expects (D95)
COMMAND_S = 3.0                    # seconds after the wake word sent as a command clip (D96)
LONGEST_S = 3 * 3600               # the cloud's limit for one meeting
MB_AN_HOUR = {"ogg": 11, "wav": 116}
#: `ended` reasons after which the cloud will never take what is left: it is deleted here
#: ("the meeting had already ended", "not a meeting this box started")
NEVER = ("ended", "unknown_meeting")
KEEP_DAYS = 7                      # pieces the cloud keeps refusing are not kept longer


def clear(folder: Path):
    for p in Path(folder).iterdir():
        if p.is_file():
            p.unlink(missing_ok=True)


def wav_bytes(pcm: bytes) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)
    return out.getvalue()


def encode(pcm: bytes, want: str = "ogg") -> tuple[str, bytes]:
    """One piece as ("ogg", Ogg Opus) if ffmpeg can, else ("wav", 16-bit PCM)."""
    if want == "ogg" and shutil.which("ffmpeg"):
        try:
            p = subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "s16le", "-ar", str(RATE), "-ac", "1",
                 "-i", "pipe:0", "-c:a", "libopus", "-b:a", "24k", "-application", "voip", "-f", "ogg", "pipe:1"],
                input=pcm, capture_output=True, timeout=60)
            if p.returncode == 0 and len(p.stdout) > 200:
                return "ogg", p.stdout
            log.warning("ffmpeg could not make Ogg Opus (exit %s): this piece goes as WAV", p.returncode)
        except (OSError, subprocess.SubprocessError) as e:
            log.warning("ffmpeg failed (%s): this piece goes as WAV", type(e).__name__)
    return "wav", wav_bytes(pcm)


def room_for(folder: Path, fmt: str = "ogg", hours: float = 3.0) -> tuple[bool, int, int]:
    """(enough?, MB free, MB a meeting of `hours` may need if the cloud takes nothing
    for the whole of it, with a quarter to spare)."""
    folder.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(folder).free // (1024 * 1024)
    need = int(MB_AN_HOUR.get(fmt, 116) * hours * 1.25) + 50
    return free >= need, int(free), need


class MeetingRecorder:
    def __init__(self, cloud: str, identity, root: Path | str, *, meeting: str | None = None,
                 on_event=None, fmt: str = "ogg", piece_s: float = PIECE_S,
                 recording_factory=MeetingRecording, encoder=encode):
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        root.chmod(0o700)                                 # waiting meeting sound: this user only
        self.folder = root / time.strftime("%Y%m%d-%H%M%S")
        self.fmt, self._encode = fmt, encoder
        self.piece_n = int(piece_s * RATE) * 2            # bytes in a full piece
        self.rec = recording_factory(cloud, identity, self.folder, meeting=meeting, on_event=on_event)
        self.samples = 0                                  # the meeting's clock: samples taken so far
        self._piece = bytearray()
        self._piece_start = 0                             # in samples
        self._seq = 0
        self._tail: deque = deque(maxlen=250)             # the last 10 s: (first sample, frame)
        self._ship = asyncio.Lock()                       # pieces reach the card in order
        self._pending: set = set()
        self.stopped = False
        self.stats = {"pieces": 0, "bytes": 0, "format": ""}

    @property
    def started(self) -> asyncio.Event:
        return self.rec.started

    @property
    def now_ms(self) -> int:
        return self.samples * 1000 // RATE

    def feed(self, frame: bytes):
        """40 ms of the room. Nothing is taken after stop()."""
        if self.stopped or not frame:
            return
        self._tail.append((self.samples, frame))
        self._piece.extend(frame)
        self.samples += len(frame) // 2
        if len(self._piece) >= self.piece_n:
            self._cut(self.piece_n)

    def _cut(self, n: int):
        pcm, self._piece = bytes(self._piece[:n]), self._piece[n:]
        seq, start = self._seq, self._piece_start
        self._seq += 1
        self._piece_start += len(pcm) // 2
        t = asyncio.create_task(self._ship_piece(seq, start, pcm))
        self._pending.add(t)
        t.add_done_callback(self._pending.discard)

    async def _ship_piece(self, seq: int, start: int, pcm: bytes):
        async with self._ship:
            try:
                fmt, sound = await asyncio.to_thread(self._encode, pcm, self.fmt)
                self.rec.add_piece(seq, start * 1000 // RATE, (len(pcm) // 2) * 1000 // RATE, sound, fmt)
                self.stats["pieces"] += 1
                self.stats["bytes"] += len(sound)
                self.stats["format"] = fmt
                log.info("piece %d on the card (%.0f s, %d kB, %s)", seq, len(pcm) / 2 / RATE, len(sound) // 1024, fmt)
            except Exception:                                # noqa: BLE001
                log.exception("piece %d could not be kept", seq)

    def mark(self) -> int:
        """A tap: a mark here. Returns where (ms into the meeting)."""
        at = self.now_ms
        self.rec.mark(at)
        return at

    async def command(self, at_sample: int) -> str:
        """The 3 s after the wake word (heard at `at_sample` of the meeting) go to the
        cloud to be understood: "mark", "stop", "nothing" or "unsent"."""
        at_sample = max(0, min(at_sample, self.samples))
        want = at_sample + int(COMMAND_S * RATE)
        for _ in range(100):                               # until those 3 s have been heard
            if self.samples >= want or self.stopped:
                break
            await asyncio.sleep(0.05)
        pcm = b"".join(f for s, f in list(self._tail) if s + len(f) // 2 > at_sample and s < want)
        if len(pcm) < RATE:                                # under half a second: nothing to send
            return "unsent"
        return await self.rec.command(at_sample * 1000 // RATE, wav_bytes(pcm))

    async def stop(self):
        """The last piece, then no more: the cloud ends the meeting once it has them all."""
        if self.stopped:
            return
        self.stopped = True
        if self._piece:
            self._cut(len(self._piece))
        if self._pending:
            await asyncio.gather(*list(self._pending), return_exceptions=True)
        self.rec.stop()
        log.info("recording stopped after %.0f s: %d pieces, %d kB (%s)", self.samples / RATE,
                 self.stats["pieces"], self.stats["bytes"] // 1024, self.stats["format"] or "nothing")

    async def run(self) -> dict:
        ended = await self.rec.run()
        try:                                              # the team's class emptied it: tidy up
            if not any(self.folder.iterdir()):
                self.folder.rmdir()
        except OSError:
            pass
        return ended


async def leftovers(cloud: str, identity, root: Path | str, *, recording_factory=MeetingRecording) -> int:
    """Meeting folders that still hold something, from before a power cut or a restart:
    hand each back to the team's class so its pieces go up, and stop it (the microphone
    was not recording while the speaker was off, so nothing more belongs to it).
    Returns how many were found; each runs until the cloud says it has ended."""
    root = Path(root)
    found = 0
    if not root.is_dir():
        return 0
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        if any(folder.iterdir()) and time.time() - folder.stat().st_mtime > KEEP_DAYS * 86400:
            # the cloud has refused this one for a week: meeting sound is not kept for ever
            log.warning("a meeting the cloud would not take for %d days is deleted from the card", KEEP_DAYS)
            clear(folder)
        if not any(folder.iterdir()):
            try:
                folder.rmdir()
            except OSError:
                pass
            continue
        found += 1
        rec = recording_factory(cloud, identity, folder)
        rec.stop()
        log.info("a meeting from before the restart still has %d file(s) on the card: sending them",
                 sum(1 for _ in folder.iterdir()))

        async def finish(rec=rec, folder=folder):
            ended = await rec.run()
            log.info("that earlier meeting is over: %s", ended.get("reason"))
            if ended.get("reason") in NEVER:
                clear(folder)
            if not any(folder.iterdir()):
                try:
                    folder.rmdir()
                except OSError:
                    pass
        asyncio.create_task(finish())
    return found
