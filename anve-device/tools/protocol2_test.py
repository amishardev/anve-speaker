#!/usr/bin/env python3
"""Protocol 2 (the team's P4 handover), tested with stand-ins:   python tools/protocol2_test.py

  * the box link's side: the line to the speaker (speakerlink.py), the generic commands,
    what the speaker reports, its long press and wake word, its own tools, and the hello
    that says protocol 2, against a stand-in cloud that speaks part E as written;
  * the speaker's side: talks the cloud ends (part C's `ends: "cloud"`), a talk ANVE opens
    with the cloud's token, and how the speaker takes the box link's commands.

Needs the team's anvebox_agent on the import path, like the other tests here.
"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import websockets  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey  # noqa: E402

from anve_device import boxlink  # noqa: E402
from anve_device.speakerlink import SpeakerLink, SpeakerPort  # noqa: E402
from anvebox_agent.cloud.link import LinkTimings  # noqa: E402
from anvebox_agent.identity import b64u_decode, link_message, new_identity  # noqa: E402

ok = total = 0
FAST = LinkTimings(first_retry=0.1, max_retry=0.4, dead_after=3.0, open_timeout=2.0, poll=0.05, nudge_spacing=0.1)


def check(name, cond, detail=""):
    global ok, total
    total += 1
    ok += bool(cond)
    print(("  ok   " if cond else "  FAIL ") + name + (f"   [{detail}]" if detail and not cond else ""))


async def wait(what, seconds: float = 3.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        v = what()
        if v:
            return v
        await asyncio.sleep(0.02)
    return None


class Cloud2:
    """The cloud's side of part A, taking protocol 1 or 2 (part E)."""

    def __init__(self, public_key: str):
        self.public_key = public_key
        self.hellos, self.got = [], []
        self.ws = None

    async def start(self):
        self.server = await websockets.serve(self._conn, "127.0.0.1", 0)
        return f"http://127.0.0.1:{self.server.sockets[0].getsockname()[1]}"

    async def stop(self):
        self.server.close()
        await self.server.wait_closed()

    async def _conn(self, ws):
        nonce = os.urandom(12).hex()
        await ws.send(json.dumps({"type": "challenge", "nonce": nonce}))
        hello = json.loads(await ws.recv())
        Ed25519PublicKey.from_public_bytes(b64u_decode(self.public_key)).verify(
            b64u_decode(hello["signature"]), link_message(nonce, hello["device_id"]))
        if hello.get("protocol") not in (1, 2):
            await ws.close(4400, "protocol")
            return
        self.hellos.append(hello)
        self.ws = ws
        await ws.send(json.dumps({"type": "welcome", "owner": {"claimed": True},
                                  "server_time": "2026-11-28T10:00:00+00:00", "rotate_claim": False}))
        try:
            async for raw in ws:
                self.got.append(json.loads(raw))
        except websockets.ConnectionClosed:
            pass

    async def command(self, name: str, args: dict) -> dict:
        cid = os.urandom(4).hex()
        await self.ws.send(json.dumps({"type": "command", "id": cid, "name": name, "args": args}))
        return await wait(lambda: next((m for m in self.got if m.get("type") == "result" and m.get("id") == cid), None))


def last(cloud, kind, name=None):
    return next((m for m in reversed(cloud.got) if m.get("type") == kind and (name is None or m.get("name") == name)), None)


VOLUME_TOOL = {"name": "set_volume", "description": "Set the speaker's volume.",
               "inputSchema": {"type": "object", "properties": {"level": {"type": "integer", "minimum": 0, "maximum": 100}},
                               "required": ["level"]}}


