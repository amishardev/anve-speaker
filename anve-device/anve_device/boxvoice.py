"""The speaker's voice through the ANVE Box cloud ([voice] backend = "anve-box").

The team's handoff of 24 Nov 2026 (box voice, part C of the box-cloud agreement, decision
D39): "Hey ANVE" is answered by the ANVE Box cloud's Gemini voice instead of the old ANVE
platform. The connection itself is the team's own code, vendored unchanged:
`anvebox_agent.cloud.voice.VoiceTalk` (one WebSocket per talk, proved with the box's own
identity, 16 kHz PCM up, 24 kHz PCM down).

This class is the adapter between that and the rest of the speaker. It has the same face
as `client.Client` (send, send_audio, run, close, halted; on_json / on_audio / on_link), and
it turns the box cloud's events into the messages `main.Device.on_json` already knows, so
the wake word, `Turn`, the local skills, the lights and the playback path do not change:

    old platform message        made here from
    ------------------------    ------------------------------------------------------
    state listening (cloud)     `ready`; the first `heard` of a new request; `interrupted`
    transcript.in               `heard`
    tool.call (thinking pulse)  a second of nothing after the last `heard`
    state speaking + audio      the first sound of an answer
    transcript.out              `said`
    stop_audio                  `interrupted`
    turn.done, listening        `turn` (then the follow-up window runs HERE, see below)
    state idle                  `ended`, or the talk could not start
    notice                      `ended` with a text for the person, `warning`

What the old platform did and this cloud does not, so the speaker does it:
  * There is no connection between talks. `send({"type": "wake"})` opens one; nothing
    reaches the cloud before that.
  * The cloud never says "I stopped listening". After ANVE has answered, the speaker keeps
    the talk open for `follow_up_s` once the answer has left the room; a `heard` in that
    time carries on in the same talk, silence hangs up. A talk in which nothing was heard
    at all is hung up after `silent_s`.
  * The microphone is buffered while the talk opens (one to two seconds) and then sent no
    more than 1.5 s ahead of the clock: the cloud does not pass on sound that runs more
    than 2 s ahead.
  * `interrupt` (a spoken "stop", the button, a local skill taking the request): the rest
    of ANVE's answer is thrown away here and the talk is hung up. Gemini needs no message.
  * Typed turns (`text`) do not exist: `send` returns False for them and `can_type` is
    False, so the speaker says its own lines itself.

Never logged: the identity, and what was said (the speaker's rule: numbers and states only).
"""
import asyncio
import logging
import time
from collections import deque

from anvebox_agent.cloud.voice import VoiceTalk

log = logging.getLogger("anve.boxvoice")
FRAME_S = 0.04
AHEAD_S = 1.5                      # how far ahead of the clock the microphone may be sent


