#!/usr/bin/env python3
"""Checks for the speaker's own skills (anve_device/skills.py).

    .venv/bin/python tools/skills_test.py            the sentence parser (no sound, no network)
    .venv/bin/python tools/skills_test.py --live "lofi beats"
                                                     really open the browser and play it
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from anve_device import skills  # noqa: E402

CASES = [
    ("Play Arijit Singh songs on YouTube.", ("youtube", "arijit singh songs")),
    ("Hey ANVE, can you play some lofi beats", ("youtube", "lofi beats")),
    ("Can you play some of the Nishad songs on any platform?", ("youtube", "of the nishad songs")),
    ("play despacito", ("youtube", "despacito")),
    ("Play some music.", ("radio", "default")),
    ("play radio", ("radio", "default")),
    ("Play jazz", ("radio", "jazz")),
    ("play jazz on youtube", ("youtube", "jazz")),
    ("put on some music on youtube", ("youtube", "music mix")),
    ("Stop the music.", ("pause", None)),
    ("pause", ("pause", None)),
    ("Resume the music", ("resume", None)),
    ("next song", ("next", None)),
    ("skip this one", ("next", None)),
    ("volume up", ("volume", 0.1)),
    ("make it quieter", ("volume", -0.1)),
    ("Open YouTube", ("open", "https://www.youtube.com")),
    ("open wikipedia.org in the browser", ("open", "https://wikipedia.org")),
    ("search for raspberry pi 5 cooling in the browser", ("search", "raspberry pi 5 cooling")),
    # these must go to ANVE, untouched
    ("What is the weather in Sambalpur tomorrow?", None),
    ("How many followers does Nicky Romero have?", None),
    ("Remind me to play cricket tomorrow.", None),
    ("Record this meeting.", None),
    ("Stop recording and make the minutes.", None),
    ("Open the leave application document.", None),
    ("What does our leave policy say?", None),
    ("Set a timer for five minutes.", None),
]


def parser_check() -> int:
    bad = 0
    for text, want in CASES:
        got = skills.parse(text)
        key = None if got is None else (
            got["do"], got.get("query") or got.get("station") or got.get("url") or got.get("step"))
        ok = key == want
        bad += not ok
        print(f"{'ok  ' if ok else 'FAIL'} {text!r:62s} -> {key}" + ("" if ok else f"   expected {want}"))
    print(f"\n{len(CASES) - bad} of {len(CASES)} sentences understood as intended")
    return bad


async def live(query: str):
    m = skills.Media()
    print("opening the browser and searching YouTube for", repr(query))
    say = await m.run({"do": "youtube", "query": query})
    print("playing:", repr(m.browser.title), "| said:", repr(say))
    await asyncio.sleep(12)
    state = await m.browser.js("(() => { const v=document.querySelector('video'); return v ? "
                               "{paused: v.paused, t: Math.round(v.currentTime), h: v.videoHeight} : null })()")
    print("video state after 12 s:", state)
    await m.pause()
    await asyncio.sleep(1)
    print("after pause:", await m.browser.js("document.querySelector('video')?.paused"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--live")
    a = ap.parse_args()
    if a.live:
        asyncio.run(live(a.live))
    else:
        sys.exit(1 if parser_check() else 0)
