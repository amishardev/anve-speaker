#!/usr/bin/env python3
"""Talk to the ANVE assistant from the command line, with the device key, and print
everything the server sends back. For proving the key works and for testing what the
assistant can do, without a microphone or a speaker.

    sudo .venv/bin/python tools/ask.py --text "What can you do?"       typed, text reply
    sudo .venv/bin/python tools/ask.py --say "What is on today?"       synthesized voice,
                                                                       through push-to-talk
    sudo .venv/bin/python tools/ask.py --suite readonly                the capability sweep
    sudo .venv/bin/python tools/ask.py --list                          show the sweep

`sudo` only because the key file is root-only. One run is ONE session (the account may
open 30 an hour and 2 at once: stop the speaker service or close the browser's assistant
if the server answers `concurrent`).

What this tool never does: it never sends `confirm`. A read-back the assistant creates
stays unanswered and expires (5 minutes); on a room key the server refuses a spoken yes
anyway. Sweep groups:
    readonly   questions and lists: nothing is created or sent
    readback   requests that make the assistant ASK for confirmation; nothing happens
               unless the owner presses Confirm on their own screen
    acts       requests that act at once, with no read-back (a timer, a reminder, an
               email to the owner): run these only with the owner's go-ahead (--yes-acts)
"""
import argparse
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from anve_device.client import FRAME_BYTES, Client  # noqa: E402

SUITE = [
    # (group, category, what to say)
    ("readonly", "capabilities", "What can you do?"),
    ("readonly", "general knowledge", "Why is the sky blue? Answer in one sentence."),
    ("readonly", "live answer", "What is the weather in Sambalpur tomorrow?"),
    ("readonly", "calendar", "What is on today?"),
    ("readonly", "reminders", "What is due this week?"),
    ("readonly", "timers", "Which timers are running?"),
    ("readonly", "documents", "List my documents."),
    ("readonly", "sheets", "List my spreadsheets."),
    ("readonly", "meetings", "List my meetings."),
    ("readonly", "notes", "List my notes."),
    ("readonly", "agents", "List my agents."),
    ("readonly", "agents", "Is anything waiting for my approval?"),
    ("readonly", "finance", "Which invoices are overdue?"),
    ("readonly", "finance", "When is the next G S T due date?"),
    ("readonly", "workspace", "What does our leave policy say?"),
    ("readback", "notes", "Save a note: call the auditor on Monday."),
    ("readback", "documents", "Draft a leave application for next Monday."),
    ("readback", "sheets", "Make a monthly budget spreadsheet."),
    ("readback", "decks", "Make a deck about our G S T services for new clients."),
    ("readback", "images", "Make an image of a lighthouse at sunset."),
    ("readback", "meetings", "Record this meeting."),
    ("readback", "agents", "Create an agent that summarises overdue invoices every Friday."),
    ("acts", "timers", "Set a timer for one minute."),
    ("acts", "reminders", "Remind me tomorrow at ten in the morning to call the bank."),
    ("acts", "email", "Email me that."),
]


def tts_frames(text: str) -> list[bytes]:
    """`text` as 16 kHz mono PCM16 in 40 ms frames (espeak-ng, then sox)."""
    wav = subprocess.run(["espeak-ng", "-v", "en-us", "-s", "150", "--stdout", text],
                         capture_output=True, check=True).stdout
    raw = subprocess.run(["sox", "-t", "wav", "-", "-r", "16000", "-c", "1", "-b", "16",
                          "-e", "signed-integer", "-t", "raw", "-", "norm", "-6",
                          "pad", "0.3", "0.5"], input=wav, capture_output=True,
                         check=True).stdout
    raw += bytes(-len(raw) % FRAME_BYTES)
    return [raw[i:i + FRAME_BYTES] for i in range(0, len(raw), FRAME_BYTES)]


