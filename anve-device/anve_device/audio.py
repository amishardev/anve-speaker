"""Audio in and out through PipeWire.

Devices are found by NAME (a substring of the PipeWire node name or description), never
by ALSA card number: card numbers change between boots and when a device is replugged.
Capture and playback each run a `pw-record` / `pw-play` child with raw PCM on a pipe.
PipeWire does the resampling and, through its echo-cancel module, the clock-drift
compensation between the USB microphone and the USB sound card. If a device is
unplugged the child exits; it is restarted when the device is back.
"""
import asyncio
import json
import logging
import subprocess

log = logging.getLogger("anve.audio")


def nodes(media_class: str) -> list[dict]:
    """PipeWire nodes of one class ('Audio/Source', 'Audio/Sink'): name, description."""
    try:
        raw = subprocess.run(["pw-dump"], capture_output=True, text=True, timeout=5).stdout
        objs = json.loads(raw)
    except Exception:                                        # noqa: BLE001
        return []
    out = []
    for o in objs:
        p = (o.get("info") or {}).get("props") or {}
        if o.get("type") == "PipeWire:Interface:Node" and p.get("media.class") == media_class:
            out.append({"id": o.get("id"), "name": p.get("node.name", ""),
                        "description": p.get("node.description", "")})
    return out


def find_node(media_class: str, match: str) -> str | None:
    """The node name whose name or description contains `match` (case-insensitive)."""
    m = match.lower()
    for n in nodes(media_class):
        if m in n["name"].lower() or m in n["description"].lower():
            return n["name"]
    return None


class Capture:
    """16 kHz mono PCM16 from a PipeWire source, as fixed-size frames (40 ms = 1280 bytes).

        async for frame in Capture("UACDemo").frames(): ...

    `on_state(ok: bool)` is called when the source appears or disappears."""

    def __init__(self, match: str, *, rate: int = 16000, frame_bytes: int = 1280,
                 latency_ms: int = 20, on_state=None):
        self.match, self.rate, self.frame_bytes = match, rate, frame_bytes
        self.latency_ms, self.on_state = latency_ms, on_state
        self.node = None
        self._proc = None
        self._closed = False

    async def frames(self):
        was_ok = None
        while not self._closed:
            self.node = await asyncio.to_thread(find_node, "Audio/Source", self.match)
            if not self.node:
                if was_ok is not False:
                    log.warning("microphone '%s' not found; waiting for it", self.match)
                    was_ok = False
                    if self.on_state:
                        await self.on_state(False)
                await asyncio.sleep(2)
                continue
            self._proc = await asyncio.create_subprocess_exec(
                "pw-record", "--raw", "--target", self.node, "--rate", str(self.rate),
                "--channels", "1", "--format", "s16", "--latency", f"{self.latency_ms}ms",
                # node.dont-reconnect: if the source goes away, this stream must END, not be
                # moved to whatever else can record. Found live on 16 Nov 2026: after the
                # echo canceller reloaded, PipeWire had re-linked this stream to the
                # Bluetooth speaker's silent hands-free input and the device heard nothing.
                "-P", '{"node.name":"anve-capture","media.role":"Communication",'
                      '"node.dont-reconnect":true}', "-",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            got = False
            try:
                while not self._closed:
                    try:
                        frame = await asyncio.wait_for(
                            self._proc.stdout.readexactly(self.frame_bytes), 3.0)
                    except (asyncio.IncompleteReadError, asyncio.TimeoutError):
                        break                  # pw-record died or the source went silent
                    if not got:
                        got = True
                        log.info("capturing from %s", self.node)
                        if was_ok is not True:
                            was_ok = True
                            if self.on_state:
                                await self.on_state(True)
                    yield frame
            finally:
                await self._kill()
            if not self._closed:
                log.warning("capture from %s stopped; retrying", self.node)
                if was_ok is not False:
                    was_ok = False
                    if self.on_state:
                        await self.on_state(False)
                await asyncio.sleep(1)

    async def _kill(self):
        p, self._proc = self._proc, None
        if p and p.returncode is None:
            p.kill()
            try:
                await asyncio.wait_for(p.wait(), 2)
            except asyncio.TimeoutError:
                pass

    async def kick(self):
        """Drop the current recorder so the loop starts a fresh one on the right node
        (called when the echo canceller has been reloaded)."""
        p = self._proc
        if p and p.returncode is None:
            p.kill()

    def linked_to(self) -> str | None:
        """The node this capture is really recording from, as PipeWire has it linked."""
        try:
            objs = json.loads(subprocess.run(["pw-dump"], capture_output=True, text=True,
                                             timeout=5).stdout)
        except Exception:                                    # noqa: BLE001
            return None
        names = {o["id"]: ((o.get("info") or {}).get("props") or {}).get("node.name")
                 for o in objs if o.get("type") == "PipeWire:Interface:Node"}
        for o in objs:
            if o.get("type") == "PipeWire:Interface:Link":
                i = o["info"]
                if names.get(i["input-node-id"]) == "anve-capture":
                    return names.get(i["output-node-id"])
        return None

    async def close(self):
        self._closed = True
        await self._kill()
