"""The speaker's side of protocol 2: the ANVE Box cloud runs the box's body (the team's P4
handover; part E of their box-cloud agreement; decisions D103 to D107, D112 to D114).

The box link (boxlink.py) gets the cloud's commands and passes them here over the local
socket (speakerlink.py). This class does them with what the speaker already has, and tells
the box link what the speaker is doing:

    listen   a chime, then a talk ANVE opens with the cloud's token ("your deck is ready")
    play     one of the speaker's own sounds: chime, notice, mark, error
    stop     the talk, the sound, the recording, or all of them
    record   start recording the meeting the cloud made, or stop
    tools    the speaker's own abilities, offered to ANVE in talks through this box:
             set_volume, set_light_colour (D113: music stays with the phrase list)

    report   idle / listening / speaking / recording / playing, the volume, mute
    events   a long press (the cloud starts a meeting, D114) and the wake word

When the box link isn't there, nothing here does anything and the speaker works as before.
"""
import asyncio
import json
import logging
import time
import urllib.request

from .playback import EARCONS, tone
from .speakerlink import SpeakerLink

log = logging.getLogger("anve.boxbody")

# what the speaker shows (Device.look) -> protocol 2's activity
ACTIVITY = {"listening": "listening", "thinking": "listening", "speaking": "speaking",
            "recording": "recording", "alarm": "playing"}
SOUNDS = {"chime": EARCONS["chime"], "notice": (784.0, 1046.5, 1318.5), "mark": (1318.5, 1568.0), "error": EARCONS["end"]}
# the resting colour of the LUMINA strip (its "ready" role); cyan is the strip's own
COLOURS = {"cyan": "#19d3ff", "blue": "#2b6cff", "green": "#22e07a", "red": "#ff2a2a", "purple": "#9b4dff",
           "pink": "#ff3db8", "orange": "#ff8a12", "white": "#ffffff"}
LONG_PRESS_WAIT_S = 6.0            # no meeting after a long press by then: the cloud said no