class Probe:
    def __init__(self, a):
        self.a = a
        self.events: list[dict] = []
        self.audio = 0
        self.ready = asyncio.Event()
        self.down = asyncio.Event()
        self.why = ""
        self.c = Client(a.url, a.key_file, engine=a.engine, mode="ptt", speak=a.speak,
                        codec=a.codec, name="anve-pi-speaker-test", on_json=self.on_json,
                        on_audio=self.on_audio, on_link=self.on_link)
        self.t_last = time.monotonic()

    async def on_json(self, m):
        self.t_last = time.monotonic()
        self.events.append(m)
        if self.a.verbose:
            print("   <-", json.dumps(m, ensure_ascii=False)[:300], flush=True)

    async def on_audio(self, pcm):
        self.t_last = time.monotonic()
        self.audio += len(pcm)

    async def on_link(self, up, why):
        if up:
            self.ready.set()
        else:
            self.why = why
            self.down.set()

    async def turn(self, text: str, *, voice: bool, timeout: float) -> dict:
        """One turn; returns what came back."""
        mark, a0, t0 = len(self.events), self.audio, time.monotonic()
        if voice:
            frames = await asyncio.to_thread(tts_frames, text)
            await self.c.send({"type": "ptt.start"})
            for f in frames:
                await self.c.send_audio(f)
                await asyncio.sleep(0.02)                # twice real time
            await self.c.send({"type": "ptt.end"})
        else:
            await self.c.send({"type": "text", "text": text})
        done_at = None
        while time.monotonic() - t0 < timeout and not self.down.is_set():
            await asyncio.sleep(0.2)
            ev = self.events[mark:]
            if done_at is None and any(e["type"] == "turn.done" for e in ev) and \
                    any(e["type"] == "transcript.out" for e in ev):
                done_at = time.monotonic()
            # Live answers a tool result in a turn of its own: wait for 2.5 quiet seconds
            if done_at and time.monotonic() - self.t_last > 2.5:
                break
        ev = self.events[mark:]
        tools = [(e.get("name"), e.get("status"), bool(e.get("ok")))
                 for e in ev if e["type"] == "tool.result"]
        return {
            "said": text, "voice": voice, "seconds": round(time.monotonic() - t0, 1),
            "heard": "".join(e.get("text", "") for e in ev if e["type"] == "transcript.in").strip(),
            "reply": "".join(e.get("text", "") for e in ev if e["type"] == "transcript.out").strip(),
            "tools": tools,
            "tool_summaries": [str(e.get("summary"))[:160] for e in ev if e["type"] == "tool.result"],
            "confirm": [{"action": e.get("action"), "voice_ok": e.get("voice_ok"),
                         "readback": str(e.get("readback"))[:160]}
                        for e in ev if e["type"] == "confirm.request"],
            "notices": [str(e.get("text"))[:160] for e in ev if e["type"] == "notice"],
            "jobs": [(e.get("kind"), e.get("status")) for e in ev if e["type"] == "job"],
            "earcons": [e.get("kind") for e in ev if e["type"] == "earcon"],
            "latency_ms": next((e.get("latency_ms") for e in reversed(ev)
                                if e["type"] == "turn.done"), None),
            "reply_audio_s": round((self.audio - a0) / 48000, 1),
            "errors": [str(e.get("message"))[:200] for e in ev if e["type"] == "error"],
            "finished": done_at is not None,
        }


def show(r: dict, label: str = ""):
    verdict = "ok" if r["finished"] and not r["errors"] else "NO REPLY" if not r["finished"] else "ERROR"
    if r["confirm"]:
        verdict += ", waits for Confirm on the screen" if not r["confirm"][0]["voice_ok"] \
            else ", waits for a yes"
    print(f"\n[{label}] {r['said']!r}  ->  {verdict}  ({r['seconds']} s"
          + (f", server latency {r['latency_ms']} ms" if r["latency_ms"] is not None else "")
          + (f", {r['reply_audio_s']} s of audio" if r["reply_audio_s"] else "") + ")")
    if r["voice"]:
        print(f"    heard as: {r['heard']!r}")
    for (name, status, ok), s in zip(r["tools"], r["tool_summaries"]):
        print(f"    tool {name}: {status}{'' if ok else ' (failed)'}: {s}")
    for c in r["confirm"]:
        print(f"    read-back ({c['action']}, voice_ok={c['voice_ok']}): {c['readback']}")
    for n in r["notices"]:
        print(f"    notice: {n}")
    for j in r["jobs"]:
        print(f"    job {j[0]}: {j[1]}")
    if r["earcons"]:
        print(f"    earcons: {', '.join(map(str, r['earcons']))}")
    for e in r["errors"]:
        print(f"    error: {e}")
    print(f"    reply: {r['reply'][:400]!r}")


