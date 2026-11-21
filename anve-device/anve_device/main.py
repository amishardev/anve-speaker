"""The ANVE speaker: one asyncio program.

    microphone -> PipeWire echo canceller (+ high-pass, noise suppression) -> AGC
               -> wake word on this device      (idle: nothing is uploaded)
               -> ANVE over the session socket  (after a wake, in push-to-talk, while a
                                                 meeting is being recorded)
    ANVE reply audio -> jitter buffer -> echo-cancel sink -> speaker
    ANVE `state`     -> LED strip          buttons -> mute / push-to-talk / interrupt

Run:  .venv/bin/python -m anve_device.main [--config config.toml] [--url ws://...]
"""
import argparse
import asyncio
import logging
import os
import re
import signal
import subprocess
import threading
import time
import wave
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import config as C
from . import skills
from .appapi import AppApi, pair_file
from .audio import Capture, nodes
from .client import Client
from .dsp import Agc
from .playback import EARCONS, EchoCancel, Playback, tone
from .turn import Turn
from .wake import RATE, Segmenter, SileroVAD, Spotter

log = logging.getLogger("anve.device")
FRAME = 640
SILENCE = bytes(FRAME * 2)          # one 40 ms frame of digital silence


#: a sentence that begins like an order to the speaker itself (music, its browser)
COMMAND_LIKE = re.compile(r"\W*(hey anve\W+)?(please |can you |could you |will you )?"
                          r"(play|pause|stop|resume|skip|next|open|volume|put on|turn (it|the)|"
                          r"(set|change|make) (the )?(speaker )?volume)\b", re.I)


def _be_nice():
    try:
        os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 10)
    except OSError:
        pass


