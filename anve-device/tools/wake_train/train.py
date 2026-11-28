"""Train the wake head: a small 1-D convolution net over Whisper tiny.en encoder vectors.
Prints, per kind of held-out clip, how many it gets right at the chosen limit, next to
what today's sample-matching (DTW) does on the same clips, and writes wake_head.onnx."""
import sys, numpy as np, torch, torch.nn as nn
torch.manual_seed(3); np.random.seed(3)
z = np.load("feats.npz"); X, L, y, g, s = z["X"], z["L"], z["y"], z["g"], z["s"]
tr, te = np.flatnonzero(s == "train"), np.flatnonzero(s == "test")
dev = "cuda"
class Head(nn.Module):
    def __init__(self, d=384, h=96):
        super().__init__()
        self.inp = nn.Dropout(0.15)
        self.c1 = nn.Conv1d(d, h, 5, padding=2); self.c2 = nn.Conv1d(h, h, 5, padding=4, dilation=2)
        self.c3 = nn.Conv1d(h, h, 5, padding=8, dilation=4)
        self.out = nn.Sequential(nn.Dropout(0.2), nn.Linear(2 * h, 1))
    def forward(self, x, n=None):                 # x [B,T,384]; n = true lengths (None: all of T)
        h = self.inp(x).transpose(1, 2)
        h = torch.relu(self.c1(h)); h = torch.relu(self.c2(h)) + h; h = torch.relu(self.c3(h)) + h
        if n is None:
            return self.out(torch.cat([h.amax(2), h.mean(2)], 1)).squeeze(1)
        m = (torch.arange(h.shape[2], device=h.device)[None] < n[:, None])[:, None]
        mx = h.masked_fill(~m, -1e4).amax(2); mean = (h * m).sum(2) / n[:, None]
        return self.out(torch.cat([mx, mean], 1)).squeeze(1)
m = Head().to(dev)
# every source counts the same per epoch, whatever its size: the owner's 16 real clips
# must not drown under 9,000 audiobook cuts
w = np.zeros(len(tr)); gt = g[tr]
share = {"tts_pos": 3, "tts_neg": 2.5, "libri": 2.5, "own_hey": .3, "own_hey_aug": 1.2, "own_anve": .2, "own_anve_aug": .8,
         "room": .2, "room_aug": .8, "room22": .1, "room22_aug": .3, "stop": .05, "stop_aug": .15, "burst": .2, "burst_aug": .8}
for k, v in share.items():
    i = gt == k
    if i.any(): w[i] = v / i.sum()
w /= w.sum()
opt = torch.optim.AdamW(m.parameters(), 1e-3, weight_decay=1e-2)
steps = 2500; sched = torch.optim.lr_scheduler.OneCycleLR(opt, 2e-3, total_steps=steps)
Xt = torch.from_numpy(X)                           # float16 on CPU, batches moved to the GPU
for step in range(steps):
    idx = tr[np.random.choice(len(tr), 256, p=w)]
    xb = Xt[idx].to(dev).float(); nb = torch.from_numpy(L[idx].astype(np.int64)).to(dev)
    # SpecAugment-like: drop a random stretch of frames after the lead-in on some examples
    for b in range(0, 256, 4):
        t0 = np.random.randint(20, 120); xb[b, t0:t0 + np.random.randint(2, 8)] = 0
    loss = nn.functional.binary_cross_entropy_with_logits(m(xb, nb), torch.from_numpy(y[idx]).float().to(dev))
    opt.zero_grad(); loss.backward(); opt.step(); sched.step()
    if step % 500 == 0: print(step, round(loss.item(), 4), flush=True)
m.eval()
def score(ix):
    out = []
    with torch.no_grad():
        for i in ix:                               # one by one, unpadded: exactly as on the speaker
            out.append(torch.sigmoid(m(Xt[i:i + 1, :L[i]].to(dev).float())).item())
    return np.array(out)
ps = score(te); yt, gt = y[te], g[te]
neg = np.sort(ps[yt == 0])[::-1]
print("held-out negatives:", len(neg), "top scores", np.round(neg[:8], 3))
for name, thr in (("limit at 0 false wakes", neg[0] + 1e-4), ("limit at 0.5 % false wakes", neg[int(len(neg) * 0.005)]), ("0.5", 0.5), ("0.8", 0.8)):
    line = "%-28s thr %.3f |" % (name, thr)
    for k in ("own_hey", "own_anve", "tts_pos"):
        i = gt == k; line += " %s %d/%d" % (k, (ps[i] >= thr).sum(), i.sum())
    for k in ("tts_neg", "libri", "room", "room22", "burst", "stop"):
        i = gt == k; line += " | %s FP %d/%d" % (k, (ps[i] >= thr).sum(), i.sum())
    print(line)
print("own_hey scores", np.round(ps[gt == "own_hey"], 2)); print("own_anve scores", np.round(ps[gt == "own_anve"], 2))
print("worst tts_neg:", np.round(np.sort(ps[gt == "tts_neg"])[::-1][:6], 2), " tts_pos lowest:", np.round(np.sort(ps[gt == "tts_pos"])[:10], 2))
# today's system on the same held-out clips: DTW to the owner's TRAIN-round recordings
sys.path.insert(0, "../anve-device")
from anve_device.wake import Enrolled
E = Enrolled("", 0.122, 0.110)
E.templates = [X[i, :L[i]].astype(np.float32) for i in tr if g[i] in ("own_hey", "own_anve")]
hit = np.array([E.match(X[i, :L[i]].astype(np.float32))["hit"] for i in te if g[i] != "libri"])
gg = np.array([g[i] for i in te if g[i] != "libri"])
print("today's sample matching (%d samples):" % len(E.templates), " ".join("%s %d/%d" % (k, hit[gg == k].sum(), (gg == k).sum()) for k in ("own_hey", "own_anve", "tts_pos", "tts_neg", "room", "room22", "burst", "stop")))
torch.save(m.state_dict(), "wake_head.pt")
m.cpu(); torch.onnx.export(m, (torch.zeros(1, 80, 384),), "wake_head.onnx", input_names=["x"], output_names=["logit"],
                           dynamic_axes={"x": {1: "T"}}, opset_version=17)
print("saved wake_head.onnx")
