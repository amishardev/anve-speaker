"""Synthetic speech for the wake model: the phrase in 28 Kokoro voices (Apache-2.0, English
voices only) and things that must NOT wake it, in the same voices. 16 kHz mono, trimmed."""
import itertools, random, sys, wave, numpy as np
from pathlib import Path
from kokoro_onnx import Kokoro
import librosa
k = Kokoro("dl_kokoro/kokoro-v1.0.onnx", "dl_kokoro/voices-v1.0.bin")
voices = [v for v in k.get_voices() if v[:2] in ("af", "am", "bf", "bm")]
NAMES = ["Unvee", "Anvee", "Onvee", "Envy", "Anvi", "Anway", "Unvay", "Anvay"]
GREET = ["Hey", "Hey", "Hey", "Hi", "Okay", "Hello"]
NEAR = ["Hey Andy.", "Hey Annie.", "Hey Anna.", "Hey Henry.", "Hey everyone.", "Hey Alexa.", "Hey Google.",
        "Hey.", "Hello.", "Okay.", "Anyway.", "And they.", "Hey, and we.", "Navy.", "Hey honey.", "Hey Amy.",
        "Hey buddy.", "On the way.", "Any day.", "Andy.", "Annie.", "Hey Emmy.", "Hey Abby.", "Hey man.",
        "And we.", "Envelope.", "Hey, anyone?", "Heavy.", "Hey Auntie.", "Hey Ravi.", "Hey Avi.", "Hey Ali.",
        "Stop.", "Play some music.", "What time is it?", "Turn it up.", "Thank you.", "Yes.", "No.", "Come here.",
        "Good morning.", "How are you?", "Next song.", "I am ready.", "Let's go.", "One, two, three.", "Hey, what's up?",
        "Hey, how are you?", "Hey, listen.", "Hey, wait.", "Hi there.", "Okay then.", "Hello everyone.", "Hey guys."]
def say(text, voice, speed):
    a, sr = k.create(text, voice=voice, speed=speed, lang="en-us")
    a = librosa.resample(a.astype(np.float32), orig_sr=sr, target_sr=16000)
    a, _ = librosa.effects.trim(a, top_db=35)
    return (a / (np.abs(a).max() + 1e-6) * 0.5 * 32767).astype(np.int16)
def save(p, a):
    p.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(a.tobytes())
random.seed(7); n = 0
W = int(sys.argv[1]) if len(sys.argv) > 1 else -1
for vi, v in enumerate(voices):
    if W >= 0 and vi % 6 != W: random.seed(7 + vi); continue
    random.seed(7 + vi)
    for name in NAMES:
        for sp_ in (0.85, 1.0, 1.15):
            g = random.choice(GREET)
            for kind, text in (("hey", f"{g} {name}."), ("bare", f"{name}."), ("heyq", f"{g} {name}!"), ("bareq", f"{name}?")):
                if kind in ("heyq", "bareq") and sp_ != 1.0: continue
                p = Path(f"tts/pos/{v}/{kind}_{name}_{sp_}.wav")
                if not p.exists(): save(p, say(text, v, sp_))
                n += 1
    for i, text in enumerate(NEAR):
        sp_ = random.choice((0.85, 1.0, 1.15))
        p = Path(f"tts/neg/{v}/{i:02d}_{sp_}.wav")
        if not p.exists(): save(p, say(text, v, sp_))
        n += 1
    print(v, n, flush=True)