class Device:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        s, a, w = cfg["server"], cfg["audio"], cfg["wake"]
        self.client = Client(s["url"], s["key_file"], engine=s["engine"], mode=s["mode"],
                             speak=s["speak"], codec=s["codec"], on_json=self.on_json,
                             on_audio=self.on_audio, on_link=self.on_link)
        self.ec = EchoCancel(a["mic"], a["speaker"], noise_suppression=a["noise_suppression"],
                             on_state=self.on_devices, on_loaded=self.on_canceller)
        self.capture = Capture(EchoCancel.SOURCE)
        self.playback = Playback(EchoCancel.SINK, jitter_ms=a["jitter_ms"],
                                 tail_ms=a["output_tail_ms"])
        self.agc = Agc(a["agc_target_db"], a["agc_max_gain_db"])
        self.vad = SileroVAD()
        self.segmenter = Segmenter(early_s=w["early_s"], own_vad=False)
        self.spotter = Spotter(w["model"], w["threads"], tokens=w["tokens"])
        # The wake-word decodes run at low priority (nice 10): sound in and out must never
        # wait for them. Heard as stutter in music before this. The model's own worker
        # threads are created from this thread and inherit its priority.
        self.pool = ThreadPoolExecutor(1, thread_name_prefix="anve-wake", initializer=_be_nice)
        self.ring = None
        # 15 s of processed 40 ms frames: (sample index, frame, speech?, silent?)
        self.buf: deque = deque(maxlen=375)
        self.turn = Turn(cfg["listen"]["start_s"], cfg["listen"]["pause_s"])
        self._flags: deque = deque()                  # the two flags, delayed like the uplink frame
        self.index = 0
        # what the server last said, and what this device is doing
        self.link = False
        self.srv = {"s": "idle", "cloud": 0, "rec": 0}
        self.mic_ok = self.speaker_ok = False
        self.muted = False
        self.streaming = False
        self.send_from = None                         # sample index to resume sending from
        self.ptt = False
        self.recording = False
        self.action_down = False
        self.action_t = self.last_tap = 0.0
        self.reply_until = 0.0                        # when the reply's sound has left the room
        self.alarm_until = 0.0
        self.last_wake_start = -1
        self.wake_t = 0.0
        # skills the speaker runs itself (its own browser, music), and thinking sounds
        self.media = skills.Media(cfg["skills"].get("duck", 0.3), cfg["skills"].get("muffle", True)) \
            if cfg["skills"]["enabled"] else None
        self.fillers = skills.Fillers() if cfg["thinking"]["mode"] == "voice" else None
        self.heard = ""                               # this request, as ANVE reports it
        self.skill_done_for = ""
        self.drop_reply_until = 0.0                   # ANVE's own answer to a local skill
        self.thinking = self.tool_pending = False
        self.last_filler = 0.0
        self._think_task = None
        self.chimed_at = 0.0                          # when the listening chime last played
        self.started_sound = False                    # the once-per-start "ready" chord
        self.holding, self.hold_t = False, 0.0        # ANVE's answer held back (see on_audio)
        self.held: list = []
        # the thinking pulse: two very soft notes, a fourth apart, that alternate
        self._ticks = (tone((587.3,), ring=0.20, gain=0.055), tone((784.0,), ring=0.20, gain=0.055))
        self.win: deque = deque(maxlen=75)            # last 3 s, for the wake check over music
        self.win_n = 0
        self.spot_busy = False
        self.spot_next = None                         # the newest segment waiting for the recogniser
        self._diag_checked, self._diag_until = 0.0, 0.0
        self.stats = {"wakes": 0, "barges": 0, "waited": 0, "replaced": 0, "spot_ms": deque(maxlen=200)}
        self._look = "idle"
        # the phone app's API on the local network (appapi.py)
        self.app = AppApi(self, cfg) if cfg["app"]["enabled"] else None

    # ---------- LEDs ----------

    def look(self):
        """What the speaker is doing, as one word: shown on the LEDs and in the app."""
        now = time.monotonic()
        s = self.srv["s"]
        if self.client.halted:
            look = "halted"
        elif not self.link or not (self.mic_ok and self.speaker_ok) or s == "error":
            look = "error"
        elif self.muted or s == "muted":
            look = "muted"
        elif now < self.alarm_until:
            look = "alarm"
        elif self.ptt:
            look = "listening"
        elif s == "speaking" or (s != "thinking" and now < self.reply_until):
            look = "speaking"
        elif self.recording and s in ("idle", "listening"):
            look = "recording"
        else:
            look = s
        self._look = look
        if self.ring:
            self.ring.set(look, cloud=bool(self.srv.get("cloud")) or self.ptt)

    # ---------- server -> device ----------

    async def on_link(self, up: bool, why: str):
        self.link = up
        if up:
            log.info("connected")
            if self.muted:
                await self.client.send({"type": "mute", "on": True, "hw": True})
            asyncio.create_task(self._startup_sound())
        else:
            self.streaming = self.ptt = False
            self.recording = False                    # the server keeps what it recorded
            self.srv = {"s": "idle", "cloud": 0, "rec": 0}
            self.thinking = self.tool_pending = False
            if self.media and self.media.ducked:
                await self.media.unduck()
        self.look()

    async def on_audio(self, pcm: bytes):
        self.thinking = self.tool_pending = False
        now = time.monotonic()
        if now < self.drop_reply_until:
            return                       # a local skill answered this request instead
        if self.holding:
            # The request began like a command to the speaker ("play ..."). ANVE cannot
            # play music and starts "Sorry, I can't ..."; that was audible before the
            # speaker took over. So its answer waits here until the speaker has decided:
            # thrown away if the speaker handles the request, played if it does not.
            if now - self.hold_t < 5.0:
                self.held.append(pcm)
                return
            self._release_held()
        self.playback.play(pcm)
        self.reply_until = time.monotonic() + self.playback.remaining()

    def _release_held(self):
        """Not a request for the speaker after all: let ANVE's answer be heard."""
        held, self.held, self.holding = self.held, [], False
        for pcm in held:
            self.playback.play(pcm)
        if held:
            self.reply_until = time.monotonic() + self.playback.remaining()

    # ---------- thinking sounds and local skills ----------

    def _media_on(self) -> bool:
        return bool(self.media and self.media.playing)

    def _media_loud(self) -> bool:
        """Music at full volume: nobody is talking to the assistant right now."""
        return bool(self.media and self.media.playing and not self.media.ducked)

    def _filler(self, kind: str):
        now = time.monotonic()
        if not self.fillers or self.recording or now < self.drop_reply_until or \
                now < self.reply_until or now - self.last_filler < 2.5:
            return
        clip = self.fillers.pick(kind)
        if clip:
            self.last_filler = now
            self.playback.play(clip)

    async def _think_aloud(self):
        """ANVE is working: let the person hear that, until the answer starts.

        "tone" (the default): two very soft notes that alternate, about one a second,
        a quiet "still here" pulse. "voice": spoken fillers from a speech synthesizer
        (the owner found that voice robotic, so it is no longer the default)."""
        t = self.cfg["thinking"]
        if t["mode"] == "off":
            return
        await asyncio.sleep(t["after_s"])
        for n in range(45):
            if not (self.thinking or self.tool_pending):
                return
            now = time.monotonic()
            quiet = now >= self.reply_until and now >= self.drop_reply_until and not self.recording
            if t["mode"] == "voice":
                self._filler("FIRST" if n == 0 else "LONG")
                await asyncio.sleep(t["again_s"])
            else:
                if quiet:
                    self.playback.play(self._ticks[n % 2])
                await asyncio.sleep(0.85)

    async def _maybe_skill(self):
        """Is this request one the speaker handles itself (music, its browser)?"""
        # The transcript arrives in pieces. Acting on the first piece once turned "Play
        # How You Feel by Amishar" into a bare "Play" (and the radio came on, and the
        # interrupt we send cut the rest of the sentence off). So wait until no new piece
        # has arrived for 1.2 s; a later piece starts its own wait.
        before = self.heard
        await asyncio.sleep(1.2)
        if self.heard != before:
            return
        text = self.heard.strip()
        intent = skills.parse(text) if self.media else None
        if not intent and COMMAND_LIKE.match(text):
            # a command to the device itself that was not understood: worth seeing
            log.info("device-style request left to ANVE: %r", text[:90])
        if not intent or text == self.skill_done_for:
            self._release_held()                      # ANVE's own answer stands
            return
        self.skill_done_for = text
        self.held, self.holding = [], False           # the speaker handles it: drop ANVE's answer
        log.info("local skill: %s %r", intent["do"],
                 intent.get("query") or intent.get("station") or intent.get("name") or "")
        # ANVE will answer too ("I can't play music"): stop that answer and drop it
        self.drop_reply_until = time.monotonic() + 12
        self.thinking = self.tool_pending = False
        self.playback.flush()
        self.reply_until = 0.0
        await self.client.send({"type": "interrupt"})
        # "Playing <song> by <artist>", in ANVE's own voice, while the search runs. The
        # speaker has no voice of its own worth hearing, so it asks ANVE to say the line:
        # a typed turn through the same socket (it is recorded in the session as typed).
        line = ""
        if intent["do"] == "youtube":
            line = f"Playing {intent['query']}."
        elif intent["do"] == "radio":
            line = f"Playing {skills.RADIO.get(intent.get('station'), skills.RADIO['default'])[0]}."
        if line and self.cfg["skills"].get("announce", True):
            await asyncio.sleep(0.3)                  # let the interrupted answer stop
            # reply audio stays discarded until the server echoes this typed turn back
            # (see transcript.in): nothing of the interrupted answer can slip through
            await self.client.send({"type": "text", "text":
                                    "[From the speaker device] The speaker is doing this itself right now, "
                                    "so call no tool. Do not apologise and do not say you cannot play "
                                    f"music. Say exactly these words and nothing before or after them: {line}"})
        try:
            if intent["do"] == "volume":
                a = self.cfg["audio"]
                if "level" in intent:                 # "set volume to 30 percent": exactly that
                    a["speaker_volume"] = round(intent["level"], 2)
                else:                                 # "volume up" / "volume down": 10 % steps
                    a["speaker_volume"] = round(max(0.1, min(1.0, a["speaker_volume"] + intent["step"])), 2)
                await asyncio.to_thread(self._set_volume)
                await asyncio.to_thread(self._remember_volume, a["speaker_volume"])
                say = ""
                self.playback.play(tone(EARCONS["chime"]))     # heard at the new volume
            else:
                say = await self.media.run(intent)
        except Exception:                                    # noqa: BLE001
            log.exception("local skill failed")
            say = "Sorry, that did not work."
        if say:
            log.info("local skill said: %s", say)
            # a falling chime, not a synthesized sentence, unless spoken fillers are on
            clip = await asyncio.to_thread(skills.render, say) if self.fillers else b""
            self.playback.play(clip or tone(EARCONS["end"]))

    async def media_watch(self):
        """Keep `media.playing` true to what the browser is really doing."""
        while self.media:
            await asyncio.sleep(5)
            if not self.media.ducked:
                await self.media.refresh()

    async def _unduck_when_quiet(self):
        """The conversation is over: bring the music back if we paused it for it."""
        for _ in range(120):
            await asyncio.sleep(0.5)
            if self.streaming or self.ptt or self.srv["s"] != "idle":
                return                                # still talking: a later idle resumes it
            if time.monotonic() >= self.reply_until + 0.5:
                break
        if self.media and self.media.ducked:
            await self.media.unduck()

    async def on_json(self, m: dict):
        t = m.get("type")
        if self.app:
            self.app.on_server(m)
        if t == "state":
            self.srv = {"s": m.get("s", "idle"), "cloud": m.get("cloud", 0),
                        "rec": m.get("rec", 0)}
            s = self.srv["s"]
            if s == "listening" and self.srv["cloud"]:
                self.heard, self.drop_reply_until = "", 0.0       # a new request begins
                self.skill_done_for = ""
                self.holding, self.held = False, []
                self.thinking = self.tool_pending = False
            elif s == "thinking":
                self.thinking = True
                if self._think_task is None or self._think_task.done():
                    self._think_task = asyncio.create_task(self._think_aloud())
            elif s == "speaking":
                self.thinking = self.tool_pending = False
            elif s == "idle":
                self.thinking = False                 # a pending tool keeps the fillers going
                if self.media and self.media.ducked:
                    asyncio.create_task(self._unduck_when_quiet())
            if m.get("rec"):
                self.recording = True
            # stream only while the server is listening; it tells us when to stop
            was = self.streaming
            self.streaming = self.srv["s"] == "listening" and not self.muted
            if self.streaming and not was:
                self.turn.start(waiting=False)        # a follow-up: the server is listening already
            elif was and not self.streaming:
                t = self.turn.stats                   # numbers only, never what was said
                log.info("turn over: began by %s after %.1f s of waiting, %d pause frames held back, "
                         "%d of them never sent", t["began"] or "?", t["waited"] * 0.04, t["held"], t["dropped"])
                self.turn.stats = {"held": 0, "dropped": 0, "waited": 0, "began": ""}
            if not self.streaming:
                self.send_from = None
            log.info("state %s%s%s", self.srv["s"], " cloud" if self.srv["cloud"] else "",
                     " rec" if self.srv["rec"] else "")
        elif t == "earcon":
            kind = m.get("kind", "chime")
            notes = EARCONS.get(kind, EARCONS["chime"])
            if kind == "wake" and time.monotonic() - self.chimed_at < 3.0:
                pass                                  # already chimed when the word was heard
            elif kind == "alarm":
                rounds = self.cfg["alarm"]["rounds"]
                pcm = b"".join(tone(notes, each=0.09, gain=0.3) + bytes(24000) for _ in range(rounds))
                self.alarm_until = time.monotonic() + len(pcm) / 48000
                log.info("alarm ringing")
                self.playback.play(pcm)
            else:
                self.playback.play(tone(notes))
        elif t == "stop_audio":
            self.playback.flush()
            self.reply_until = 0.0
        elif t == "listening":
            log.info("follow-up window open for %.1f s", float(m.get("until_s") or 0))
        elif t == "turn.done":
            if m.get("latency_ms") is not None:
                log.info("turn done: server latency %s ms", m["latency_ms"])
        elif t == "transcript.in":
            log.debug("%s %s", t, m.get("text"))
            if m.get("typed"):
                # our own "say this line" request, echoed: from here on the audio is the
                # announcement, so stop throwing reply audio away
                self.drop_reply_until = 0.0
            else:
                self.heard += str(m.get("text") or "")
                if self.media:
                    if not self.holding and COMMAND_LIKE.match(self.heard):
                        self.holding, self.hold_t, self.held = True, time.monotonic(), []
                        self.playback.flush()         # cut a "Sorry" that already started
                        self.reply_until = 0.0
                    asyncio.create_task(self._maybe_skill())
        elif t == "transcript.out":
            log.debug("%s %s", t, m.get("text"))
        elif t == "tool.call":
            log.info("tool call: %s", m.get("name"))
            if m.get("name") not in ("confirm_action", "cancel_action"):
                self.tool_pending = True
                self._filler("TOOL")                  # spoken only in "voice" mode
                if self._think_task is None or self._think_task.done():
                    self._think_task = asyncio.create_task(self._think_aloud())
        elif t == "tool.result":
            log.info("tool result: %s ok=%s status=%s", m.get("name"), m.get("ok"),
                     m.get("status"))
            if m.get("name") == "stop_recording" and m.get("ok"):
                self.recording = False
        elif t == "confirm.request":
            log.info("confirmation waiting (%s), voice_ok=%s: confirm on the ANVE screen",
                     m.get("action"), m.get("voice_ok"))
            if not m.get("voice_ok"):
                self.playback.play(tone(EARCONS["chime"]))
                if self.ring:
                    self.ring.flash((255, 120, 0), 0.6)
        elif t == "confirm.result":
            log.info("confirmation %s: %s", m.get("status"), str(m.get("summary"))[:120])
            card = m.get("card") or {}
            if isinstance(card, dict) and card.get("kind") == "recording" and \
                    m.get("status") == "done":
                self.recording = True
                log.info("meeting recording started: streaming the room continuously")
        elif t == "recording":
            self.recording = m.get("state") == "on"
            log.info("recording %s %s", m.get("state"), m.get("note") or "")
        elif t == "notice":
            log.info("notice (%s%s): %s", m.get("level"),
                     ", announcement" if m.get("announce") else "", str(m.get("text"))[:200])
        elif t == "notices":
            for it in m.get("items") or []:
                log.info("waiting notice: %s", str(it)[:200])
        elif t == "job":
            log.info("job %s %s: %s", m.get("kind"), m.get("id"), m.get("status"))
        elif t == "ready":
            if not m.get("voice_confirm"):
                log.info("room device: spoken confirmations are refused; use the screen")
        self.look()

    # ---------- audio devices ----------

    async def on_devices(self, mic_ok: bool, speaker_ok: bool):
        self.mic_ok, self.speaker_ok = mic_ok, speaker_ok
        if speaker_ok:
            await asyncio.to_thread(self._set_volume)
        self.look()

    async def on_canceller(self):
        """The echo canceller was (re)loaded: its nodes are new, so both streams must be
        opened again on them."""
        await self.capture.kick()
        self.playback.flush()
        asyncio.create_task(self._startup_sound())

    async def _startup_sound(self):
        """Once per start: a rising chord when the speaker can really be used, that is
        when the microphone and speaker are up AND ANVE is connected. After a power-on
        this is how you know it is ready. If the speaker is switched on later, it plays
        then."""
        if self.started_sound or not (self.link and self.ec.ready.is_set()):
            return
        self.started_sound = True
        await asyncio.sleep(2.0)                      # let a Bluetooth speaker wake up
        self.playback.play(bytes(int(24000 * 2 * 0.6)) +
                           tone(EARCONS["start"], each=0.11, ring=0.55, gain=0.2))
        log.info("ready: startup sound played")

    async def route_watchdog(self):
        """Every 10 s, check that the capture really records from the echo-cancelled
        source. PipeWire once moved it onto the Bluetooth speaker's silent input."""
        from .playback import EchoCancel as EC
        while True:
            await asyncio.sleep(10)
            if not self.ec.ready.is_set():
                continue
            src = await asyncio.to_thread(self.capture.linked_to)
            if src and src != EC.SOURCE:
                log.warning("the capture was recording from %s, not the echo-cancelled "
                            "microphone; reopening it", src)
                await self.capture.kick()

    def _remember_volume(self, vol: float):
        """Write a volume asked for by voice into config.toml, so it survives a restart."""
        try:
            p = self.cfg["_path"]
            s = open(p).read()
            s2, n = re.subn(r"(?m)^(speaker_volume\s*=\s*)[0-9.]+", lambda m: f"{m.group(1)}{vol:.2f}", s, count=1)
            if n and s2 != s:
                open(p, "w").write(s2)
        except OSError:
            log.warning("could not save the volume to the config file")

    def _set_volume(self):
        want = self.cfg["audio"]["speaker_volume"]
        match = self.cfg["audio"]["speaker"].lower()
        for n in nodes("Audio/Sink"):
            if match in n["name"].lower() or match in n["description"].lower():
                subprocess.run(["wpctl", "set-volume", str(n["id"]), f"{want:.2f}"],
                               capture_output=True)
                subprocess.run(["wpctl", "set-mute", str(n["id"]), "0"], capture_output=True)
                log.info("speaker volume set to %.0f %%", want * 100)

    async def bluetooth_keeper(self):
        mac = self.cfg["audio"]["bluetooth_mac"]
        if not mac:
            return
        while True:
            await asyncio.sleep(15)
            if self.speaker_ok:
                continue          # the output exists: leave a working link alone
            info = await asyncio.to_thread(
                subprocess.run, ["bluetoothctl", "info", mac], capture_output=True, text=True)
            if "Connected: yes" not in info.stdout:
                log.warning("Bluetooth speaker not connected; trying %s", mac)
                await asyncio.to_thread(
                    subprocess.run, ["bluetoothctl", "--timeout", "12", "connect", mac],
                    capture_output=True)

    async def keep_speaker_awake(self):
        """Many Bluetooth speakers switch themselves off after some minutes without
        audio (this one dropped off 20 minutes after its last sound, 16 Nov 2026). Every
        `keepalive_s` seconds of silence, send it one second of a 25 Hz tone at -50 dBFS:
        a real audio stream, far below what the driver can reproduce or anyone can hear.
        Whether this particular speaker's power-off timer honours it is not verified."""
        every = float(self.cfg["audio"].get("keepalive_s", 0) or 0)
        if every <= 0:
            return
        import numpy as np
        t = np.arange(24000) / 24000.0
        env = np.minimum(1.0, np.minimum(t, 1.0 - t) / 0.2)
        quiet = (np.sin(2 * np.pi * 25 * t) * env * 0.003 * 32767).astype(np.int16).tobytes()
        last = time.monotonic()
        while True:
            await asyncio.sleep(5)
            if self.playback.active:
                last = time.monotonic()
            elif self.speaker_ok and time.monotonic() - last >= every:
                self.playback.play(quiet)
                last = time.monotonic()

    # ---------- microphone ----------

    async def _send_backlog(self):
        """After a wake: the audio from the end of the wake word up to now."""
        start, self.send_from = self.send_from, None
        rest = [(f, v, q) for idx, f, v, q in list(self.buf) if idx + FRAME > start]
        if self.ptt or self.recording:                # the button, a meeting: everything, as it is
            for f, _v, _q in rest:
                await self.client.send_audio(f)
            return
        for n, (f, v, q) in enumerate(rest):
            # all but the newest were heard before the chime: they do not use up the
            # person's time to start (turn.py)
            for out in self.turn.feed(f, v, q, live=n == len(rest) - 1):
                await self.client.send_audio(out)

    async def mic_loop(self):
        await self.ec.ready.wait()
        async for raw in self.capture.frames():
            prob = self.vad.feed(raw)                 # judged before any gain is applied
            speech = prob >= 0.5
            # the uplink frame comes out 2 frames (80 ms) late, with its gate decided
            # with hindsight; the spotter gets this frame now, ungated
            # music turned down underneath a request: only frames that stand clearly above
            # it count as speech, so the server still sees silence when you stop talking
            under_music = self._media_on()
            frame = self.agc.process(raw, speech, gate_open=prob >= 0.15 and not under_music,
                                     strict=under_music)
            heard = self.agc.ungated
            idx = self.index
            self.index += FRAME
            # for turn-taking: is this speech, or silence (no speech AND down at the room's
            # own noise)? Decided now, used when the delayed uplink frame comes out.
            self._flags.append((self.agc.speech, not self.agc.speech and self.agc.level_in <= self.agc.floor + 6.0))
            voiced, quiet = self._flags.popleft() if len(self._flags) > self.agc.lookahead else (False, True)
            self.buf.append((idx - self.agc.delay_samples, frame, voiced, quiet))
            now = time.monotonic()
            if self.ring:
                self.ring.level(self.playback.level if getattr(self, "_look", "") == "speaking"
                                else self.agc.meter())
            if self.muted or not self.link:
                continue
            if self.ptt or self.recording:
                if self.send_from is not None:
                    await self._send_backlog()
                else:
                    await self.client.send_audio(frame)
                continue
            # While the speaker's own music plays at full volume, nothing is streamed: the
            # echo canceller cannot remove music completely, and the server answered the
            # lyrics as if they were requests. "Hey ANVE" turns the music down to half
            # (it keeps playing) and from then until the conversation is over the
            # microphone is streamed as usual, with a stricter speech gate (see below).
            if self.streaming and not self._media_loud():
                if now < self.reply_until:
                    # our own reply is still in the room: not a request. Send silence,
                    # not nothing: the server measures a request in frames received, and
                    # with no frames it once sat in "listening" for 2.5 minutes.
                    await self.client.send_audio(SILENCE)
                    continue
                if self.send_from is not None:
                    await self._send_backlog()
                else:
                    for out in self.turn.feed(frame, voiced, quiet):
                        await self.client.send_audio(out)
                continue
            if self.streaming:
                await self.client.send_audio(SILENCE)    # music at full volume: same reason
            # idle, thinking or speaking: nothing leaves the device; spot locally
            speaking = now < self.reply_until
            if speaking and not self.cfg["wake"]["barge_in"]:
                continue
            for kind, pcm, start in self.segmenter.feed(heard, idx, speech):
                if kind == "final" and start == self.last_wake_start:
                    continue
                self._spot_or_wait(pcm, start, False)
            if self._media_on():
                # A song keeps the "speech" going, so "Hey ANVE" never starts a segment.
                # While music plays, also check the last 3 s every 2 s for the wake
                # phrase anywhere in it (the phrase is under 1 s, the overlap is 1 s).
                # Every 2 s, not 1.5: one decode takes about 1 s, and the music needs the
                # processor too.
                self.win.append(heard)
                self.win_n += 1
                if self.win_n >= 50 and len(self.win) >= 60:
                    self.win_n = 0
                    pcm = b"".join(self.win)
                    self._spot_or_wait(pcm, idx + FRAME - len(pcm) // 2, True)
            elif self.win:
                self.win.clear()

    def _spot_or_wait(self, pcm: bytes, start: int, anywhere: bool):
        """Give a piece of sound to the recogniser, or keep it until the recogniser is free.

        One decode takes a second or more. Until 19 Nov 2026 whatever arrived meanwhile was
        thrown away, and that is how "Hey ANVE" went unheard: the early look at a phrase
        (often cut in the middle of the name) was being decoded when the whole phrase
        arrived, and the whole phrase was dropped. Replaying the owner's recording of five
        wake phrases that way woke the speaker once. Now the newest arrival waits: one
        place, the newer one wins, and a real segment is never pushed out by one of the
        "anywhere" windows used while music plays."""
        if not self.spot_busy:
            self.spot_busy = True
            asyncio.create_task(self._spot(pcm, start, anywhere))
            return
        if self.spot_next is not None:
            if anywhere and not self.spot_next[2]:
                return                                       # a window never replaces a segment
            self.stats["replaced"] += 1
        self.spot_next = (pcm, start, anywhere)
        self.stats["waited"] += 1

    async def _spot(self, pcm: bytes, start: int, anywhere: bool = False):
        try:
            await self._spot_one(pcm, start, anywhere)
        except Exception:                                    # noqa: BLE001
            log.exception("handling a spotted segment failed")
        finally:
            nxt, self.spot_next = self.spot_next, None
            if nxt is None:
                self.spot_busy = False
            else:
                asyncio.create_task(self._spot(*nxt))        # still busy: straight on to the next

    def _diagnosing(self) -> bool:
        """True while someone has asked for a look at the wake word (tools/wake_diag.sh):
        for those minutes only, what the recogniser heard is logged and the clips are kept."""
        now = time.time()
        if now - self._diag_checked > 5.0:
            self._diag_checked = now
            try:
                self._diag_until = float((pair_file().parent / "diagnose-until").read_text().strip())
            except (OSError, ValueError):
                self._diag_until = 0.0
        return now < self._diag_until

    def _diag_keep(self, pcm: bytes, d: dict, anywhere: bool):
        try:
            folder = Path(__file__).resolve().parent.parent / "recordings" / "live"
            folder.mkdir(parents=True, exist_ok=True)
            name = time.strftime("%H%M%S") + ("-wake" if d["wake"] else "-veto" if d["vetoed"] else "-no") + \
                ("-music" if anywhere else "") + ".wav"
            with wave.open(str(folder / name), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(16000)
                w.writeframes(pcm)
            for old in sorted(folder.glob("*.wav"))[:-150]:
                old.unlink(missing_ok=True)
            log.info("diag %s: %.1f s, %d ms, heard %r", name, len(pcm) / 32000, d["ms"], d["text"][:80])
        except OSError:
            pass

    async def _spot_one(self, pcm: bytes, start: int, anywhere: bool = False):
        try:
            d = await asyncio.get_running_loop().run_in_executor(
                self.pool, self.spotter.detect, pcm, anywhere)
        except Exception:                                    # noqa: BLE001
            log.exception("wake spotter failed")
            return
        self.stats["spot_ms"].append(d["ms"])
        if self._diagnosing():
            self._diag_keep(pcm, d, anywhere)
        # what the room said stays out of the journal unless someone asks for DEBUG
        log.debug("spotter: %.1f s segment, %d ms, wake=%s barge=%s vetoed=%s heard %r",
                  len(pcm) / 32000, d["ms"], d["wake"], d["barge"], d["vetoed"], d["text"][:80])
        if self.muted or self.ptt or self.recording or not self.link or \
                (self.streaming and not self._media_loud()):
            return
        speaking = time.monotonic() < self.reply_until
        if d["wake"] and time.monotonic() - self.wake_t < 4.0:
            return                # the same "Hey ANVE", caught again in an overlapping window
        if d["wake"] and start != self.last_wake_start:
            self.last_wake_start = start
            self.stats["wakes"] += 1
            if speaking:
                self.playback.flush()
                self.reply_until = 0.0
            if self.media:
                await self.media.duck()               # music goes quiet while you talk
            # the listening chime, at once: no waiting for the server to ask for it
            self.playback.play(tone(EARCONS["wake"]))
            self.chimed_at = time.monotonic()
            log.info("wake word (score %.2f, decode %d ms%s)", d["score"], d["ms"],
                     ", barge-in" if speaking else "")
            await self.client.send({"type": "wake", "word": self.cfg["wake"]["word"],
                                    "score": d["score"]})
            # nothing is sent until the request begins; over music only a voice counts as a start
            self.turn.start(waiting=True, loud_ok=not self._media_on())
            self.send_from = start + int(d["end_s"] * RATE)
            self.streaming, self.wake_t = True, time.monotonic()
            self.segmenter.reset()
            if self.ring:
                self.ring.flash((255, 255, 255), 0.15)
        elif speaking and (d["barge"] or d["stop_word"]):
            self.stats["barges"] += 1
            log.info("spoken stop (decode %d ms)", d["ms"])
            self.playback.flush()
            self.reply_until = 0.0
            await self.client.send({"type": "interrupt"})
        elif self.media and self.media.playing and d["barge"]:
            # a plain "stop" or "pause" while music plays: no wake word needed
            log.info("spoken stop: music paused")
            self.media.ducked = False
            await self.media.pause()
        self.look()

    # ---------- buttons ----------

    async def press_mute(self):
        self.muted = not self.muted
        self.streaming = self.ptt = False
        log.info("mute %s (hardware button)", "on" if self.muted else "off")
        await self.client.send({"type": "mute", "on": self.muted, "hw": True})
        self.look()

    async def hold_mute(self):
        if self.client.halted:
            log.info("mute button held: trying the server again")
            self.client.resume()

    async def press_action(self):
        now = time.monotonic()
        self.action_down, self.action_t = True, now
        press_index = self.index
        was_speaking = now < self.reply_until
        if now < self.alarm_until or was_speaking:
            self.alarm_until = 0.0
            self.playback.flush()
            self.reply_until = 0.0
            if was_speaking:
                log.info("action button while speaking: interrupt")
                await self.client.send({"type": "interrupt"})
        self.look()
        await asyncio.sleep(self.cfg["buttons"]["tap_s"])
        if self.action_down and self.action_t == now and not self.muted and self.link:
            log.info("push-to-talk start")
            if self.media:
                await self.media.duck()
            self.playback.play(tone(EARCONS["wake"]))
            self.chimed_at = time.monotonic()
            await self.client.send({"type": "ptt.start"})
            self.send_from = press_index
            self.ptt = True
            self.look()

    async def release_action(self):
        now = time.monotonic()
        self.action_down = False
        if self.ptt:
            self.ptt = False
            log.info("push-to-talk end (%.1f s)", now - self.action_t)
            await self.client.send({"type": "ptt.end"})
        elif self.recording and now - self.last_tap < 0.6:
            log.info("double tap during a recording: record.stop")
            await self.client.send({"type": "record.stop"})
        self.last_tap = now
        self.look()

    def _buttons(self, loop):
        from gpiozero import Button
        b = self.cfg["buttons"]
        fire = lambda coro: loop.call_soon_threadsafe(asyncio.ensure_future, coro())  # noqa: E731
        self._mute_btn = Button(b["mute_gpio"], pull_up=True, bounce_time=0.03, hold_time=3)
        self._mute_btn.when_pressed = lambda: fire(self.press_mute)
        self._mute_btn.when_held = lambda: fire(self.hold_mute)
        self._act_btn = Button(b["action_gpio"], pull_up=True, bounce_time=0.03)
        self._act_btn.when_pressed = lambda: fire(self.press_action)
        self._act_btn.when_released = lambda: fire(self.release_action)
        log.info("buttons: mute on GPIO%d, action on GPIO%d", b["mute_gpio"], b["action_gpio"])

    # ---------- run ----------

    async def ticker(self):
        while True:
            await asyncio.sleep(0.2)
            # a wake the server never answered (no `state listening` within 3 s): stop
            # streaming rather than keep uploading the room
            if self.streaming and self.srv["s"] != "listening" and \
                    time.monotonic() - self.wake_t > 3.0:
                log.warning("the server did not start listening after the wake; stopped streaming")
                self.streaming, self.send_from = False, None
                self.playback.play(tone(EARCONS["end"]))      # audible "that went nowhere"
                if self.media and self.media.ducked:
                    asyncio.create_task(self.media.unduck())  # give the music its volume back
            self.look()

    async def run(self, *, leds: bool = True, buttons: bool = True):
        loop = asyncio.get_running_loop()
        if leds and self.cfg["lumina"]["enabled"]:
            # The light strip belongs to LUMINA, a separate program (lumina/): the speaker
            # only reports its state to it (lights.py). The speaker itself never opens the
            # strip then, whatever [leds] says: one program on the SPI bus, not two.
            try:
                from .lights import Lights
                self.ring = Lights(self, self.cfg["lumina"]["port"])
            except Exception as e:                           # noqa: BLE001
                log.warning("lights unavailable (%s)", e)
        elif leds and self.cfg["leds"]["enabled"]:
            try:
                from .led import Strip
                from .ring import Ring
                self.ring = Ring(Strip(self.cfg["leds"]["count"], self.cfg["leds"]["brightness"]))
            except Exception as e:                           # noqa: BLE001
                log.warning("LEDs unavailable (%s)", e)
        if buttons:
            try:
                self._buttons(loop)
            except Exception as e:                           # noqa: BLE001
                log.warning("buttons unavailable (%s)", e)
        self.look()
        log.info("loading the wake-word model %s", self.cfg["wake"]["model"])
        await loop.run_in_executor(self.pool, self.spotter.load)
        if self.fillers:
            await asyncio.to_thread(self.fillers.load)
            log.info("thinking sounds: %d phrases ready",
                     sum(len(v) for v in self.fillers.clips.values()))
        self.playback.start()
        tasks = [asyncio.create_task(c) for c in (
            self.ec.run(), self.mic_loop(), self.client.run(), self.ticker(),
            self.bluetooth_keeper(), self.keep_speaker_awake(), self.route_watchdog(),
            self.media_watch())]
        if self.app:
            tasks.append(asyncio.create_task(self.app.run()))
        stop = asyncio.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        await stop.wait()
        log.info("stopping")
        await self.client.close()
        for t in tasks:
            t.cancel()
        if self.media:
            await self.media.close()
        await self.capture.close()
        await self.playback.close()
        await self.ec.close()
        if self.ring:
            self.ring.set("off")
            await asyncio.sleep(0.1)
            self.ring.close()


def main():
    ap = argparse.ArgumentParser(description="ANVE smart speaker")
    ap.add_argument("--config")
    ap.add_argument("--url", help="override the server URL (e.g. the mock server)")
    ap.add_argument("--key-file")
    ap.add_argument("--mode", choices=("wake", "ptt"))
    ap.add_argument("--no-leds", action="store_true")
    ap.add_argument("--no-buttons", action="store_true")
    # DEBUG also logs what the wake-word spotter heard (words spoken in the room): use
    # it for a diagnosis, not as the normal setting
    ap.add_argument("--log-level", default=os.environ.get("ANVE_LOG_LEVEL", "INFO"))
    a = ap.parse_args()
    logging.basicConfig(level=a.log_level.upper(), format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("faster_whisper").setLevel(logging.WARNING)
    # At DEBUG the websockets library logs the whole handshake, including the
    # "Authorization: Bearer <device key>" header. Never let it go below INFO.
    for name in ("websockets", "websockets.client", "websockets.server", "asyncio"):
        logging.getLogger(name).setLevel(logging.INFO)
    cfg = C.load(a.config)
    if a.url:
        cfg["server"]["url"] = a.url
    if a.key_file:
        cfg["server"]["key_file"] = a.key_file
    if a.mode:
        cfg["server"]["mode"] = a.mode
    log.info("config %s; server %s; mode %s", cfg["_path"], cfg["server"]["url"],
             cfg["server"]["mode"])
    asyncio.run(Device(cfg).run(leds=not a.no_leds, buttons=not a.no_buttons))


if __name__ == "__main__":
    main()
