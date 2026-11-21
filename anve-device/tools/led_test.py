#!/usr/bin/env python3
"""LED strip check: red, green, blue, dim white, a single pixel running the strip, off.

    python3 tools/led_test.py [--count 30] [--brightness 0.35] [--hold 2.0]
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from anve_device.led import Strip  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=30)
    ap.add_argument("--brightness", type=float, default=0.35)
    ap.add_argument("--hold", type=float, default=2.0)
    a = ap.parse_args()
    strip = Strip(a.count, a.brightness)
    try:
        for name, rgb in (("red", (255, 0, 0)), ("green", (0, 255, 0)),
                          ("blue", (0, 0, 255)), ("dim white", (90, 90, 90))):
            print(f"{time.strftime('%H:%M:%S')} {name}", flush=True)
            strip.fill(rgb)
            time.sleep(a.hold)
        print(f"{time.strftime('%H:%M:%S')} one yellow pixel running from first to last",
              flush=True)
        for i in range(a.count):
            px = [(0, 0, 0)] * a.count
            px[i] = (255, 160, 0)
            strip.show(px)
            time.sleep(0.08)
        print(f"{time.strftime('%H:%M:%S')} off", flush=True)
    finally:
        strip.close()


if __name__ == "__main__":
    main()
