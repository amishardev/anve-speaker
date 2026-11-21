#!/usr/bin/env python3
"""Echo return loss, measured: how much of the speaker's own sound is left in the
microphone signal before and after the echo canceller.

    python3 tools/erle_test.py --speech ref.wav [--tag name] [--settle 3]

It plays `ref.wav` into the echo-cancel sink (so the canceller sees it as reference and
it comes out of the real speaker), and records three things at once:
    raw    the microphone as it is
    clean  the echo-cancelled source
    ref    what was sent to the speaker (the sink's monitor)

The room is not silent, so plain levels would mix echo with room noise. The echo part is
therefore measured as the power in each recording that is COHERENT with the reference
(Welch cross-spectra after aligning for the speaker delay). Echo return loss enhancement
(ERLE) = echo power in raw / echo power in clean. Plain levels are printed as well.
"""
import argparse
import subprocess
import sys
import time
import wave
from pathlib import Path

import numpy as np

SR = 48000


def rd(path):
    w = wave.open(str(path))
    return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float64) / 32768


def db(p):
    return 10 * np.log10(max(float(p), 1e-12))


def lag_of(x, r, max_s=1.0):
    """Samples by which x lags r (band-limited cross-correlation)."""
    n = min(x.size, r.size)
    N = 1 << int(np.ceil(np.log2(2 * n)))
    F = np.fft.rfft(x[:n], N) * np.conj(np.fft.rfft(r[:n], N))
    f = np.fft.rfftfreq(N, 1 / SR)
    F[(f < 300) | (f > 3400)] = 0
    c = np.abs(np.fft.irfft(F, N))
    m = int(max_s * SR)
    k = int(np.argmax(c[:m]))
    return k, float(c[k] / (np.median(c[:m]) + 1e-12))


def coherent_power(x, r, nper=8192):
    """(power of x that is linearly explained by r, total power of x), 300-3400 Hz."""
    n = min(x.size, r.size) // (nper // 2) * (nper // 2)
    win = np.hanning(nper)
    sxx = srr = 0.0
    sxr = 0j
    for i in range(0, n - nper + 1, nper // 2):
        X = np.fft.rfft(x[i:i + nper] * win)
        R = np.fft.rfft(r[i:i + nper] * win)
        sxx = sxx + np.abs(X) ** 2
        srr = srr + np.abs(R) ** 2
        sxr = sxr + X * np.conj(R)
    f = np.fft.rfftfreq(nper, 1 / SR)
    band = (f >= 300) & (f <= 3400)
    coh = np.abs(sxr) ** 2 / (sxx * srr + 1e-30)
    return float(np.sum((coh * sxx)[band])), float(np.sum(sxx[band]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--speech", required=True)
    ap.add_argument("--mic", default="alsa_input.usb-Jieli")
    ap.add_argument("--ec-sink", default="anve_ec_sink")
    ap.add_argument("--ec-source", default="anve_ec_source")
    ap.add_argument("--tag", default="erle")
    ap.add_argument("--settle", type=float, default=3.0, help="seconds of echo skipped while the canceller converges")
    ap.add_argument("--out", default="recordings")
    a = ap.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from anve_device.audio import find_node
    mic = find_node("Audio/Source", a.mic)
    if not mic:
        sys.exit(f"no microphone matching {a.mic!r}")
    out = Path(a.out)
    out.mkdir(exist_ok=True)
    dur = float(subprocess.run(["soxi", "-D", a.speech], capture_output=True, text=True).stdout)
    total = dur + 5.5
    f = {k: out / f"{a.tag}_{k}.wav" for k in ("raw", "clean", "ref")}
    rec = lambda target, path, extra=(): subprocess.Popen(      # noqa: E731
        ["timeout", f"{total}", "pw-record", "--target", target, *extra, "--rate", str(SR),
         "--channels", "1", "--format", "s16", str(path)], stderr=subprocess.DEVNULL)
    procs = [rec(mic, f["raw"]), rec(a.ec_source, f["clean"]),
             rec(a.ec_sink, f["ref"], ("-P", "{ stream.capture.sink = true }"))]
    time.sleep(3.0)                                  # room noise only
    subprocess.run(["pw-play", "--target", a.ec_sink, a.speech], check=False)
    for p in procs:
        p.wait()
    raw, clean, ref = (rd(f[k]) for k in ("raw", "clean", "ref"))
    on = np.where(np.abs(ref) > 0.01)[0]
    if on.size < SR:
        sys.exit("the reference recording is silent: nothing reached the echo-cancel sink")
    s0, s1 = int(on[0]), int(on[-1])
    lag_raw, q_raw = lag_of(raw[s0:s1], ref[s0:s1])
    lag_cl, q_cl = lag_of(clean[s0:s1], ref[s0:s1])
    print(f"reference speech: {dur:.1f} s, level {db(np.mean(ref[s0:s1] ** 2)):.1f} dBFS")
    print(f"speaker-to-mic delay: {lag_raw / SR * 1000:.0f} ms (correlation {q_raw:.0f}x median)")
    b = slice(int(0.5 * SR), max(int(0.5 * SR) + 1, s0 - int(0.2 * SR)))
    print(f"room noise before playback   raw {db(np.mean(raw[b] ** 2)):6.1f} dBFS   "
          f"clean {db(np.mean(clean[b] ** 2)):6.1f} dBFS")
    for name, skip in (("whole reply", 0.0), (f"after {a.settle:.0f} s", a.settle)):
        t0 = s0 + int(skip * SR)
        r = ref[t0:s1]
        x_raw = raw[t0 + lag_raw:s1 + lag_raw]
        x_cl = clean[t0 + lag_cl:s1 + lag_cl]
        m = min(r.size, x_raw.size, x_cl.size)
        e_raw, p_raw = coherent_power(x_raw[:m], r[:m])
        e_cl, p_cl = coherent_power(x_cl[:m], r[:m])
        k = np.sum(np.hanning(8192) ** 2) * (m // 4096) / 2 * 4096 / 1  # scale to mean-square
        print(f"{name:14s} level while speaking: raw {db(np.mean(x_raw[:m] ** 2)):6.1f} dBFS, "
              f"clean {db(np.mean(x_cl[:m] ** 2)):6.1f} dBFS  |  echo share of raw "
              f"{100 * e_raw / p_raw:3.0f} %, of clean {100 * e_cl / p_cl:3.0f} %  |  "
              f"ERLE {db(e_raw) - db(e_cl):5.1f} dB")
    print(f"files: {', '.join(str(p) for p in f.values())}")


if __name__ == "__main__":
    main()
