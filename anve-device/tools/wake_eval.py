#!/usr/bin/env python3
"""Measure the wake-word spotter on a real voice, in the real room.

    .venv/bin/python tools/wake_eval.py --seconds 120 [--source UACDemo] [--leds]

It listens to the microphone, prints every speech segment with what Whisper heard and
whether it counted as a wake, and at the end prints the totals. With --leds the strip
flashes green on a wake and shows a dim blue while a segment is being heard, so the
test works without a speaker.

Two runs give the two numbers that matter:
  * say "Hey ANVE" N times, a few seconds apart  -> misses = N - wakes
  * talk about anything else / leave the room noise on -> every wake is a false accept
"""
import argparse
import asyncio
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from anve_device.audio import Capture  # noqa: E402
from anve_device.wake import Segmenter, Spotter  # noqa: E402


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--source", default="UACDemo")
    ap.add_argument("--model", default="tiny.en")
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--early", type=float, default=1.5)
    ap.add_argument("--leds", action="store_true")
    ap.add_argument("--count", type=int, default=30)
    a = ap.parse_args()

    strip = None
    if a.leds:
        from anve_device.led import Strip
        strip = Strip(a.count, 0.35)
    spot = Spotter(a.model, a.threads)
    t0 = time.monotonic()
    spot.load()
    print(f"model {a.model} loaded in {time.monotonic() - t0:.1f} s, {a.threads} threads",
          flush=True)
    seg = Segmenter(early_s=a.early)
    pool = ThreadPoolExecutor(1)
    loop = asyncio.get_running_loop()
    cap = Capture(a.source)
    stats = {"segments": 0, "wakes": 0, "early_wakes": 0, "barges": 0, "ms": [], "levels": []}
    woke_segment = {"start": None}
    pending = []

    async def decode(kind, pcm, start, t_seg_end):
        d = await loop.run_in_executor(pool, spot.detect, pcm)
        stats["ms"].append(d["ms"])
        dur = len(pcm) / 32000
        late = time.monotonic() - t_seg_end
        tag = ""
        if d["wake"] and woke_segment["start"] != start:
            woke_segment["start"] = start
            stats["wakes"] += 1
            stats["early_wakes"] += kind == "early"
            tag = "  <== WAKE"
            if strip:
                strip.fill((0, 255, 0))
                loop.call_later(0.6, strip.off)
        elif d["wake"]:
            tag = "  (same wake, final pass)"
        elif d["vetoed"]:
            tag = "  (vetoed: another name)"
        if d["barge"]:
            stats["barges"] += 1
            tag += "  [barge word]"
        print(f"{time.strftime('%H:%M:%S')} {kind:5s} {dur:4.1f}s decode {d['ms']:4d} ms "
              f"known {late:4.1f}s after it was cut  conf {d['score']:.2f}  "
              f"{d['text'][:70]!r}{tag}", flush=True)

    print(f"listening for {a.seconds:.0f} s on '{a.source}' ...", flush=True)
    index, end_at = 0, time.monotonic() + a.seconds
    try:
        async for frame in cap.frames():
            if time.monotonic() > end_at:
                break
            for kind, pcm, start in seg.feed(frame, index):
                if kind == "final":
                    stats["segments"] += 1
                    if woke_segment["start"] == start:
                        continue                  # already a wake from the early pass
                pending.append(asyncio.create_task(decode(kind, pcm, start, time.monotonic())))
            index += len(frame) // 2
            if index % (16000 * 5) < 640:
                stats["levels"].append(round(seg.vad.prob, 2))
    finally:
        await cap.close()
        if pending:
            await asyncio.wait(pending, timeout=15)
        if strip:
            strip.close()
    ms = sorted(stats["ms"]) or [0]
    print(f"\n--- {a.seconds:.0f} s, model {a.model} ---")
    print(f"speech segments: {stats['segments']}   wakes: {stats['wakes']} "
          f"(of which known before the speaker paused: {stats['early_wakes']})   "
          f"barge words: {stats['barges']}")
    print(f"decode time: median {ms[len(ms) // 2]} ms, worst {ms[-1]} ms over {len(stats['ms'])} decodes")
    print(f"speech probability sampled every 5 s: {stats['levels']}")


if __name__ == "__main__":
    asyncio.run(main())
