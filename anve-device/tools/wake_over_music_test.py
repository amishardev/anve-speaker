#!/usr/bin/env python3
"""Can the speaker hear "Hey ANVE" while its own music is playing?

    .venv/bin/python tools/wake_over_music_test.py [--volumes 0.6,0.3] [--says 5]

Needs the speaker service running (it owns the echo canceller). For each music volume it
plays a free radio stream through the echo-cancel sink, has a synthetic voice say the
wake phrase through the real speaker (bypassing the echo reference, so it counts as a
person in the room), records the cleaned microphone, and then runs the same rolling
check the service uses over music. It prints what each window was heard as.
The synthetic voice is only a stand-in: confirm with a real voice.
"""
import argparse
import asyncio
import subprocess
import sys
import time
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from anve_device.audio import find_node  # noqa: E402
from anve_device.dsp import Agc  # noqa: E402
from anve_device.skills import RADIO  # noqa: E402
from anve_device.wake import SileroVAD, Spotter  # noqa: E402


def sh(cmd):
    return subprocess.Popen(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--volumes", default="0.6,0.3")
    ap.add_argument("--says", type=int, default=5)
    ap.add_argument("--phrase", default="Hey Anvee.")
    ap.add_argument("--station", default="default", help="default (instrumental), indie (singing), jazz, lofi, classical")
    a = ap.parse_args()
    spk = find_node("Audio/Sink", "bluez_output") or find_node("Audio/Sink", "SoundDrum")
    if not find_node("Audio/Source", "anve_ec_source") or not spk:
        sys.exit("the echo canceller or the speaker is missing: is the service running?")
    subprocess.run(f"espeak-ng -v en-us -s 140 -w /tmp/_w.wav {a.phrase!r} && "
                   "sox /tmp/_w.wav -r 48000 -c 2 /tmp/_w48.wav norm -3", shell=True, check=True)
    spot = Spotter("tiny.en", 2)
    spot.load()
    url = RADIO[a.station][1]
    for vol in [float(v) for v in a.volumes.split(",")]:
        out = f"/tmp/_wom_{vol}.wav"
        radio = sh(f"ffmpeg -nostdin -loglevel error -i {url!r} -vn -f s16le -ar 48000 -ac 2 - | "
                   f"pw-play --raw --target anve_ec_sink --rate 48000 --channels 2 --format s16 --volume {vol} -")
        time.sleep(6)                                    # let the stream start and the canceller settle
        dur = 4 + a.says * 5
        rec = subprocess.Popen(["timeout", str(dur), "pw-record", "--target", "anve_ec_source", "--rate", "16000",
                                "--channels", "1", "--format", "s16", out], stderr=subprocess.DEVNULL)
        t0, said = time.time(), []
        time.sleep(3)
        for _ in range(a.says):
            said.append(time.time() - t0)
            subprocess.run(["pw-play", "--target", spk, "/tmp/_w48.wav"])
            time.sleep(4)
        rec.wait()
        subprocess.run(f"pkill -g {radio.pid}", shell=True)
        radio.terminate()
        subprocess.run("pkill -f 'pw-play --raw --target anve_ec_sink --rate 4800[0]'", shell=True)
        w = wave.open(out)
        b = w.readframes(w.getnframes())
        x = np.frombuffer(b, dtype=np.int16).astype(np.float32) / 32768
        lv = 20 * np.log10(np.sqrt(np.mean(x[:16000 * 2] ** 2)) + 1e-6)
        vad, agc, win, hits, n, speech = SileroVAD(), Agc(), [], [], 0, 0
        print(f"\n=== music at stream volume {vol}: cleaned mic level with music only {lv:.0f} dBFS; "
              f"wake phrase said at " + ", ".join(f"{s:.0f}s" for s in said) + " ===")
        for i in range(0, len(b) - 1279, 1280):
            p = vad.feed(b[i:i + 1280])
            speech += p >= 0.5
            agc.process(b[i:i + 1280], p >= 0.5, strict=True)
            win.append(agc.ungated)
            win = win[-63:]
            n += 1
            if n % 37 == 0 and len(win) >= 50:
                d = spot.detect(b"".join(win), True)
                t = i / 32000
                if d["wake"]:
                    hits.append(t)
                print(f"  window ending {t:5.1f}s  {'WAKE ' if d['wake'] else '     '} {d['ms']:4d} ms  {d['text'][:70]!r}")
        caught = sum(any(s <= h <= s + 4.5 for h in hits) for s in said)
        print(f"  -> caught {caught} of {len(said)} wake phrases; the detector called {100 * speech / max(1, n):.0f} % "
              f"of the music 'speech'")
        time.sleep(1)


if __name__ == "__main__":
    main()
