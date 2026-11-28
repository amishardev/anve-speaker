#!/usr/bin/env python3
"""Teach the speaker its owner's "Hey ANVE" (see wake.Enrolled), and check the result.

    .venv/bin/python tools/wake_enroll.py recordings/pos_23nov
        Every .wav in that folder is one recording of the owner saying the phrase (kept
        clips from tools/wake_diag.sh, sorted by hand). Writes recordings/enrol/templates.npz.
        Restart the speaker afterwards.

    .venv/bin/python tools/wake_enroll.py recordings/pos_23nov --test other1 other2.wav ...
        Writes nothing. Each recording is matched against all the OTHERS (so a clip never
        vouches for itself), and every clip in the other folders / files, which must NOT
        contain the phrase, is matched against all of them. Long files are cut into phrases
        the way the live chain does. Prints the scores and what the limits would do.

The recordings and the file made from them stay on this device (recordings/ is not in git).
"""
import argparse
import sys
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from anve_device import config  # noqa: E402
from anve_device.wake import FRAME_BYTES, Enrolled, Segmenter, Spotter  # noqa: E402


def load(path: Path) -> bytes:
    if path.stat().st_size < 1000:
        return b""
    with wave.open(str(path)) as w:
        b = w.readframes(w.getnframes())
        if w.getnchannels() > 1:
            b = np.frombuffer(b, np.int16).reshape(-1, w.getnchannels())[:, 0].tobytes()
    return b


def clips(path: Path):
    """(name, pcm) for a clip, or for each phrase in a long recording."""
    b = load(path)
    if len(b) <= 3.2 * 32000:
        if len(b) >= 22400:
            yield path.name, b
        return
    seg, n = Segmenter(early_s=1.7), 0
    for i in range(0, len(b) - FRAME_BYTES, FRAME_BYTES):
        for kind, pcm, _ in seg.feed(b[i:i + FRAME_BYTES], i // 2):
            if len(pcm) >= 22400:
                yield f"{path.name}#{n}{kind[0]}", pcm
                n += 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--test", nargs="*", default=None, metavar="NOT_THE_PHRASE")
    ap.add_argument("--stop", action="store_true",
                    help='the folder holds "ANVE stop" recordings: write the stop file instead')
    a = ap.parse_args()
    w = config.load()["wake"]
    spot = Spotter(w["model"], w["threads"])
    files = sorted(Path(a.folder).glob("*.wav"))
    embs = [(f.name, spot.embed(b)) for f in files if len(b := load(f)) >= 22400]
    if len(embs) < 5:
        sys.exit(f"{len(embs)} usable recordings in {a.folder}: at least 5 are needed")
    if a.test is None:
        out = ROOT / (w["stop_file"] if a.stop else w["enrol_file"])
        out.parent.mkdir(parents=True, exist_ok=True)
        np.savez(out, **{f"t{i:02d}": e.astype(np.float32) for i, (_n, e) in enumerate(embs)})
        print(f"{len(embs)} recordings -> {out}")
        return
    lim = Enrolled("", w["enrol_whole"], w["enrol_prefix"])
    print(f"limits: whole {lim.whole}, prefix {lim.prefix}\nthe phrase, each against the others:")
    hits = 0
    for i, (name, e) in enumerate(embs):
        lim.templates = [x for j, (_n, x) in enumerate(embs) if j != i]
        m = lim.match(e)
        hits += m["hit"]
        print(f"  {'HIT ' if m['hit'] else 'miss'} {m['whole']:.3f} {m['prefix']:.3f}  {name}")
    print(f"  {hits} of {len(embs)} recognised")
    lim.templates = [e for _n, e in embs]
    rows = []
    for t in a.test:
        p = Path(t)
        for f in (sorted(p.glob("*.wav")) if p.is_dir() else [p]):
            for name, pcm in clips(f):
                m = lim.match(spot.embed(pcm))
                rows.append((min(m["whole"], m["prefix"] + lim.whole - lim.prefix), m, name))
    rows.sort(key=lambda r: r[0])
    false = [r for r in rows if r[1]["hit"]]
    print(f"not the phrase: {len(rows)} clips, {len(false)} would have woken it. The nearest:")
    for _s, m, name in rows[:8]:
        print(f"  {'WAKE' if m['hit'] else '  - '} {m['whole']:.3f} {m['prefix']:.3f}  {name}")


if __name__ == "__main__":
    main()