async def box_link_side():
    print("without the speaker's socket")
    agent = boxlink.BoxAgent()
    check("the box takes no generic commands (it stays protocol 1)",
          agent.handlers == {} and agent.run_command("listen", {"opening": "x"}) == (False, "unknown_command"))

    print("the line to the speaker")
    port = SpeakerPort("tcp:127.0.0.1:0")
    await port.start()
    identity = new_identity()
    cloud = Cloud2(identity.registration_record()["public_key"])
    base = await cloud.start()
    folder = Path(os.environ.get("TEMP", "/tmp")) / f"p2-{os.getpid()}"
    folder.mkdir(exist_ok=True)
    agent = boxlink.BoxAgent()
    link, claim, agent, disk = boxlink.build(identity, base, disk=boxlink.Disk(folder), agent=agent,
                                             speaker=port, timings=FAST)
    link.start()
    await wait(lambda: cloud.hellos)
    h = cloud.hellos[0] if cloud.hellos else {}
    check("the hello says protocol 2 and lists the commands", h.get("protocol") == 2
          and h.get("capabilities") == ["button", "light", "listen", "play", "record", "stop"], str(h.get("capabilities")))
    r = await cloud.command("listen", {"opening": "t" * 32, "chime": "notice", "light": "notice"})
    check("with no speaker connected, a command answers busy", r and r.get("ok") is False and r.get("error") == "busy", str(r))

    got = []
    speaker = SpeakerLink(f"tcp:127.0.0.1:{port.port}", on_message=got.append, retry_s=0.1)
    speaker.send({"type": "tools", "tools": [VOLUME_TOOL], "answers": {"set_volume": "The volume is now {level}."}})
    speaker.send({"type": "report", "activity": "idle", "volume": 70, "muted": False})
    task = asyncio.create_task(speaker.run())
    await wait(lambda: port.connected and agent.doing.get("volume") == 70)
    check("the speaker connects, and what it said before is sent at once", port.connected and agent.doing == {"activity": "idle", "volume": 70, "muted": False})
    st = await wait(lambda: (m := last(cloud, "status")) and m.get("state", {}).get("volume") == 70 and m)
    check("the cloud hears what the speaker is doing, in `state`", bool(st), str(last(cloud, "status")))
    tools = await wait(lambda: last(cloud, "tools"))
    check("and gets the speaker's tools", tools and tools["tools"][0]["name"] == "set_volume", str(tools))

    r = await cloud.command("listen", {"opening": "t" * 32, "chime": "notice", "light": "notice"})
    cmd = await wait(lambda: next((m for m in got if m.get("type") == "command"), None))
    check("listen is accepted and passed on with its arguments", r.get("ok") is True and cmd
          and cmd["name"] == "listen" and cmd["args"]["opening"] == "t" * 32, f"{r} {cmd}")
    check("and the box link shows the light it asked for", agent.shown_pattern == "notice")
    r = await cloud.command("listen", {"chime": "notice"})
    check("listen without a token is refused (bad_args)", r.get("error") == "bad_args")
    r = await cloud.command("play", {"sound": "fanfare"})
    check("play of a sound the box hasn't got is refused", r.get("error") == "bad_args")
    r = await cloud.command("stop", {"what": "all"})
    check("stop is passed on", r.get("ok") is True and await wait(lambda: any(m.get("args") == {"what": "all"} for m in got)))

    speaker.send({"type": "report", "activity": "speaking"})
    await wait(lambda: agent.doing.get("activity") == "speaking")
    r = await cloud.command("listen", {"opening": "u" * 32})
    check("while the speaker is talking, listen answers busy", r.get("error") == "busy")
    speaker.send({"type": "report", "activity": "recording"})
    await wait(lambda: agent.doing.get("activity") == "recording")
    r = await cloud.command("record", {"action": "start", "meeting": "m" * 32})
    check("while it records, another record start answers busy", r.get("error") == "busy")
    r = await cloud.command("record", {"action": "stop"})
    check("record stop is passed on", r.get("ok") is True)
    speaker.send({"type": "report", "activity": "idle", "muted": True})
    await wait(lambda: agent.doing.get("muted") is True)
    r = await cloud.command("listen", {"opening": "v" * 32})
    check("with the microphone muted, listen answers busy", r.get("error") == "busy")
    speaker.send({"type": "report", "activity": "idle", "muted": False, "volume": 300})
    await wait(lambda: agent.doing.get("muted") is False)
    check("odd values are kept in range", agent.doing.get("volume") == 100)

    print("events")
    speaker.send({"type": "event", "name": "button.long"})
    ev = await wait(lambda: last(cloud, "event", "button.long"))
    check("a long press reaches the cloud, with what the box is doing", ev and ev.get("state", {}).get("activity") == "idle", str(ev))
    speaker.send({"type": "event", "name": "wake"})
    check("the wake word too", await wait(lambda: last(cloud, "event", "wake")))

    print("the speaker's own tools")
    r = await cloud.command("tools.call", {"name": "set_volume", "arguments": {"level": 40}})
    tool = await wait(lambda: next((m for m in got if m.get("type") == "tool"), None))
    check("a call is passed on, and the answer is what to tell the person", r.get("ok") is True
          and r.get("content") == "The volume is now 40." and tool == {"type": "tool", "name": "set_volume", "arguments": {"level": 40}}, f"{r} {tool}")
    r = await cloud.command("tools.call", {"name": "set_volume", "arguments": {"level": 400}})
    check("arguments outside the schema are refused", r.get("ok") is False and r.get("error") == "bad_args", str(r))
    r = await cloud.command("tools.call", {"name": "make_coffee", "arguments": {}})
    check("a tool the speaker hasn't got is refused", r.get("error") == "unknown_tool", str(r))

    print("when the speaker goes away")
    task.cancel()
    speaker.line.close()
    await wait(lambda: not port.connected)
    check("what it was doing and its tools are forgotten", agent.doing == {} and agent.tools == [])
    r = await cloud.command("record", {"action": "start", "meeting": "n" * 32})
    check("and commands answer busy again", r.get("error") == "busy")

    await link.stop()
    await port.stop()
    await cloud.stop()


