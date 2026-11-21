"""Taking turns: which microphone frames go to ANVE during one request, and when.

The platform decides when a request has ended, and it is not to be changed: once it has
heard a little speech, 0.8 s of silence ends the request (handsfree.py, END_SILENCE_S), and
it counts that silence in the audio it RECEIVES, frame by frame. So the speaker can give a
person more time simply by choosing what it sends, and when:

  * After "Hey ANVE" nothing is sent until the person actually starts the request. Until
    19 Nov 2026 the speaker sent whatever followed the name: usually the tail of the name
    and then the silence of someone waiting for the chime. The platform heard "a little
    speech, then 0.8 s of silence" and went straight to thinking; there was no time to
    say anything. Now the person has `start_s` (4 s) from the chime to begin, and the
    first thing the platform hears is the start of their sentence.
  * While the person is talking, a pause is passed on only up to `pass_s` (0.6 s, safely
    under the platform's 0.8). If they go on, the rest of the pause is never sent, so a
    breath or a moment's thought does not end the request. If the pause reaches `pause_s`
    (1.4 s), what was held back is sent, the platform sees its silence, and the request
    ends: `pause_s` after the last word, however long the sentence was.

Only frames that are silent by BOTH measures are ever held back or dropped: the speech
detector says "no speech" and the level is down at the room's own noise. Quiet or distant
speech that the detector misses still has energy, so it is always sent.

`Turn` is plain logic with no audio in it, so it is tested without a microphone
(tools/turn_test.py).
"""
from __future__ import annotations

from collections import deque

FRAME_S = 0.04


class Turn:
    def __init__(self, start_s: float = 4.0, pause_s: float = 1.4, pass_s: float = 0.6,
                 run: int = 4, skip_s: float = 0.3, loud_s: float = 0.48, chime_s: float = 0.8):
        self.start_n = max(1, round(start_s / FRAME_S))
        self.pause_n = max(2, round(pause_s / FRAME_S))
        self.pass_n = max(1, min(self.pause_n - 1, round(pass_s / FRAME_S)))
        self.run_n = run                               # speech frames in a row = "they have started"
        self.skip_n = round(skip_s / FRAME_S)          # after the name: its tail is not a request
        # sound with energy that the speech detector does not call speech (a distant voice)
        # also counts as a start, once there has been this much of it, the chime being over
        self.loud_n, self.chime_n = round(loud_s / FRAME_S), round(chime_s / FRAME_S)
        # kept while waiting, sent when the person starts: long enough to hold a sentence
        # that began right after the name
        self._pre: deque = deque(maxlen=self.skip_n + max(self.run_n, self.loud_n) + 1)
        self.stats = {"held": 0, "dropped": 0, "waited": 0, "began": ""}
        self.start(waiting=False)

    def start(self, waiting: bool, loud_ok: bool = True):
        """A new request. `waiting`: it follows the wake word, so send nothing until the
        person begins. Without it (a follow-up, the button) frames pass from the start.
        `loud_ok`: False while music plays, when "sound with energy" proves nothing."""
        self.waiting, self.loud_ok = waiting, loud_ok
        self.stats["began"] = "wait" if waiting else "open"
        self.spoken = False            # the person has begun this request
        self.ended = False             # the closing pause has been sent
        self._pre.clear()
        self._held: list = []
        self._voiced = self._loud = self._quiet = self._waited = self._seen = 0

    def _take(self, lead: int = 6) -> list:
        """What was kept while waiting, from where the person's sentence begins: back over
        everything with sound in it (short gaps included), plus a little lead-in. The
        name's tail, further back behind real silence, is left out."""
        items = list(self._pre)
        self._pre.clear()
        i, gap = len(items) - 1, 0
        while i >= 0 and (items[i][1] or gap < 3):
            gap = 0 if items[i][1] else gap + 1
            i -= 1
        return [f for f, _ in items[max(0, i + 1 - lead):]]

    def feed(self, frame: bytes, voiced: bool, quiet: bool, live: bool = True) -> list:
        """One 40 ms frame in, the frames to send now out (often none, sometimes several).
        `voiced`: the speech detector's verdict. `quiet`: no speech AND down at the room's
        noise level. `live`: False for frames replayed from just before the chime, which
        do not use up the person's time to start."""
        self._seen += 1
        counts = voiced and self._seen > (self.skip_n if self.waiting else 0)
        self._voiced = self._voiced + 1 if counts else 0

        if self.waiting:
            self._pre.append((frame, voiced or not quiet))
            self._loud = self._loud + 1 if (self.loud_ok and not quiet and live and self._waited >= self.chime_n) else 0
            if self._voiced >= self.run_n or self._loud >= self.loud_n:
                # they have started: send from a moment before, so the first sound is whole
                self.stats["began"] = "voice" if self._voiced >= self.run_n else "loudness"
                self.waiting, self.spoken = False, True
                self._quiet = 0
                return self._take()
            if live:
                self._waited += 1
                self.stats["waited"] += 1
                if self._waited >= self.start_n:
                    # nobody spoke: let the platform have the frames so that it can say
                    # it did not catch anything
                    # ... and everything that was kept goes with them: if a request was
                    # spoken after all and not recognised as a start, it is not thrown away
                    self.waiting = False
                    self.stats["began"] = "timeout"
                    out = [f for f, _ in self._pre]
                    self._pre.clear()
                    return out
            return []

        if not self.spoken:
            if self._voiced >= self.run_n:
                self.spoken, self._quiet = True, 0
            return [frame]

        if not quiet:
            if self._held:                              # the pause is over: its held part is never sent
                self.stats["dropped"] += len(self._held)
                self._held = []
            self._quiet = 0
            return [frame]
        self._quiet += 1
        if self.ended or self._quiet <= self.pass_n:
            return [frame]
        if self._quiet < self.pause_n:
            self._held.append(frame)
            self.stats["held"] += 1
            return []
        out, self._held, self.ended = self._held + [frame], [], True     # the pause was long enough
        return out
