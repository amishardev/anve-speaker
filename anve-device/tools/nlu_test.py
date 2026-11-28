#!/usr/bin/env python3
"""What the speaker makes of everyday sentences (anve_device/nlu.py). No speaker needed.

    python tools/nlu_test.py          prints every sentence that came out wrong; exit 1 if any
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from anve_device.nlu import understand  # noqa: E402

P = {"playing": True}                    # music is playing
Z = {"paused": True}                     # music is paused
N = {}                                   # nothing is on
def after(last, age=10, **kw):
    return {"last": last, "age": age, **kw}
UP, DOWN = {"do": "volume", "step": 0.1}, {"do": "volume", "step": -0.1}

CASES = [
    # --- volume, said plainly ---
    ("Set the volume to 30 percent.", N, {"do": "volume", "level": 0.3}),
    ("volume 50", N, {"do": "volume", "level": 0.5}),
    ("Hey Anvi, set volume at eighty percent", N, {"do": "volume", "level": 0.8}),
    ("make the volume sixty", N, {"do": "volume", "level": 0.6}),
    ("volume thirty five", N, {"do": "volume", "level": 0.35}),
    ("full volume", N, {"do": "volume", "level": 1.0}),
    ("volume to half", N, {"do": "volume", "level": 0.5}),
    ("Turn the volume up.", N, UP),
    ("volume down please", N, DOWN),
    ("can you increase the volume", N, UP),
    ("lower the volume a bit", N, {"do": "volume", "step": -0.05}),
    ("increase volume by 20", N, {"do": "volume", "step": 0.2}),
    ("raise the volume a lot", N, {"do": "volume", "step": 0.2}),
    # --- volume, Hinglish and Hindi ---
    ("awaaz kam karo", N, DOWN),
    ("volume kam kar do", N, DOWN),
    ("awaaz badhao", N, UP),
    ("thoda awaaz badha do", N, {"do": "volume", "step": 0.05}),
    ("volume bahut kam karo", N, {"do": "volume", "step": -0.2}),
    ("awaaz zyada karo", N, UP),
    ("volume pachas kar do", N, {"do": "volume", "level": 0.5}),
    ("volume poora kar do", N, {"do": "volume", "level": 1.0}),
    ("आवाज़ कम करो", N, DOWN),
    ("वॉल्यूम बढ़ाओ", N, UP),
    ("awaaz dheere karo", N, DOWN),
    ("awaaz tez karo", N, UP),
    # --- volume, by what is wrong ---
    ("it's too loud", N, {"do": "volume", "step": -0.15}),
    ("bahut tez hai", N, {"do": "volume", "step": -0.15}),
    ("I can't hear it", N, {"do": "volume", "step": 0.15}),
    ("sunai nahi de raha", N, {"do": "volume", "step": 0.15}),
    ("it is too quiet", N, {"do": "volume", "step": 0.15}),
    # --- volume without the word, only with music on or just after a change ---
    ("louder", P, UP),
    ("kam karo", P, DOWN),
    ("thoda kam karo", P, {"do": "volume", "step": -0.05}),
    ("turn it up", P, UP),
    ("turn it down a little", P, {"do": "volume", "step": -0.05}),
    ("kam karo", N, None),
    ("badhao", after(DOWN), UP),
    # --- context: about the last action ---
    ("thoda aur", after(DOWN), {"do": "volume", "step": -0.05}),
    ("aur", after(UP), UP),
    ("more", after(UP), UP),
    ("again", after(DOWN), DOWN),
    ("a bit more", after(UP), {"do": "volume", "step": 0.05}),
    ("too much", after(UP), {"do": "volume", "step": -0.05}),
    ("zyada ho gaya", after(DOWN), {"do": "volume", "step": 0.05}),
    ("more", after(UP, age=600), None),
    ("ek aur", after({"do": "next"}, playing=True), {"do": "next"}),
    # --- asking ---
    ("what's the volume", N, {"do": "volume_query"}),
    ("volume kitna hai", N, {"do": "volume_query"}),
    ("What time is it?", N, {"do": "time"}),
    ("time kya hai", N, {"do": "time"}),
    ("kitne baje hain", N, {"do": "time"}),
    ("Can you just, just a second. What's the time?", N, {"do": "time"}),
    ("which song is this", P, {"do": "nowplaying"}),
    ("ye kaunsa gaana hai", P, {"do": "nowplaying"}),
    ("what's playing", P, {"do": "nowplaying"}),
    # --- stopping the music ---
    ("stop", P, {"do": "pause"}),
    ("Stop the music.", P, {"do": "pause"}),
    ("hey anve stop the music", P, {"do": "pause"}),
    ("gaana band karo", P, {"do": "pause"}),
    ("band karo", P, {"do": "pause"}),
    ("ise band kar do", P, {"do": "pause"}),
    ("music roko", P, {"do": "pause"}),
    ("bas karo", P, {"do": "pause"}),
    ("chup", P, {"do": "pause"}),
    ("pause it", P, {"do": "pause"}),
    ("turn it off", P, {"do": "pause"}),
    ("गाना बंद करो", P, {"do": "pause"}),
    ("shut up", P, {"do": "pause"}),
    ("stop", N, None),
    ("stop recording", P, None),
    ("stop the timer", P, None),
    ("band karo ye meeting", P, None),
    # --- going on, the next one ---
    ("resume", Z, {"do": "resume"}),
    ("continue the music", Z, {"do": "resume"}),
    ("wapas chalao", Z, {"do": "resume"}),
    ("chalu karo", Z, {"do": "resume"}),
    ("play", Z, {"do": "resume"}),
    ("next", P, {"do": "next"}),
    ("skip this song", P, {"do": "next"}),
    ("agla gaana", P, {"do": "next"}),
    ("agla lagao", P, {"do": "next"}),
    ("ye nahi, doosra lagao", P, {"do": "next"}),
    ("change the song", P, {"do": "next"}),
    ("mujhe ye pasand nahi", P, {"do": "next"}),
    # --- playing something ---
    ("Play Let It Happen by Tame Impala.", N, {"do": "youtube", "query": "let it happen by tame impala"}),
    ("play let it happen by tame impala", P, {"do": "youtube", "query": "let it happen by tame impala"}),
    ("Play play let it happen", N, {"do": "youtube", "query": "let it happen"}),
    ("Can you play some of like weightless by Martin Garrix?", N, {"do": "youtube", "query": "weightless by martin garrix"}),
    ("can you play new delhi by bloodywood", N, {"do": "youtube", "query": "new delhi by bloodywood"}),
    ("hey anve play god by diljit dosanjh on youtube", N, {"do": "youtube", "query": "god by diljit dosanjh"}),
    ("arijit singh ke gaane bajao", N, {"do": "youtube", "query": "arijit singh"}),
    ("kesariya chalao", N, {"do": "youtube", "query": "kesariya"}),
    ("tum hi ho laga do", N, {"do": "youtube", "query": "tum hi ho"}),
    ("kuch gaane bajao", N, {"do": "radio", "station": "default"}),
    ("play some music", N, {"do": "radio", "station": "default"}),
    ("gaana bajao", N, {"do": "radio", "station": "default"}),
    ("play jazz", N, {"do": "radio", "station": "jazz"}),
    ("put on some lofi music", N, {"do": "radio", "station": "lofi"}),
    ("I want to hear Blinding Lights", N, {"do": "youtube", "query": "blinding lights"}),
    # --- who made the speaker (only when asked) ---
    ("Who made you?", N, {"do": "maker"}),
    ("hey anve who built you", N, {"do": "maker"}),
    ("who created this speaker", P, {"do": "maker"}),
    ("Who is your creator?", N, {"do": "maker"}),
    ("who developed you", N, {"do": "maker"}),
    ("tumhe kisne banaya", N, {"do": "maker"}),
    ("aapko kisne banaya hai", N, {"do": "maker"}),
    ("तुम्हें किसने बनाया", N, {"do": "maker"}),
    ("kisne banaya", N, {"do": "maker"}),
    ("Who made the Taj Mahal?", N, None),
    ("who made this song", P, None),
    ("who is the creator of Python", N, None),
    ("what can you do", N, None),
    ("who are you", N, None),
    # --- not for the device: ANVE answers ---
    ("What is the volume of a sphere?", N, None),
    ("Set a timer for five minutes.", N, None),
    ("What time is my meeting tomorrow?", N, None),
    ("What time is it in London?", N, None),
    ("play the recording of yesterday's meeting", N, None),
    ("Who is the prime minister?", P, None),
    ("how much revenue did we make", P, None),
    ("send an email to Rahul", P, None),
    ("remind me to call the bank", P, None),
    ("what's the weather today", P, None),
    ("don't reduce the volume", P, None),
    ("awaaz kam mat karo", P, None),
    ("bas itna hi chahiye", N, None),
    ("is the report ready", P, None),
]


def main():
    bad = 0
    for text, ctx, want in CASES:
        got = understand(text, ctx)
        ok = got == want or (got and want and got.get("do") == want.get("do") == "volume"
                             and all(abs(got.get(k, 0) - want.get(k, 0)) < 1e-9 for k in ("step", "level")))
        if not ok:
            bad += 1
            print(f"WRONG  {text!r}  ({'music on' if ctx.get('playing') else 'paused' if ctx.get('paused') else 'nothing on'})"
                  f"\n       got  {got}\n       want {want}")
    print(f"{len(CASES) - bad} of {len(CASES)} sentences understood as intended")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
