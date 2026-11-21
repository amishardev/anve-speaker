#!/usr/bin/env python3
"""Contrast of every text colour in src/ui.tsx against the surface it sits on (WCAG).
Small text needs 4.5:1. Run it after changing a colour:   python scripts/contrast.py"""
import re
import sys
from pathlib import Path

src = (Path(__file__).resolve().parent.parent / "src" / "ui.tsx").read_text(encoding="utf-8")


def block(name):
    body = re.search(name + r"[^=]*=\s*\{(.*?)\n\};", src, re.S).group(1)
    return dict(re.findall(r"(\w+):\s*'(#[0-9A-Fa-f]{6})'", body))


def lum(h):
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4  # noqa: E731
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def ratio(a, b):
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


PAIRS = [("ink", "card"), ("ink", "surface"), ("sub", "card"), ("sub", "surface"), ("sub", "fill"),
         ("faint", "card"), ("faint", "fill"), ("faint", "surface"), ("accent", "card"), ("accent", "tint"),
         ("accent", "surface"), ("onPrimary", "primary"), ("bad", "card"), ("bad", "badBg"), ("ink", "tint"),
         ("onBox", "box"), ("onBox2", "box")]
L, D = block("const light"), block("const darkT")
worst = 99.0
print(f"{'text on surface':22s} light   dark")
for a, b in PAIRS:
    l, d = ratio(L[a], L[b]), ratio(D[a], D[b])
    worst = min(worst, l, d)
    print(f"{a + ' on ' + b:22s} {l:5.1f}  {d:5.1f}{'   <-- below 4.5' if min(l, d) < 4.5 else ''}")
print(f"\nlowest: {worst:.1f}:1")
sys.exit(0 if worst >= 4.5 else 1)