class FakeTalk:
    """The team's VoiceTalk, scripted: `ready` says what the test sets in READY."""
    made = []
    READY = {"type": "ready", "limit": 600, "ends": "cloud"}

    def __init__(self, cloud, identity, *, on_sound=None, on_event=None, opening=None):
        self.on_sound, self.on_event, self.opening = on_sound, on_event, opening
        self.ready, self.ended, self.hung_up = asyncio.Event(), None, False
        self.finish = asyncio.Event()
        self.reason = "hung_up"
        FakeTalk.made.append(self)

    async def run(self):
        await asyncio.sleep(0.1)
        self.ready.set()
        await self.on_event(dict(FakeTalk.READY))
        await self.finish.wait()
        text = "Quiet for a while." if self.reason == "quiet" else ""
        self.ended = {"type": "ended", "reason": self.reason, "text": text, "seconds": 3}
        return self.ended

    async def send_sound(self, piece):
        return self.ready.is_set() and self.ended is None

    async def end(self):
        self.hung_up = True
        self.finish.set()


class OldTalk:
    """A copy of the team's code from before protocol 2: no `opening`."""

    def __init__(self, cloud, identity, *, on_sound=None, on_event=None):
        pass


async def speaker_voice():
    from anve_device.boxvoice import BoxVoice
    print("talks the cloud ends (part C)")
    got = []

    async def on_json(m):
        got.append(m)

    bv = BoxVoice("https://cloud.example", None, follow_up_s=0.3, silent_s=0.4, on_json=on_json, talk_factory=FakeTalk)
    await bv.send({"type": "wake"})
    talk = FakeTalk.made[-1]
    await asyncio.sleep(0.2)
    await talk.on_event({"type": "heard", "text": "make a deck"})
    await talk.on_event({"type": "turn"})
    await asyncio.sleep(1.2)
    check("with ends: cloud, the speaker doesn't hang up after its own pause", not talk.hung_up and bv.talk is talk)
    await talk.on_event({"type": "working"})
    check("`working` starts the thinking pulse", any(m == {"type": "tool.call", "name": "working"} for m in got))
    talk.reason = "quiet"
    talk.finish.set()
    await wait(lambda: bv.talk is None)
    check("the cloud's `quiet` ends it, with nothing said out loud",
          bv.talk is None and not any(m.get("type") == "notice" for m in got))

    FakeTalk.READY = {"type": "ready", "limit": 600}
    await bv.send({"type": "wake"})
    talk = FakeTalk.made[-1]
    await asyncio.sleep(0.9)
    check("a cloud that doesn't end talks: the speaker still hangs up a silent talk itself", talk.hung_up)
    await wait(lambda: bv.talk is None)
    FakeTalk.READY = {"type": "ready", "limit": 600, "ends": "cloud"}

    print("a talk ANVE opens (listen)")
    opened = await bv.send({"type": "listen", "opening": "o" * 32})
    talk = FakeTalk.made[-1]
    check("the cloud's token goes in the talk's hello", opened and talk.opening == "o" * 32)
    check("a second listen during that talk is refused", await bv.send({"type": "listen", "opening": "p" * 32}) is False)
    await talk.end()
    await wait(lambda: bv.talk is None)
    old = BoxVoice("https://cloud.example", None, on_json=on_json, talk_factory=OldTalk)
    check("with an older copy of the team's code, listen fails instead of breaking",
          await old.send({"type": "listen", "opening": "q" * 32}) is False)


class Playback:
    def __init__(self):
        self.played, self.flushed = [], 0

    def play(self, pcm):
        self.played.append(pcm)

    def flush(self):
        self.flushed += 1


class Client:
    def __init__(self):
        self.sent = []

    async def send(self, obj):
        self.sent.append(obj)
        return True


class FakeDevice:
    def __init__(self):
        self.cfg = {"audio": {"speaker_volume": 0.7}, "lumina": {"enabled": True}}
        self.muted, self.link, self._look, self.media_on = False, True, "idle", False
        self.meeting, self.reply_until, self.alarm_until = None, 0.0, 0.0
        self.playback, self.client = Playback(), Client()
        self.did = []

    def _media_on(self):
        return self.media_on

    def look(self):
        self.body.report()

    def _set_volume(self):
        self.did.append(("volume", self.cfg["audio"]["speaker_volume"]))

    def _remember_volume(self, vol):
        pass

    async def open_talk(self, opening):
        self.did.append(("talk", opening))

    async def meeting_start(self, meeting_id, how):
        self.did.append(("start", meeting_id))
        self.meeting = object()
        return ""

    async def meeting_stop(self, how):
        self.did.append(("stop", how))
        self.meeting = None
        return ""


