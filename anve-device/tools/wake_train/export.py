import numpy as np, torch, torch.nn as nn
class Head(nn.Module):
    def __init__(self, d=384, h=96):
        super().__init__()
        self.inp = nn.Dropout(0.15)
        self.c1 = nn.Conv1d(d, h, 5, padding=2); self.c2 = nn.Conv1d(h, h, 5, padding=4, dilation=2)
        self.c3 = nn.Conv1d(h, h, 5, padding=8, dilation=4)
        self.out = nn.Sequential(nn.Dropout(0.2), nn.Linear(2 * h, 1))
    def forward(self, x):
        h = x.transpose(1, 2)
        h = torch.relu(self.c1(h)); h = torch.relu(self.c2(h)) + h; h = torch.relu(self.c3(h)) + h
        return self.out(torch.cat([h.amax(2), h.mean(2)], 1)).squeeze(1)
m = Head(); m.load_state_dict(torch.load("wake_head.pt", map_location="cpu")); m.eval()
torch.onnx.export(m, (torch.zeros(1, 80, 384),), "wake_head.onnx", input_names=["x"], output_names=["logit"],
                  dynamic_axes={"x": {1: "T"}}, opset_version=17, dynamo=False)
import onnxruntime as ort
s = ort.InferenceSession("wake_head.onnx", providers=["CPUExecutionProvider"])
z = np.load("feats.npz"); X, L, g, sp = z["X"], z["L"], z["g"], z["s"]
for k in ("own_hey", "own_anve", "room", "stop"):
    ix = [i for i in np.flatnonzero((g == k) & (sp == "test"))][:6]
    a = [float(1 / (1 + np.exp(-s.run(None, {"x": X[i:i+1, :L[i]].astype(np.float32)})[0][0]))) for i in ix]
    b = [float(torch.sigmoid(m(torch.from_numpy(X[i:i+1, :L[i]].astype(np.float32)))).item()) for i in ix]
    print(k, np.round(a, 3), "torch", np.round(b, 3))
import os; print("onnx bytes", os.path.getsize("wake_head.onnx"))