async def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--text", action="append", default=[], help="a typed turn (repeatable)")
    ap.add_argument("--say", action="append", default=[], help="a spoken turn, synthesized (repeatable)")
    ap.add_argument("--suite", choices=("readonly", "readback", "acts", "all"))
    ap.add_argument("--yes-acts", action="store_true", help="the owner approved the 'acts' group")
    ap.add_argument("--typed", action="store_true", help="run the suite as typed turns instead of voice")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--url", default="wss://app.anveai.com/api/assistant/ws")
    ap.add_argument("--key-file", default="/etc/anve-device/key")
    ap.add_argument("--engine", default="live")
    ap.add_argument("--codec", default="opus")
    ap.add_argument("--speak", type=int, default=0, help="1 = also ask for reply audio")
    ap.add_argument("--timeout", type=float, default=45)
    ap.add_argument("--out", default=str(ROOT / "recordings" / "capability_results.json"))
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    a.speak = bool(a.speak)
    if a.list:
        for g, cat, text in SUITE:
            print(f"{g:9s} {cat:18s} {text}")
        return 0
    turns = [("typed", t, False) for t in a.text] + [("spoken", t, True) for t in a.say]
    if a.suite:
        groups = ("readonly", "readback", "acts") if a.suite == "all" else (a.suite,)
        if "acts" in groups and not a.yes_acts:
            print("The 'acts' group sets a timer, adds a reminder and sends an email to the "
                  "owner without a read-back.\nRun it only with the owner's go-ahead: add "
                  "--yes-acts.")
            groups = tuple(g for g in groups if g != "acts")
        turns += [(f"{g}/{cat}", text, not a.typed) for g, cat, text in SUITE if g in groups]
    if not turns:
        ap.error("nothing to do: give --text, --say or --suite")
    p = Probe(a)
    task = asyncio.create_task(p.c.run())
    try:
        await asyncio.wait_for(asyncio.wait([asyncio.create_task(p.ready.wait()),
                                             asyncio.create_task(p.down.wait())],
                                            return_when=asyncio.FIRST_COMPLETED), 25)
    except asyncio.TimeoutError:
        pass
    if not p.ready.is_set():
        print(f"NOT CONNECTED: {p.why or p.c.halted or 'no answer from the server in 25 s'}")
        for e in p.events:
            if e.get("type") in ("closing", "error"):
                print("   server said:", json.dumps(e)[:300])
        task.cancel()
        return 2
    r = p.c.ready
    await asyncio.wait_for(p.c.codec_known.wait(), 5)
    print(f"connected: session {r.get('session_id')}, engine {r.get('engine')} "
          f"({'cloud' if r.get('cloud') else 'local'}: {r.get('where')}), mode {r.get('mode')}, "
          f"codec {p.c.codec}, voice_confirm {r.get('voice_confirm')}, protocol {r.get('protocol')}")
    print(f"limits: {r.get('limits')}")
    await asyncio.sleep(1.0)
    waiting = [e for e in p.events if e["type"] == "notices" and e.get("items")]
    if waiting:
        print(f"waiting notices: {json.dumps(waiting[0]['items'], ensure_ascii=False)[:400]}")
    # a non-hands-free session greets first: let that turn finish before asking
    t0 = time.monotonic()
    while time.monotonic() - t0 < 12 and not any(e["type"] == "turn.done" for e in p.events):
        await asyncio.sleep(0.2)
    greet = "".join(e.get("text", "") for e in p.events if e["type"] == "transcript.out").strip()
    if greet:
        print(f"greeting: {greet[:200]!r}")
    await asyncio.sleep(1.0)
    results = []
    for label, text, voice in turns:
        if p.down.is_set():
            print(f"\nlink went down ({p.why}); stopping")
            break
        res = await p.turn(text, voice=voice, timeout=a.timeout)
        res["label"] = label
        results.append(res)
        show(res, label)
        await asyncio.sleep(1.5)
    await p.c.close()
    task.cancel()
    Path(a.out).parent.mkdir(exist_ok=True)
    Path(a.out).write_text(json.dumps({"when": time.strftime("%Y-%m-%d %H:%M:%S"),
                                       "ready": {k: r.get(k) for k in ("engine", "cloud", "where", "mode", "voice_confirm")},
                                       "results": results}, indent=1, ensure_ascii=False))
    ok = sum(1 for x in results if x["finished"] and not x["errors"])
    print(f"\n{ok} of {len(results)} turns answered; "
          f"{sum(1 for x in results if x['confirm'])} asked for confirmation; "
          f"{sum(1 for x in results if any(not t[2] for t in x['tools']))} had a failed tool. "
          f"Saved {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
