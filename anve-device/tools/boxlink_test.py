#!/usr/bin/env python3
"""The box link, tested against a stand-in for the ANVE Box cloud:   python tools/boxlink_test.py

The stand-in speaks part A of the team's docs/box-cloud-agreement.md as written there
(challenge, signed hello, welcome, ping, command, owner, claim), and checks the box's
signature with the public key from its registration record, the way the real cloud does.
It is NOT the team's cloud: a pass here means the box keeps to the written agreement.
"""
import asyncio
import json
import os
import stat
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import websockets  # noqa: E402
from cryptography.exceptions import InvalidSignature  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey  # noqa: E402

from anve_device import boxlink  # noqa: E402
from anvebox_agent.cloud.link import LinkTimings  # noqa: E402
from anvebox_agent.identity import b64u_decode, claim_hash, link_message, new_identity  # noqa: E402
from anvebox_agent.model import State  # noqa: E402

ok = total = 0
FAST = LinkTimings(first_retry=0.1, max_retry=0.4, dead_after=3.0, open_timeout=2.0, poll=0.05, nudge_spacing=0.1)


def check(name, cond, detail=""):
    global ok, total
    total += 1
    ok += bool(cond)
    print(("  ok   " if cond else "  FAIL ") + name + (f"   [{detail}]" if detail and not cond else ""))


