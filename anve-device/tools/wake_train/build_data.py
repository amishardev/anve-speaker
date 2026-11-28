"""Turn clips into training examples for the wake model: each example is what the speaker's
Segmenter would hand over (about 0.5 s of room, then speech, at most 3 s), run through the
same Whisper tiny.en encoder call the speaker uses (Spotter.embed). Labels: 1 = the clip
starts with "Hey ANVE" / "ANVE", 0 = anything else."""
import random, sys, wave, time, numpy as np
from pathlib import Path
import soundfile as sf
from scipy.signal import fftconvolve
sys.path.insert(0, "../anve-device")
from anve_device.wake import Spotter, Enrolled
R = 16000
rng = random.Random(11); nrng = np.random.default_rng(11)
sp = Spotter()
def wav(p):
    with wave.open(str(p)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
TEST_VOICES = {"af_sky", "am_puck", "bf_lily", "bm_lewis"}
libri = sorted(Path("dl_libri/LibriSpeech/dev-clean").glob("*/*/*.flac"))
spk = sorted({p.parts[-3] for p in libri}); TEST_SPK = set(spk[-6:])
def libri_cut(test, at_start=None, lo=0.6, hi=3.0):
    while True:
        p = rng.choice(libri)
        if (p.parts[-3] in TEST_SPK) != test: continue
        a, sr = sf.read(str(p), dtype="float32")
        n = int(rng.uniform(lo, hi) * R)
        if len(a) <= n + R: continue
        s = 0 if (at_start if at_start is not None else rng.random() < 0.5) else rng.randrange(0, len(a) - n)
        if s == 0:                       # skip the silence books start with
            nz = np.flatnonzero(np.abs(a) > 0.02); s = max(0, int(nz[0]) - 800) if nz.size else 0
        return a[s:s + n]
def noise(n, db):
    x = nrng.standard_normal(n).astype(np.float32)
    if rng.random() < 0.6:               # pink-ish
        x = np.cumsum(x); x -= np.convolve(x, np.ones(400) / 400, mode="same")
    return x / (np.sqrt(np.mean(x * x)) + 1e-9) * 10 ** (db / 20)
def reverb(a):
    rt = rng.uniform(0.15, 0.6); n = int(rt * R)
    ir = nrng.standard_normal(n).astype(np.float32) * np.exp(-6.9 * np.arange(n) / n); ir[0] = 1.0
    ir[1:] *= rng.uniform(0.2, 0.6)
    return fftconvolve(a, ir)[:len(a) + n // 3].astype(np.float32)
def assemble(speech, test, tail_p=0.4, cut=True):
    """lead-in + speech (+ a request spoken right after it), at the level a mic would give"""
    a = speech / (np.sqrt(np.mean(speech * speech)) + 1e-9)
    if rng.random() < tail_p:
        t = libri_cut(test, lo=0.4, hi=1.6); t = t / (np.sqrt(np.mean(t * t)) + 1e-9)
        a = np.concatenate([a, np.zeros(int(rng.uniform(0.03, 0.3) * R), np.float32), t * rng.uniform(0.6, 1.2)])
    else:
        a = np.concatenate([a, np.zeros(int(rng.uniform(0.15, 0.4) * R), np.float32)])
    if rng.random() < 0.5: a = reverb(a)
    level = 10 ** (rng.uniform(-30, -14) / 20)
    a = np.concatenate([np.zeros(int(rng.uniform(0.3, 0.55) * R), np.float32), a * level])
    a = a + noise(len(a), 20 * np.log10(level) - rng.uniform(12, 40))
    if cut:
        if rng.random() < 0.35: a = a[:int(1.7 * R)]          # the early look at a phrase
        a = a[:int(3.0 * R)]
    return np.clip(a, -1, 1)
def real_aug(a):
    g = 10 ** (rng.uniform(-6, 6) / 20)
    a = a * g + noise(len(a), rng.uniform(-62, -48))
    s = rng.randrange(0, 1600)                               # up to 0.1 s less lead-in
    return np.clip(a[s:], -1, 1)
items = []      # (audio float32, label, group, split)
def add(a, y, g, split): items.append((a, y, g, split))
for p in sorted(Path("tts/pos").glob("*/*.wav")):
    v = p.parts[-2]; test = v in TEST_VOICES; a = wav(p)
    for _ in range(2 if test else 4): add(assemble(a, test), 1, "tts_pos", "test" if test else "train")
for p in sorted(Path("tts/neg").glob("*/*.wav")):
    v = p.parts[-2]; test = v in TEST_VOICES; a = wav(p)
    for _ in range(2 if test else 3): add(assemble(a, test), 0, "tts_neg", "test" if test else "train")
for i in range(11000):
    test = i % 8 == 0
    add(assemble(libri_cut(test), test, tail_p=0.0), 0, "libri", "test" if test else "train")
D = Path("data/data_23nov")
E = Enrolled("", 9, 9)
own = sorted((D / "hey_anve").glob("*.wav")); anve = sorted((D / "anve").glob("*.wav"))
E.templates = [sp.embed((wav(f) * 32767).astype(np.int16).tobytes()) for f in own + anve]
for f in own:
    test = f.name[:4] in ("1756", "1757", "1758")            # the second recording round is held out
    add(wav(f), 1, "own_hey", "test" if test else "train")
    if not test:
        for _ in range(25): add(real_aug(wav(f)), 1, "own_hey_aug", "train")
for i, f in enumerate(anve):
    test = i >= 4
    add(wav(f), 1, "own_anve", "test" if test else "train")
    if not test:
        for _ in range(40): add(real_aug(wav(f)), 1, "own_anve_aug", "train")
dropped = 0
for g, folder in (("room", "not_phrase_room"), ("room22", "not_phrase_22nov"), ("stop", "stop"), ("burst", "music_bursts_unlabelled")):
    fs = sorted((D / folder).glob("*.wav"))
    for i, f in enumerate(fs):
        if f.stat().st_size < 22500 or "wake" in f.name: continue
        a = wav(f)[:3 * R]
        # these folders are "not the phrase" by my reading of the log, not by ear: a clip that
        # sits close to the owner's recordings may well be the phrase, so it is left out
        if E.match(sp.embed((a * 32767).astype(np.int16).tobytes()))["whole"] < 0.17:
            dropped += 1; continue
        test = i % 3 == 0
        add(a, 0, g, "test" if test else "train")
        if not test:
            for _ in range(6): add(real_aug(a), 0, g + "_aug", "train")
print("items", len(items), "left out as possibly the phrase:", dropped, flush=True)
N = len(items); X = np.zeros((N, 150, 384), np.float16); L = np.zeros(N, np.int16)
t0 = time.time()
for i, (a, y, g, s) in enumerate(items):
    e = sp.embed((a * 32767).astype(np.int16).tobytes())[:150]
    X[i, :len(e)] = e; L[i] = len(e)
    if i % 2000 == 0: print(i, "%.0f s" % (time.time() - t0), flush=True)
np.savez("feats.npz", X=X, L=L, y=np.array([it[1] for it in items], np.int8),
         g=np.array([it[2] for it in items]), s=np.array([it[3] for it in items]))
from collections import Counter
print(Counter((it[2], it[3], it[1]) for it in items))
