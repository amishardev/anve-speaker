#!/usr/bin/env python3
"""Turn-taking, tested without a microphone:   python tools/turn_test.py

Streams of labelled 40 ms frames go through `anve_device.turn.Turn` and then into a copy
of the platform's own rule for ending a request (handsfree.py: a request begins at 3
speech frames in a row, and ends once it has more than 0.2 s of speech followed by 0.8 s
of silence, counted in the frames it receives).

Frame labels:  S speech   . room silence   t the tail of the name "ANVE"
               q quiet speech the speech detector misses (it has energy, though)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from anve_device.turn import Turn  # noqa: E402

ENERGY = {"S": True, "t": True, "q": True, ".": False}       # what the platform's level rule hears
VOICED = {"S": True, "t": True, "q": False, ".": False}      # what the speaker's speech detector says
ok = total = 0


def check(name, cond, detail=""):
    global ok, total
    total += 1
    ok += bool(cond)
    print(("  ok   " if cond else "  FAIL ") + name + (f"   [{detail}]" if detail and not cond else ""))


def frames(text: str) -> list:
    """'S*25 .*10' -> labels; numbers are frame counts (25 frames = 1 s)."""
    out = []
    for part in text.split():
        label, _, n = part.partition("*")
        out += [label] * int(n or 1)
    return out


class Platform:
    """handsfree.py's await -> listen -> end, for device-side wake."""

    def __init__(self):
        self.state, self.run, self.voiced, self.silent = "await", 0, 0.0, 0.0
        self.got = []                      # every label that arrived
        self.request = []                  # the labels that made up the request

    def feed(self, label: str):
        self.got.append(label)
        loud = ENERGY[label]
        if self.state == "await":
            self.run = self.run + 1 if loud else 0
            if self.run >= 3:
                self.state, self.voiced, self.silent = "listen", 0.12, 0.0
                self.request = self.got[-3:]
        elif self.state == "listen":
            self.request.append(label)
            if loud:
                self.voiced, self.silent = self.voiced + 0.04, 0.0
            else:
                self.silent += 0.04
            if self.voiced > 0.2 and self.silent >= 0.8:
                self.state = "ended"


def run(stream: list, turn: Turn | None, backlog: int = 0):
    """Returns (platform, the stream index at which the request ended or None)."""
    p, ended_at = Platform(), None
    for i, label in enumerate(stream):
        out = [label] if turn is None else turn.feed(label, VOICED[label], not VOICED[label] and not ENERGY[label],
                                                     live=i >= backlog)
        for f in out:
            if p.state != "ended":
                p.feed(f)
        if p.state == "ended" and ended_at is None:
            ended_at = i
    return p, ended_at


def new(**kw) -> Turn:
    t = Turn(**kw)
    t.start(waiting=True)
    return t


print("after 'Hey ANVE', a person waits for the chime, then speaks")
# tail of the name 0.3 s, 1.6 s of waiting, a 2 s request, then silence
s = frames("t*8 .*40 S*50 .*60")
old, old_end = run(s, None)
check("the old way ends the request before the person has said a word",
      old.state == "ended" and old_end < 45 and "S" not in old.request, f"ended at frame {old_end}")
p, end = run(s, new())
check("now the request the platform hears is the person's sentence", p.request.count("S") == 50 and "t" not in p.got)
check("nothing is sent while the person has not begun, bar a short lead-in",
      p.got[:p.got.index("S")].count(".") <= 10 and len(p.got[:p.got.index("S")]) <= 10)
check("it ends 1.4 s after the last word", end is not None and abs((end - 97) * 0.04 - 1.4) <= 0.08, f"{(end - 97) * 0.04:.2f} s")

print("a pause in the middle of a sentence")
s = frames("t*5 .*20 S*30 .*25 S*30 .*60")            # a full second of thinking mid-sentence
old, _ = run(frames("S*30 .*25 S*30 .*60"), None)
check("the old way cuts the sentence at the pause", old.request.count("S") == 30)
p, end = run(s, new())
check("now both halves arrive as one request", p.request.count("S") == 60 and p.state == "ended")
check("the pause the platform sees is shorter than the one that ends a request",
      max(len(g) for g in "".join(p.request).strip(".").split("S")) * 0.04 < 0.8)

