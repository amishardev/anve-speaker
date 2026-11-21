#!/usr/bin/env python3
"""The audio-quality proof: you read a fixed script from 1 m and then from 3 m while the
speaker talks over you, and this prints raw vs cleaned levels and a transcript of each.

    .venv/bin/python tools/proof_record.py            both distances, about 90 s in all
    .venv/bin/python tools/proof_record.py --script   just print the script to read

The speaker tells you what to do. Each take is 32 s: you start reading at the beep; the
assistant's voice (a recorded sample) plays from second 6 to about second 25, so every
take has a part with you alone and a part with both of you.

Recorded at once, per take (16 kHz mono, in recordings/):
    proof_<d>_raw.wav     the microphone as it is
    proof_<d>_clean.wav   after the echo canceller, high-pass and noise suppression
    proof_<d>_sent.wav    what would be sent to ANVE (clean + gain control)
Then each is transcribed on the Pi (Whisper base.en) and scored against the script.
"""
import argparse
import asyncio
import re
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
from anve_device.playback import EchoCancel  # noqa: E402
from anve_device.wake import SileroVAD  # noqa: E402

SCRIPT = ("The quarterly review is on Thursday at three in the afternoon. Please book the "
          "small meeting room and invite the finance team. We need last month's sales "
          "figures, the list of overdue invoices, and the updated budget. Remind me one "
          "hour before the meeting. After that, send a short summary to everyone who "
          "attended, and set a follow up for next Monday morning at ten.")
TAKE_S, SPEECH_AT = 32, 6.0


def sh(cmd: str):
    subprocess.run(cmd, shell=True, check=False, stderr=subprocess.DEVNULL)


def say(text: str, sink: str):
    sh(f"espeak-ng -v en-gb -s 150 -w /tmp/_p.wav {text!r} && "
       f"sox /tmp/_p.wav -r 48000 -c 2 /tmp/_p48.wav norm -6 && pw-play --target {sink} /tmp/_p48.wav")


def beep(sink: str):
    sh("sox -n -r 48000 -c 2 /tmp/_b.wav synth 0.25 sine 880 fade q 0.02 0.25 0.05 gain -14 pad 0.2 0.1 && "
       f"pw-play --target {sink} /tmp/_b.wav")


def rd(path) -> np.ndarray:
    w = wave.open(str(path))
    return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def db(x: np.ndarray) -> float:
    x = x.astype(np.float64) / 32768
    return 10 * np.log10(np.mean(x * x) + 1e-12) if x.size else -120.0


def words(t: str) -> list[str]:
    return re.sub(r"[^a-z0-9' ]+", " ", t.lower()).split()