class BoxVoice:
    can_type = False               # no typed "say exactly these words" turns on this cloud

    def __init__(self, cloud: str, identity, *, follow_up_s: float = 8.0, silent_s: float = 9.0,
                 on_json=None, on_audio=None, on_link=None, room_busy=None, talk_factory=VoiceTalk):
        self.cloud, self.identity = cloud, identity
        self.follow_up_s, self.silent_s = follow_up_s, silent_s
        self.on_json, self.on_audio, self.on_link = on_json, on_audio, on_link
        self.room_busy = room_busy or (lambda: False)      # is ANVE's voice still in the room?
        self._factory = talk_factory
        self.halted = ""                                   # (the old client's; never set here)
        self.sessions_opened = 0
        self.talk = None
        self._task = self._pump = self._watch = self._think = None
        self._q: deque = deque(maxlen=250)                 # 10 s of microphone waiting to go
        self._sent_s = 0.0
        self._ready_t = 0.0
        self._state = "idle"
        self._drop_sound = False                           # the rest of an answer nobody wants
        self._new_request = True                           # the next `heard` starts a request
        self._last_heard = self._turn_t = 0.0
        self._heard_any = False
        self._stop = asyncio.Event()

    # ---------- the old client's face ----------

    @property
    def up(self) -> bool:
        return True

    def resume(self):
        pass

    async def run(self):
        """Nothing is kept open between talks: say "up" once and wait."""
        if self.on_link:
            await self.on_link(True, "")
        await self._stop.wait()

    async def close(self):
        self._stop.set()
        await self._hang_up()

    async def send(self, obj: dict) -> bool:
        t = obj.get("type")
        if t in ("wake", "ptt.start"):
            await self._open()
            return True
        if t == "interrupt":
            self._drop_sound = True
            await self._hang_up()
            return True
        if t == "mute":
            if obj.get("on"):
                await self._hang_up()
            return True
        return False                                       # text, record.stop, ptt.end, ...

    async def send_audio(self, pcm16: bytes) -> bool:
        """One 40 ms microphone frame. Kept until the talk is ready, then sent in order."""
        if self.talk is None or self.talk.ended is not None:
            return False
        self._q.append(pcm16)
        return True

    # ---------- one talk ----------

    async def _open(self):
        if self.talk is not None and self.talk.ended is None:
            # woken again inside a talk (talking over ANVE): the same talk goes on
            self._drop_sound = True
            self._new_request = True
            self._turn_t = 0.0
            await self._say_state("listening")
            return
        self._q.clear()
        self._sent_s, self._ready_t = 0.0, 0.0
        self._drop_sound, self._new_request, self._heard_any = False, True, False
        self._last_heard = self._turn_t = 0.0
        self.sessions_opened += 1
        self.talk = self._factory(self.cloud, self.identity, on_sound=self._on_sound, on_event=self._on_event)
        self._opened_t = time.monotonic()
        self._task = asyncio.create_task(self._run_talk(self.talk))
        self._pump = asyncio.create_task(self._pump_mic(self.talk))
        self._watch = asyncio.create_task(self._watch_talk(self.talk))

    async def _hang_up(self):
        talk = self.talk
        if talk is not None and talk.ended is None:
            try:
                await talk.end()
            except Exception:                                # noqa: BLE001
                pass

    async def _run_talk(self, talk):
        try:
            ended = await talk.run()
        except Exception as e:                               # noqa: BLE001
            log.exception("the voice talk failed")
            ended = {"type": "ended", "reason": "failed", "text": type(e).__name__, "seconds": 0}
        if self.talk is not talk:
            return
        reason, text = str(ended.get("reason") or ""), str(ended.get("text") or "")
        log.info("talk over: %s after %s s%s", reason, ended.get("seconds", 0),
                 " (never became ready)" if not talk.ready.is_set() else "")
        for task in (self._pump, self._watch, self._think):
            if task and task is not asyncio.current_task():
                task.cancel()
        self.talk = None
        self._q.clear()
        if reason == "meeting" and ended.get("meeting"):
            # "ANVE, record this meeting": the cloud started one and ended the talk (part D
            # of the agreement). The speaker now records the room for it (meetings.py).
            await self._emit({"type": "meeting.start", "meeting": ended.get("meeting")})
        elif reason not in ("hung_up",) and text:
            # something the person should know: not added to an account, the owner is
            # already talking to ANVE in the app, the cloud cannot be reached, ...
            await self._emit({"type": "notice", "level": "error" if reason in ("refused", "unreachable", "failed", "dropped") else "info",
                              "announce": True, "reason": reason, "text": text})
        await self._say_state("idle", cloud=0)

    async def _pump_mic(self, talk):
        """Microphone frames to the cloud, in order, never far ahead of the clock."""
        try:
            await talk.ready.wait()
            while talk.ended is None:
                if not self._q:
                    await asyncio.sleep(0.01)
                    continue
                ahead = self._sent_s - (time.monotonic() - self._ready_t)
                if ahead > AHEAD_S:
                    await asyncio.sleep(min(0.2, ahead - AHEAD_S))
                    continue
                frame = self._q.popleft()
                if not await talk.send_sound(frame):
                    return
                self._sent_s += len(frame) / 32000
                # a gap in what the speaker chose to send is not "behind the clock"
                self._sent_s = max(self._sent_s, time.monotonic() - self._ready_t - 0.2)
        except asyncio.CancelledError:
            pass

    async def _watch_talk(self, talk):
        """Hang up when nobody is talking any more (the cloud never does that itself)."""
        try:
            while talk.ended is None:
                await asyncio.sleep(0.25)
                now = time.monotonic()
                if not talk.ready.is_set():
                    continue
                if self.room_busy():
                    if self._turn_t:
                        self._turn_t = now               # the window starts when the room is quiet
                    continue
                if self._turn_t and now - self._turn_t > self.follow_up_s and now - self._last_heard > self.follow_up_s:
                    log.info("nobody spoke for %.0f s after the answer: hanging up", self.follow_up_s)
                    await talk.end()
                    return
                if not self._heard_any and now - self._ready_t > self.silent_s:
                    log.info("nothing was heard in %.0f s: hanging up", self.silent_s)
                    await talk.end()
                    return
        except asyncio.CancelledError:
            pass

    # ---------- the cloud's events, as the speaker's old messages ----------

    async def _emit(self, msg: dict):
        if self.on_json:
            try:
                await self.on_json(msg)
            except Exception:                                # noqa: BLE001
                log.exception("handler failed for %s", msg.get("type"))

    async def _say_state(self, s: str, cloud: int = 1):
        self._state = s
        await self._emit({"type": "state", "s": s, "cloud": cloud, "rec": 0})

    async def _on_sound(self, pcm: bytes):
        if self._drop_sound or not pcm:
            return
        if self._think:
            self._think.cancel()
        if self._state != "speaking":
            await self._say_state("speaking")
        if self.on_audio:
            await self.on_audio(pcm)

    async def _thinking_soon(self):
        try:
            await asyncio.sleep(1.0)
            if self._state == "listening" and self._heard_any and time.monotonic() - self._last_heard >= 0.95:
                # NOT a "thinking" state: the speaker stops sending the microphone outside
                # "listening", and Gemini needs to hear the silence to know the person has
                # finished. This only starts the soft thinking pulse; the first sound of
                # the answer ends it.
                await self._emit({"type": "tool.call", "name": "thinking"})
        except asyncio.CancelledError:
            pass

    async def _on_event(self, ev: dict):
        t = ev.get("type")
        if t == "ready":
            self._ready_t = time.monotonic()
            log.info("ANVE is listening (%.1f s after the wake word; this talk may last %s s)",
                     self._ready_t - self._opened_t, ev.get("limit"))
            await self._say_state("listening")
        elif t == "heard":
            self._last_heard, self._heard_any = time.monotonic(), True
            self._turn_t = 0.0
            if self._new_request or self._state == "speaking":
                self._new_request, self._drop_sound = False, False
                await self._say_state("listening")           # a new request: main clears its text
            await self._emit({"type": "transcript.in", "text": ev.get("text") or ""})
            if self._think:
                self._think.cancel()
            self._think = asyncio.create_task(self._thinking_soon())
        elif t == "said":
            await self._emit({"type": "transcript.out", "text": ev.get("text") or ""})
        elif t == "interrupted":
            self._drop_sound = False
            await self._emit({"type": "stop_audio"})
            await self._say_state("listening")
        elif t == "turn":
            self._drop_sound = False
            self._new_request = True
            self._turn_t = time.monotonic()
            await self._emit({"type": "turn.done"})
            await self._emit({"type": "listening", "until_s": self.follow_up_s})
            await self._say_state("listening")
        elif t in ("action", "rule"):
            log.info("ANVE %s", "did something" if t == "action" else "proposed a rule")
            await self._emit({"type": "tool.result", "name": t, "ok": True, "status": "done"})
        elif t == "warning":
            log.info("a minute of this talk is left")
        # "chat", "ended" (handled when run() returns) and unknown types: nothing to do
