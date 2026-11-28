#!/usr/bin/env python3
"""boxvoice.BoxVoice against a scripted stand-in for the team's VoiceTalk: no network, no
identity, no sound. Checks what the speaker is told and when the talk is hung up.

    python tools/boxvoice_test.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from anve_device.boxvoice import BoxVoice  # noqa: E402

FRAME = bytes(1280)


class FakeTalk:
    """Opens after 0.3 s; the test then plays the cloud's part through `cloud()`."""
    made = []

    def __init__(self, cloud, identity, *, on_sound=None, on_event=None):
        self.on_sound, self.on_event = on_sound, on_event
        self.ready, self.ended, self.sent, self.hung_up = asyncio.Event(), None, [], False
        self._done = asyncio.Event()
        self.refuse = None
        FakeTalk.made.append(self)

    async def run(self):
        await asyncio.sleep(0.3)
        if self.refuse:
            self.ended = self.refuse
            return self.ended
        self.ready.set()
        await self.on_event({"type": "ready", "limit": 600})
        await self._done.wait()
        self.ended = {"type": "ended", "reason": "hung_up", "text": "", "seconds": 3}
        await self.on_event(self.ended)
        return self.ended

    async def send_sound(self, piece):
        if not self.ready.is_set() or self.ended is not None:
            return False
        self.sent.append(piece)
        return True

    async def end(self):
        self.hung_up = True
        self._done.set()


async def main():
    got, sound, busy = [], [], [False]

    async def on_json(m):
        got.append(m)

    async def on_audio(p):
        sound.append(p)

    async def on_link(up, why):
        got.append({"type": "link", "up": up})

    bv = BoxVoice("https://cloud.example", None, follow_up_s=0.8, silent_s=1.0, on_json=on_json,
                  on_audio=on_audio, on_link=on_link, room_busy=lambda: busy[0], talk_factory=FakeTalk)
    runner = asyncio.create_task(bv.run())
    await asyncio.sleep(0.05)
    bad = []

    def check(name, ok):
        print(("ok    " if ok else "WRONG ") + name)
        if not ok:
            bad.append(name)

    def states():
        return [m["s"] for m in got if m["type"] == "state"]

    check("says the link is up at once, with no connection", got == [{"type": "link", "up": True}])
    check("no microphone is taken before a wake", await bv.send_audio(FRAME) is False)

    # 1. a wake, words spoken while the talk opens, an answer, a follow-up, then silence
    await bv.send({"type": "wake"})
    for _ in range(5):
        await bv.send_audio(FRAME)                          # spoken before "ready"
    t = FakeTalk.made[-1]
    check("nothing is sent before the cloud is ready", t.sent == [])
    await asyncio.sleep(0.5)
    check("the buffered microphone goes out once it is ready", len(t.sent) == 5)
    check("ready -> listening", states() == ["listening"])
    await t.on_event({"type": "heard", "text": "is my box "})
    await t.on_event({"type": "heard", "text": "online"})
    heard = "".join(m["text"] for m in got if m["type"] == "transcript.in")
    check("heard -> transcript.in, in pieces", heard == "is my box online")
    check("one request = one 'listening' (the text is not cleared half way)", states() == ["listening", "listening"])
    await t.on_sound(b"\x01\x00" * 100)
    await t.on_sound(b"\x02\x00" * 100)
    check("the first sound -> speaking, and the sound is played", states()[-1] == "speaking" and len(sound) == 2)
    busy[0] = True                                           # the answer is still in the room
    await t.on_event({"type": "turn"})
    check("turn -> turn.done and listening again", got[-3]["type"] == "turn.done" and states()[-1] == "listening")
    await asyncio.sleep(1.2)
    check("no hang-up while the answer is still playing", not t.hung_up)
    busy[0] = False
    await asyncio.sleep(0.4)
    await t.on_event({"type": "heard", "text": "and the lights"})   # a follow-up, no wake word
    check("a follow-up is a new request (listening again)", states()[-1] == "listening" and not t.hung_up)
    await t.on_sound(b"\x03\x00" * 100)
    await t.on_event({"type": "turn"})
    await asyncio.sleep(1.4)
    check("silence after the answer hangs up", t.hung_up)
    await asyncio.sleep(0.1)
    check("ended -> idle, and the talk is gone", states()[-1] == "idle" and bv.talk is None)

    # 2. a wake with nothing said at all
    got.clear()
    await bv.send({"type": "wake"})
    t = FakeTalk.made[-1]
    await asyncio.sleep(1.8)
    check("a talk in which nothing is heard is hung up", t.hung_up and states()[-1] == "idle")

    # 3. a local skill (or a spoken stop) takes the request: the rest of the answer is dropped
    got.clear(); sound.clear()
    await bv.send({"type": "wake"})
    t = FakeTalk.made[-1]
    await asyncio.sleep(0.4)
    await t.on_event({"type": "heard", "text": "play some jazz"})
    await bv.send({"type": "interrupt"})
    await t.on_sound(b"\x04\x00" * 100)
    check("interrupt hangs up and ANVE's answer is not played", t.hung_up and sound == [])
    check("typed turns are refused (the speaker speaks its own lines)", await bv.send({"type": "text", "text": "x"}) is False)
    await asyncio.sleep(0.1)

    # 4. the cloud turns the box away
    got.clear()
    orig = FakeTalk.__init__

    def refusing(self, *a, **k):
        orig(self, *a, **k)
        self.refuse = {"type": "ended", "reason": "unclaimed", "text": "This box isn't added to an account yet.", "seconds": 0}
    FakeTalk.__init__ = refusing
    await bv.send({"type": "wake"})
    await asyncio.sleep(0.5)
    FakeTalk.__init__ = orig
    notice = [m for m in got if m["type"] == "notice"]
    check("a refusal with words for the person becomes a spoken notice, then idle",
          len(notice) == 1 and notice[0]["announce"] and states()[-1] == "idle")

    await bv.close()
    await runner
    print(f"{'ALL OK' if not bad else str(len(bad)) + ' WRONG'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
