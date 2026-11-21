#!/usr/bin/env python3
"""Replay recordings through the wake-word chain and show what happens to each phrase.

    .venv/bin/python tools/wake_bench.py recordings/proof_1m_clean.wav [more.wav ...]
        [--model tiny.en] [--vad 0.5] [--normalise] [--raw] [--texts]

For every speech segment the chain cuts out, it prints when it started, how long and how
loud it was, what the recogniser heard and whether that counted as "Hey ANVE". Changing
the options shows what a change would have done to the SAME audio, which is the only fair
way to compare two settings.

  --raw        the recording is straight from the microphone: run the gain stage first
               (the live chain does; "clean" recordings already went through it)
  --live drop  honour time the way the speaker did until 19 Nov: while one decode runs,
               whatever else arrives is thrown away
  --live wait  honour time the way it does now: the newest arrival waits for its turn
               (without --live every segment is decoded, as if decoding took no time)
  --scale 0.8  how long a decode takes live, compared with here (the bench runs on one
               thread next to the running speaker; live it has two)
  --texts      also print what the second, unbiased decode heard (the veto)
"""
import argparse
import sys
import time
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from anve_device import wake  # noqa: E402
from anve_device.dsp import Agc  # noqa: E402
from anve_device.wake import FRAME, RATE, Segmenter, SileroVAD, Spotter  # noqa: E402


def load(path: str) -> bytes:
    with wave.open(path) as w:
        a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        if w.getnchannels() > 1:
            a = a.reshape(-1, w.getnchannels())[:, 0]
        if w.getframerate() != RATE:
            n = int(len(a) * RATE / w.getframerate())
            a = np.interp(np.linspace(0, len(a) - 1, n), np.arange(len(a)), a.astype(np.float32)).astype(np.int16)
    return a.tobytes()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--model", default="tiny.en")
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--early", type=float, default=1.7)
    ap.add_argument("--raw", action="store_true")
    ap.add_argument("--texts", action="store_true")
    ap.add_argument("--finals", action="store_true", help="decode whole segments only (skip the early pass)")
    ap.add_argument("--live", choices=("drop", "wait"), default="")
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--tokens", type=int, default=24)
    a = ap.parse_args()

    spotter = Spotter(a.model, a.threads, tokens=a.tokens)
    spotter.load()
    total = wakes = dropped = waited = 0
    spent = []
    for path in a.files:
        pcm = load(path)
        vad, seg, agc = SileroVAD(), Segmenter(early_s=a.early, own_vad=False), Agc()
        gate = wake.Sensitivity() if hasattr(wake, "Sensitivity") else None
        print(f"\n== {Path(path).name}  ({len(pcm) / 2 / RATE:.1f} s)")
        woke_at = -10.0
        busy_until, waiting = 0.0, None

        def decode(kind, clip, start, late=0.0):
            nonlocal total, wakes, woke_at
            t = start / RATE
            x = np.frombuffer(clip, dtype=np.int16).astype(np.float32) / 32768
            level = 20 * np.log10(max(float(np.sqrt(np.mean(x * x))), 1e-6))
            t0 = time.monotonic()
            d = spotter.detect(clip)
            took = time.monotonic() - t0
            spent.append(took)
            total += 1
            mark = "WAKE " if d["wake"] else "veto " if d["vetoed"] else "     "
            if d["wake"] and t - woke_at > 2.0:
                wakes += 1
                woke_at = t
            line = (f"  {t:6.1f}s {kind:5s} {len(clip) / 2 / RATE:4.1f}s {level:5.0f} dB  {mark} {d['ms']:5d} ms  "
                    f"{d['text'][:70]!r}" + (f"   (waited {late:.1f} s)" if late > 0.05 else ""))
            if a.texts:
                plain = " ".join(w for w, _s, _e in spotter.words(clip, hotwords=None, short=True)[0])
                line += f"   | unbiased: {plain[:50]!r}"
            print(line)
            return took * a.scale

        for i in range(0, len(pcm) - FRAME * 2 + 1, FRAME * 2):
            now = (i + FRAME * 2) / 2 / RATE            # the moment this frame has been heard
            if waiting and now >= busy_until:
                kind, clip, start, since = waiting
                waiting = None
                busy_until = now + decode(kind, clip, start, late=now - since)
            frame = pcm[i:i + FRAME * 2]
            prob = vad.feed(frame)
            if a.raw:
                agc.process(frame, prob >= 0.5, gate_open=prob >= 0.15)
                heard = agc.ungated
            else:
                heard = frame
            speech = gate.feed(frame, prob) if gate else prob >= 0.5
            for kind, clip, start in seg.feed(heard, i // 2, speech):
                if a.finals and kind == "early":
                    continue
                if a.live and now < busy_until:
                    if a.live == "drop":
                        dropped += 1
                        print(f"  {start / RATE:6.1f}s {kind:5s} {len(clip) / 2 / RATE:4.1f}s            DROPPED: the recogniser was still busy")
                    else:
                        if waiting:
                            dropped += 1               # the newer one takes the waiting place
                        waiting = (kind, clip, start, now)
                        waited += 1
                    continue
                took = decode(kind, clip, start)
                if a.live:
                    busy_until = now + took
        if waiting:
            decode(*waiting[:3], late=max(0.0, busy_until - waiting[3]))
    print(f"\n{wakes} wakes in {total} decodes ({dropped} dropped, {waited} waited); decode time median "
          f"{np.median(spent) * 1000:.0f} ms, longest {max(spent) * 1000:.0f} ms" if spent else "\nno speech segments found")


if __name__ == "__main__":
    main()