async def speaker_body():
    from anve_device import boxbody
    print("the speaker's body, through the box link")
    port = SpeakerPort("tcp:127.0.0.1:0")
    heard = []
    port.on_message = heard.append
    await port.start()
    dev = FakeDevice()
    body = dev.body = boxbody.BoxBody(dev, f"tcp:127.0.0.1:{port.port}")
    body.link.retry_s = 0.1
    task = asyncio.create_task(body.run())
    await wait(lambda: port.connected and any(m["type"] == "tools" for m in heard))
    report = next((m for m in heard if m["type"] == "report"), None)
    check("on connecting it says what it's doing",
          report == {"type": "report", "activity": "idle", "volume": 70, "muted": False}, str(report))
    tools = next(m for m in heard if m["type"] == "tools")
    check("and offers volume and the light as tools, with the volume it has now",
          [t["name"] for t in tools["tools"]] == ["set_volume", "set_light_colour"]
          and "It is 70 now" in tools["tools"][0]["description"])
    check("D113: volume in a talk goes to ANVE's tool", body.offers("set_volume") and not body.offers("play_music"))

    heard.clear()
    dev._look = "speaking"
    dev.look()
    dev.look()
    await wait(lambda: heard)
    check("a change of look is reported once", [m.get("activity") for m in heard] == ["speaking"])
    dev._look, dev.media_on = "idle", True
    dev.look()
    await wait(lambda: len(heard) > 1)
    check("music on its own counts as playing", heard[-1].get("activity") == "playing")
    dev.media_on = False
    dev.look()

    port.send({"type": "command", "name": "listen", "args": {"opening": "t" * 32, "chime": "notice"}})
    await wait(lambda: ("talk", "t" * 32) in dev.did)
    check("listen chimes, then opens the talk with the token", dev.playback.played and ("talk", "t" * 32) in dev.did)
    dev.muted = True
    port.send({"type": "command", "name": "listen", "args": {"opening": "u" * 32}})
    await asyncio.sleep(0.2)
    check("but not while muted", ("talk", "u" * 32) not in dev.did)
    dev.muted = False
    port.send({"type": "command", "name": "record", "args": {"action": "start", "meeting": "m" * 32}})
    await wait(lambda: ("start", "m" * 32) in dev.did)
    check("record start records the cloud's meeting", ("start", "m" * 32) in dev.did)
    port.send({"type": "command", "name": "stop", "args": {"what": "all"}})
    await wait(lambda: any(d[0] == "stop" for d in dev.did))
    check("stop all hangs up, silences and stops the recording",
          {"type": "interrupt"} in dev.client.sent and dev.playback.flushed and dev.meeting is None)
    n = len(dev.playback.played)
    dev.reply_until = time.monotonic() + 5
    port.send({"type": "command", "name": "play", "args": {"sound": "chime", "channel": "alert"}})
    await asyncio.sleep(0.2)
    check("an alert waits while ANVE is talking", len(dev.playback.played) == n)
    dev.reply_until = 0.0
    port.send({"type": "command", "name": "play", "args": {"sound": "chime", "channel": "alert"}})
    await wait(lambda: len(dev.playback.played) > n)
    check("and plays when it isn't", len(dev.playback.played) == n + 1)

    heard.clear()
    port.send({"type": "tool", "name": "set_volume", "arguments": {"level": 40}})
    await wait(lambda: ("volume", 0.4) in dev.did)
    check("set_volume sets the speaker's volume", ("volume", 0.4) in dev.did)
    await wait(lambda: any(m["type"] == "tools" for m in heard))
    tools = next((m for m in heard if m["type"] == "tools"), {"tools": [{}]})
    check("and the tools then say the new volume", "It is 40 now" in tools["tools"][0].get("description", ""))

    heard.clear()
    boxbody.LONG_PRESS_WAIT_S = 0.3
    check("a long press is sent to the cloud", body.long_press())
    await wait(lambda: any(m.get("name") == "button.long" for m in heard))
    check("as the event button.long", any(m == {"type": "event", "name": "button.long"} for m in heard))
    n = len(dev.playback.played)
    await asyncio.sleep(0.5)
    check("no meeting after it: a short 'that went nowhere' sound", len(dev.playback.played) == n + 1)
    body.wake()
    check("the wake word is reported", await wait(lambda: any(m == {"type": "event", "name": "wake"} for m in heard)))

    await port.stop()
    await wait(lambda: not body.on)
    check("with the box link gone, the long press is left to the speaker", body.long_press() is False)
    task.cancel()


async def main():
    await box_link_side()
    await speaker_voice()
    await speaker_body()
    print(f"\n{ok}/{total} passed")
    return 0 if ok == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