class Cloud:
    """One box's worth of the cloud's side of protocol 1."""

    def __init__(self):
        self.boxes = {}                 # device_id -> {"public_key", "claim_hash", "claimed", "used": set()}
        self.hellos, self.got = [], []
        self.ws = None
        self.server = None
        self.port = 0
        self.rejections = 0

    def register(self, record: dict):
        self.boxes[record["device_id"]] = {"public_key": record["public_key"], "claim_hash": record["claim_hash"],
                                           "claimed": False, "used": set()}

    async def start(self):
        self.server = await websockets.serve(self._conn, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{self.port}"

    async def stop(self):
        self.server.close()
        await self.server.wait_closed()

    async def _conn(self, ws):
        nonce = os.urandom(12).hex()
        await ws.send(json.dumps({"type": "challenge", "nonce": nonce}))
        hello = json.loads(await ws.recv())
        box = self.boxes.get(hello.get("device_id"))
        try:
            if box is None or hello.get("protocol") != 1:
                raise InvalidSignature
            Ed25519PublicKey.from_public_bytes(b64u_decode(box["public_key"])).verify(
                b64u_decode(hello["signature"]), link_message(nonce, hello["device_id"]))
        except (InvalidSignature, KeyError, ValueError):
            self.rejections += 1
            await ws.close(4401, "unknown box")
            return
        self.hellos.append(hello)
        if hello.get("was_reset") and hello.get("claim_hash") not in box["used"]:
            box["claimed"] = False
        rotate = hello.get("claim_hash") in box["used"]
        if not rotate:
            box["claim_hash"] = hello.get("claim_hash")
        self.ws = ws
        await ws.send(json.dumps({"type": "welcome", "owner": {"claimed": box["claimed"]},
                                  "server_time": "2026-11-19T14:00:00+00:00", "rotate_claim": rotate}))
        try:
            async for raw in ws:
                m = json.loads(raw)
                self.got.append(m)
                if m.get("type") == "claim":
                    box["claim_hash"] = m["claim_hash"]
        except websockets.ConnectionClosed:
            pass
        finally:
            if self.ws is ws:
                self.ws = None

    async def send(self, message: dict):
        await self.ws.send(json.dumps(message))

    async def command(self, name: str, args: dict | None = None, cid: str = "") -> dict:
        cid = cid or os.urandom(4).hex()
        await self.send({"type": "command", "id": cid, "name": name, "args": args or {}})
        return await wait(lambda: next((m for m in self.got if m.get("type") == "result" and m.get("id") == cid), None))

    def claim(self, device_id: str, secret: str) -> bool:
        """A person opens the claim link: the cloud checks the hash, as the real one does."""
        box = self.boxes[device_id]
        h = claim_hash(secret)
        if h != box["claim_hash"] or h in box["used"]:
            return False
        box["claimed"] = True
        box["used"].add(h)
        return True


async def wait(what, seconds: float = 3.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        v = what()
        if v:
            return v
        await asyncio.sleep(0.02)
    return None


class Led:
    def __init__(self):
        self.log = []

    def take(self): self.log.append("take")
    def set(self, level): self.log.append(1 if level >= 0.5 else 0)
    def release(self): self.log.append("release")


async def main():
    folder = Path(tempfile.mkdtemp(prefix="boxlink-test-"))
    identity = new_identity()
    cloud = Cloud()
    base = await cloud.start()

    print("addresses")
    check("https becomes wss, with the link's path", boxlink.link_url("https://box-cloud.example/") == "wss://box-cloud.example/box/v1/link")
    check("http (a development cloud) becomes ws", boxlink.link_url("http://127.0.0.1:8200") == "ws://127.0.0.1:8200/box/v1/link")

    print("a box the cloud does not know")
    led, sent = Led(), []
    mirror = type("M", (), {"send": lambda self, m: sent.append(m)})()
    agent = boxlink.BoxAgent(led=led, mirror=mirror)
    disk = boxlink.Disk(folder)
    link, claim, agent, disk = boxlink.build(identity, base, disk=disk, agent=agent, timings=FAST)
    link.start()
    await wait(lambda: cloud.rejections >= 2)
    check("is turned away (4401) and keeps trying, politely", cloud.rejections >= 2 and "doesn't know this box" in (link.last_problem or ""))
    check("meanwhile its mode is CONNECTING, with the fast blink", agent.status()["mode"] == "CONNECTING" and agent.status()["light"] == "fast_blink")
    check("the first claim secret is on disk already, readable by its owner only",
          json.loads(disk.path.read_text())["claim_secret"] == identity.claim_secret.reveal()
          and (os.name == "nt" or stat.S_IMODE(os.stat(disk.path).st_mode) == 0o600))

    print("once the record is registered")
    record = identity.registration_record()
    check("the record holds nothing secret", set(record) == {"device_id", "public_key", "claim_hash"}
          and identity.claim_secret.reveal() not in json.dumps(record) and identity.device_key.reveal() not in json.dumps(record))
    cloud.register(record)
    await wait(lambda: cloud.hellos)
    h = cloud.hellos[0]
    check("the hello is signed and accepted", bool(cloud.hellos))
    check("it carries protocol 1, the version, the mode, the light and what the box can do",
          h["protocol"] == 1 and "anve-device" in h["software"] and h["mode"] == "CONNECTING" and h["light"] == "fast_blink"
          and h["capabilities"] == ["light", "button"])
    check("and the claim secret's hash, never the secret", h["claim_hash"] == claim_hash(identity.claim_secret.reveal())
          and identity.claim_secret.reveal() not in json.dumps(h) and h["was_reset"] is False)
    st = await wait(lambda: next((m for m in cloud.got if m.get("type") == "status"), None))
    check("status follows: online, no owner yet, two short blinks", st and st["mode"] == "ONLINE_UNCLAIMED" and st["light"] == "double_blink"
          and st["network"].get("via") in ("wifi", "cable"), str(st))
    check("a mode change is reported as an event with a time", any(m.get("type") == "event" and m.get("name") == "mode.changed"
          and m.get("to") == "ONLINE_UNCLAIMED" and "at" in m for m in cloud.got))

    print("commands")
    await cloud.send({"type": "ping"})
    check("ping is answered with pong", await wait(lambda: any(m.get("type") == "pong" for m in cloud.got)))
    t0 = time.monotonic()
    r = await cloud.command("light.identify")
    check("light.identify is accepted at once", r and r["ok"] is True and time.monotonic() - t0 < 0.5, str(r))
    await wait(lambda: led.log.count(1) >= 4)
    check("the green LED is taken over and flickers", led.log[:1] == ["take"] and led.log.count(1) >= 4 and 0 in led.log, str(led.log[:12]))
    check("the strip is told the same pattern", sent and sent[-1]["type"] == "pattern" and sent[-1]["name"] == "identify" and sent[-1]["seconds"] == 10.0)
    check("the status says which light is showing", await wait(lambda: any(m.get("type") == "status" and m.get("light") == "identify" for m in cloud.got)))
    r = await cloud.command("light.show", {"pattern": "slow_blink", "seconds": 1})
    check("light.show replaces it and ends by itself", r["ok"] is True)
    await wait(lambda: led.log and led.log[-1] == "release" and agent.shown_pattern == "double_blink", 4.0)
    check("afterwards the LED is handed back and the usual light is reported", led.log[-1] == "release" and agent.status()["light"] == "double_blink")
    for args, why in (({"pattern": "disco", "seconds": 5}, "an unknown pattern"), ({"pattern": "solid", "seconds": 0}, "0 seconds"),
                      ({"pattern": "solid", "seconds": 601}, "601 seconds"), ({"pattern": "solid", "seconds": True}, "seconds = true"),
                      ({"seconds": 5}, "no pattern")):
        r = await cloud.command("light.show", args)
        check(f"light.show with {why} is refused as bad_args", r["ok"] is False and r["error"] == "bad_args", str(r))
    r = await cloud.command("wifi.change", {"ssid": "x"})
    check("a command that is not in protocol 1 is refused as unknown_command", r["ok"] is False and r["error"] == "unknown_command")
    n = sum(1 for m in cloud.got if m.get("type") == "status")
    r = await cloud.command("status.get")
    check("status.get sends a status, then the result", r["ok"] is True and sum(1 for m in cloud.got if m.get("type") == "status") == n + 1)

    print("claiming")
    link_text = boxlink.claim_link("https://cloud.example/", identity, disk)
    check("the claim link has the box's ID and secret after the #", link_text == f"https://cloud.example/claim#{identity.device_id}.{identity.claim_secret.reveal()}")
    first = disk.claim_secret
    check("opening the link makes the person the owner", cloud.claim(identity.device_id, first))
    await cloud.send({"type": "owner", "claimed": True, "rotate_claim": True})
    new = await wait(lambda: next((m for m in cloud.got if m.get("type") == "claim"), None))
    check("the box makes a new secret and sends only its hash", new and disk.claim_secret != first and new["claim_hash"] == claim_hash(disk.claim_secret))
    check("it now knows it has an owner: READY, solid light", agent.status()["mode"] == "READY" and agent.status()["light"] == "solid")
    again = boxlink.Disk(folder)
    check("and that survives a restart (it is on disk)", again.claimed is True and again.claim_secret == disk.claim_secret)
    check("the same link does not work twice", cloud.claim(identity.device_id, first) is False)
    check("no temporary file is left behind", not [p for p in folder.iterdir() if p.name.startswith(".state-")])

    print("losing the cloud")
    hellos = len(cloud.hellos)
    await cloud.ws.close()
    await wait(lambda: len(cloud.hellos) > hellos)
    check("a dropped connection is made again", len(cloud.hellos) > hellos and cloud.hellos[-1]["mode"] == "READY")
    await cloud.stop()
    if cloud.ws is not None:
        await cloud.ws.close()
    await wait(lambda: not agent.reachable)
    agent.tick()
    check("for the first minute it stays as it was (the grace period)", agent.status()["mode"] == "READY")
    agent.clock = lambda: time.monotonic() + 3600                  # an hour later, as far as the box can tell
    agent.tick()
    check("out of reach for more than a minute: OFFLINE, three short blinks (the network is there)",
          agent.status()["mode"] == "OFFLINE" and agent.status()["light"] == "triple_blink", str(agent.status()))
    check("while offline the cloud's light commands are refused as busy", agent.run_command("light.identify", {}) == (False, "busy"))
    agent.setup_open = lambda: True
    agent.tick()
    check("when the box is in setup, the mode says SETUP and the light is the slow pulse",
          agent.status()["mode"] == "SETUP" and agent.status()["light"] == "setup_pulse")
    await link.stop()

    print("a factory reset")
    cloud2 = Cloud()
    base2 = await cloud2.start()
    cloud2.register(record)
    cloud2.boxes[identity.device_id].update(claimed=True, claim_hash=claim_hash(disk.claim_secret))
    link2, claim2, agent2, disk2 = boxlink.build(identity, base2, disk=boxlink.Disk(folder), agent=boxlink.BoxAgent(led=Led()), timings=FAST)
    before = disk2.claim_secret
    claim2.forget()
    check("the box drops its owner, makes a new secret and notes the reset", disk2.claimed is False and disk2.claim_secret != before and disk2.was_reset is True)
    link2.start()
    await wait(lambda: cloud2.hellos)
    check("its next hello tells the cloud", cloud2.hellos and cloud2.hellos[0]["was_reset"] is True)
    await wait(lambda: disk2.was_reset is False)
    check("the cloud releases the old owner, and the note is dropped once it has been heard",
          cloud2.boxes[identity.device_id]["claimed"] is False and disk2.was_reset is False and agent2.status()["mode"] == "ONLINE_UNCLAIMED")
    await link2.stop()
    await cloud2.stop()

    print("a damaged state file")
    (folder / "state.json").write_text("{ not json", encoding="utf-8")
    d = boxlink.Disk(folder)
    check("is read as 'no owner yet' instead of stopping the box", d.claimed is False and d.claim_secret is None and d.was_reset is False)
    link3, claim3, agent3, d3 = boxlink.build(identity, "http://127.0.0.1:9", disk=d, timings=FAST)
    check("and the card's first secret is used again", d3.claim_secret == identity.claim_secret.reveal())

    print("the identity file")
    p = folder / "identity.json"
    from anvebox_agent.identity import save
    save(identity, p)
    loaded = boxlink.load_identity(p)
    check("is read back whole", loaded.device_id == identity.device_id and loaded.public_key == identity.public_key)
    check("and never shows its secrets when printed", "***" in repr(loaded) and identity.device_key.reveal() not in repr(loaded))

    print(f"\n{ok}/{total} passed")
    return 0 if ok == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