class BoxBody:
    def __init__(self, dev, address: str, lumina_dashboard: int = 8742):
        self.dev = dev
        self.link = SpeakerLink(address, on_message=self.on_message, on_change=self.on_change)
        self.lumina = lumina_dashboard
        self._said = None
        self._tools_for = None
        self._tasks: set = set()

    @property
    def on(self) -> bool:
        """Is protocol 2 on: is the box link there?"""
        return self.link.connected

    def offers(self, tool: str) -> bool:
        return self.on and any(t["name"] == tool for t in self.tools())

    async def run(self):
        self.report()
        await self.link.run()

    # ---------- what the speaker tells the box link ----------

    def volume(self) -> int:
        return round(float(self.dev.cfg["audio"]["speaker_volume"]) * 100)

    def report(self):
        """Called whenever the speaker's look changes (Device.look); sends only changes."""
        d = self.dev
        activity = ACTIVITY.get(getattr(d, "_look", "idle"), "idle")
        if activity == "idle" and d._media_on():
            activity = "playing"
        said = (activity, self.volume(), bool(d.muted))
        if said != self._said:
            self._said = said
            self.link.send({"type": "report", "activity": activity, "volume": said[1], "muted": said[2]})
        if self._tools_for != said[1]:
            self._tools_for = said[1]
            self.link.send({"type": "tools", "tools": self.tools(), "answers": {
                "set_volume": "The volume is now {level}.", "set_light_colour": "The light is now {colour}."}})

    def tools(self) -> list:
        tools = [{
            "name": "set_volume",
            "description": (f"Set this speaker's volume, from 0 (silent) to 100 (loudest). It is {self.volume()} now. "
                            "For 'a bit lower' or 'a bit louder', change it by about 10."),
            "inputSchema": {"type": "object", "properties": {"level": {"type": "integer", "minimum": 0, "maximum": 100}},
                            "required": ["level"]},
        }]
        if self.dev.cfg["lumina"]["enabled"]:
            tools.append({
                "name": "set_light_colour",
                "description": "Change the colour this speaker's light strip glows while it rests. Cyan is its usual colour.",
                "inputSchema": {"type": "object", "properties": {"colour": {"type": "string", "enum": sorted(COLOURS)}},
                                "required": ["colour"]},
            })
        return tools

    def wake(self):
        self.link.send({"type": "event", "name": "wake"})

    def long_press(self) -> bool:
        """A long press, for the cloud to decide (D114). False if the box link isn't there:
        then the speaker starts the meeting itself, as before."""
        if not self.link.send({"type": "event", "name": "button.long"}):
            return False
        self._spawn(self._no_meeting_after_press())
        return True

    async def _no_meeting_after_press(self):
        await asyncio.sleep(LONG_PRESS_WAIT_S)
        if not self.dev.meeting:
            log.info("no meeting came after the long press (not allowed for this account, or one is on elsewhere)")
            self.dev.playback.play(tone(SOUNDS["error"]))

    async def on_change(self, connected: bool):
        if connected:
            self._said = self._tools_for = None            # the box link starts afresh: say it all again
            self.report()

    # ---------- what the box link asks for ----------

    def on_message(self, m: dict):
        """Each one in its own task: a long start must not hold up the next message."""
        self._spawn(self._handle(m))

    def _spawn(self, coro):
        t = asyncio.create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    async def _handle(self, m: dict):
        kind = m.get("type")
        args = m.get("args") if isinstance(m.get("args"), dict) else {}
        if kind == "command":
            name = m.get("name")
            log.info("the cloud asks for %s", name)
            if name == "listen":
                await self._listen(args)
            elif name == "play":
                self._play(args.get("sound"), args.get("channel", "alert"))
            elif name == "stop":
                await self._stop(args.get("what"))
            elif name == "record" and args.get("action") == "start":
                why = await self.dev.meeting_start(str(args.get("meeting") or ""), "for the cloud")
                if why:
                    log.info("the meeting could not start: %s", why)
            elif name == "record" and args.get("action") == "stop":
                await self.dev.meeting_stop("for the cloud")
        elif kind == "tool":
            arguments = m.get("arguments") if isinstance(m.get("arguments"), dict) else {}
            if m.get("name") == "set_volume" and isinstance(arguments.get("level"), int):
                await self.set_volume(arguments["level"])
            elif m.get("name") == "set_light_colour" and arguments.get("colour") in COLOURS:
                await asyncio.to_thread(self._set_light, arguments["colour"])

    async def _listen(self, args: dict):
        d = self.dev
        if d.meeting or d.muted or not d.link:
            log.info("listen: not now (%s)", "a meeting" if d.meeting else "muted" if d.muted else "no link")
            return
        if args.get("chime") in SOUNDS:
            d.playback.play(tone(SOUNDS[args["chime"]]))
        await d.open_talk(str(args.get("opening") or ""))

    def _play(self, sound, channel: str):
        d = self.dev
        if sound not in SOUNDS:
            return
        if channel != "dialog" and time.monotonic() < d.reply_until:
            return                                       # ANVE's talk beats alerts and music
        d.playback.play(tone(SOUNDS[sound]))

    async def _stop(self, what):
        d = self.dev
        if what in ("listen", "all"):
            await d.client.send({"type": "interrupt"})
        if what in ("play", "all"):
            d.alarm_until = d.reply_until = 0.0
            d.playback.flush()
        if what in ("record", "all") and d.meeting:
            await d.meeting_stop("for the cloud")
        d.look()

    async def set_volume(self, level: int):
        d = self.dev
        level = max(0, min(100, int(level)))
        d.cfg["audio"]["speaker_volume"] = level / 100
        await asyncio.to_thread(d._set_volume)
        await asyncio.to_thread(d._remember_volume, level / 100)
        log.info("volume %d, asked for by ANVE", level)
        d.look()

    def _set_light(self, colour: str):
        """LUMINA's own settings API, which programs on this machine may use without its key."""
        body = json.dumps({"colours": {"ready": COLOURS[colour]}}).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{self.lumina}/api/settings", data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=3) as r:
                log.info("light colour %s (LUMINA answered %s)", colour, r.status)
        except OSError as e:
            log.warning("LUMINA did not take the colour (%s)", type(e).__name__)