print("the request follows the name at once (\"Hey ANVE what's the weather\")")
s = frames("S*45 .*60")                                # no gap at all after the name
p, end = run(s, new(), backlog=30)                     # the first 1.2 s were said before the chime
check("the start of the sentence is not lost", p.got.count("S") == 45, f"{p.got.count('S')} of 45 speech frames arrived")
check("it still ends after the pause", p.state == "ended")

print("the name's tail is not a request")
p, end = run(frames("t*7 .*100"), new())               # 0.28 s of tail, then nobody speaks for 4 s
check("the tail alone starts nothing", "t" not in p.got and p.state == "await")
check("after 4 s of nobody speaking, frames flow again so the platform can give up", len(p.got) > 0)
t = new()
sent = sum(len(t.feed(".", False, True)) for _ in range(99))
check("... and not before 4 s", sent == 0)
check("what was said before the chime does not use up the 4 s",
      sum(len(new().feed(".", False, True, live=False)) for _ in range(1)) == 0 and
      (lambda u: sum(len(u.feed(".", False, True, live=False)) for _ in range(300)) == 0)(new()))

print("quiet speech the detector misses is never thrown away")
s = frames("t*5 .*15 S*20 q*30 S*20 .*60")
p, end = run(s, new())
check("every frame with a voice in it reaches the platform", p.got.count("q") == 30 and p.got.count("S") == 40)
t = new()
[t.feed("S", True, False) for _ in range(20)]
held = [t.feed("q", False, False) for _ in range(60)]
check("once the person has begun, frames with energy are sent at once, never held",
      all(len(h) == 1 for h in held) and t.stats["dropped"] == 0)
p, end = run(frames("t*5 .*30 q*50 .*60"), new())      # a distant voice the detector never calls speech
check("a voice the detector misses altogether still starts a request", p.got.count("q") == 50 and p.state == "ended",
      f"{p.got.count('q')} of 50 arrived, {p.state}")
p, end = run(frames("t*5 q*12 .*110"), new())          # the chime's own echo right after the name
check("the chime's echo right after the name starts nothing", "q" not in p.got[:1] and p.state == "await", p.state)

print("a follow-up (no wake word): the platform is listening already")
t = Turn()
t.start(waiting=False)
p, end = run(frames(".*30 S*40 .*20 S*20 .*60"), t)
check("frames pass from the start", p.got[:30] == ["."] * 30)
check("pauses are bridged the same way", p.request.count("S") == 60 and p.state == "ended")

print("other pause lengths")
for want in (1.0, 1.5, 4.0):
    p, end = run(frames("t*5 .*10 S*40 .*150"), new(pause_s=want))
    got = (end - 54) * 0.04 if end is not None else -1
    check(f"pause_s = {want}: ends {want} s after the last word", abs(got - want) <= 0.08, f"{got:.2f} s")
p, end = run(frames("S*40 .*30 S*10 .*150"), new(pause_s=1.4))
check("after the closing pause was sent, later frames still pass (nothing sticks)", len(p.got) > 0 and p.state == "ended")

print("what started the request is recorded, and music does not count as a voice")
t = new()
[t.feed("S", True, False) for _ in range(20)]
check("a voice: began by voice", t.stats["began"] == "voice")
t = Turn()
t.start(waiting=True, loud_ok=False)                   # music is playing
out = sum(len(t.feed("q", False, False)) for _ in range(90))
check("over music, sound with energy alone starts nothing", out == 0 and t.waiting)
out = sum(len(t.feed("q", False, False)) for _ in range(12))
check("... and on the timeout everything that was kept is sent, not just the end",
      out >= 20 and t.stats["began"] == "timeout", f"{out} frames")

print(f"\n{ok}/{total} passed")
sys.exit(0 if ok == total else 1)