def wer(ref: list[str], hyp: list[str]) -> float:
    d = np.arange(len(hyp) + 1)
    for i, r in enumerate(ref, 1):
        prev, d[0] = d[0], i
        for j, h in enumerate(hyp, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (r != h))
    return d[-1] / max(1, len(ref))


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", action="store_true")
    ap.add_argument("--distances", default="1m,3m")
    ap.add_argument("--speech", default=str(ROOT / "recordings" / "ref_speech.wav"))
    ap.add_argument("--mic", default="alsa_input.usb-Jieli")
    ap.add_argument("--speaker", default="SoundDrum")
    ap.add_argument("--model", default="base.en")
    ap.add_argument("--analyse-only", action="store_true", help="re-score existing takes")
    a = ap.parse_args()
    if a.script:
        print(SCRIPT)
        return
    out = ROOT / "recordings"
    out.mkdir(exist_ok=True)
    dists = [d.strip() for d in a.distances.split(",") if d.strip()]
    ec = task = None
    if not a.analyse_only:
        if not find_node("Audio/Source", EchoCancel.SOURCE):       # the service is not running
            ec = EchoCancel("UACDemo", a.speaker)
            task = asyncio.create_task(ec.run())
            try:
                await asyncio.wait_for(ec.ready.wait(), 25)
            except asyncio.TimeoutError:
                task.cancel()
                await ec.close()
                sys.exit("The echo canceller could not start: the microphone or the speaker "
                         "is missing. Is the Bluetooth speaker switched on and connected?")
            await asyncio.sleep(2)
        mic = find_node("Audio/Source", a.mic)
        if not mic:
            sys.exit(f"no microphone matching {a.mic!r}")
        sink = EchoCancel.SINK
        for d in dists:
            spoken = {"1m": "one metre", "3m": "three metres"}.get(d, d)
            say(f"Stand {spoken} from the microphone. After the beep, read the script aloud "
                "at a normal pace, and keep reading while I talk.", sink)
            await asyncio.sleep(1.0)
            recs = [subprocess.Popen(
                ["timeout", str(TAKE_S + 1), "pw-record", "--target", tg, "--rate", "16000",
                 "--channels", "1", "--format", "s16", str(out / f"proof_{d}_{k}.wav")],
                stderr=subprocess.DEVNULL) for k, tg in (("raw", mic), ("clean", EchoCancel.SOURCE))]
            await asyncio.sleep(0.6)
            await asyncio.to_thread(beep, sink)
            t0 = time.monotonic()
            await asyncio.sleep(max(0.0, SPEECH_AT - 1.0))
            await asyncio.to_thread(sh, f"pw-play --target {sink} {a.speech}")
            await asyncio.sleep(max(0.0, TAKE_S - (time.monotonic() - t0)))
            await asyncio.to_thread(lambda: [p.wait() for p in recs])
            say("Thank you.", sink)
        if task:
            task.cancel()
            await ec.close()

    from faster_whisper import WhisperModel
    model = WhisperModel(a.model, device="cpu", compute_type="int8", cpu_threads=2)
    ref = words(SCRIPT)
    print(f"\nscript: {len(ref)} words. Assistant voice plays from about {SPEECH_AT:.0f} s to "
          f"{SPEECH_AT + 19.5:.0f} s of each take.\n")
    for d in dists:
        raw_p, clean_p = out / f"proof_{d}_raw.wav", out / f"proof_{d}_clean.wav"
        if not raw_p.exists():
            print(f"{d}: no recording")
            continue
        raw, clean = rd(raw_p), rd(clean_p)
        # what the device would send: Silero decision -> gain control (uplink frame)
        vad, agc, sent, gains = SileroVAD(), Agc(), bytearray(), []
        b = clean.tobytes()
        for i in range(0, len(b) - 1279, 1280):
            p = vad.feed(b[i:i + 1280])
            sent += agc.process(b[i:i + 1280], p >= 0.5, gate_open=p >= 0.15)
            gains.append(agc.gain_db)
        sent = np.frombuffer(bytes(sent), dtype=np.int16)
        w = wave.open(str(out / f"proof_{d}_sent.wav"), "wb")
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(sent.tobytes()); w.close()  # noqa: E702
        r = 16000
        alone = slice(int(1.5 * r), int(5.0 * r))
        both = slice(int((SPEECH_AT + 4) * r), int((SPEECH_AT + 18) * r))
        tail = slice(int((SPEECH_AT + 22) * r), int((TAKE_S - 1) * r))
        print(f"=== {d} ===")
        print(f"{'':22s}{'you alone':>12s}{'you + assistant':>18s}{'after it stops':>16s}   (dBFS)")
        for name, x in (("raw microphone", raw), ("cleaned", clean), ("sent to ANVE", sent)):
            print(f"{name:22s}{db(x[alone]):12.1f}{db(x[both]):18.1f}{db(x[tail]):16.1f}")
        print(f"gain control: {min(gains):+.0f} to {max(gains):+.0f} dB over the take; "
              f"raw clipped {100 * np.mean(np.abs(raw) > 32100):.2f} % of samples")
        for name, x in (("raw microphone", raw), ("cleaned", clean), ("sent to ANVE", sent)):
            segs, _ = model.transcribe(x.astype(np.float32) / 32768, language="en", beam_size=5,
                                       temperature=0.0, condition_on_previous_text=False,
                                       vad_filter=True)
            txt = " ".join(s.text.strip() for s in segs)
            print(f"{name:16s} word error rate {100 * wer(ref, words(txt)):3.0f} %: {txt[:420]!r}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
