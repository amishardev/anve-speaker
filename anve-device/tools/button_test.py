#!/usr/bin/env python3
"""Check the two buttons' wiring: press each a few times and watch the lines appear.

    python3 tools/button_test.py [--seconds 30]

Mute is GPIO17 (header pin 11), action is GPIO27 (pin 13); each switch goes to a ground
pin (9 and 14) with no resistor. Stop the speaker service first, it owns the pins:
    sudo systemctl stop anve-device
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from anve_device import config as C  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=30)
    a = ap.parse_args()
    from gpiozero import Button
    b = C.load()["buttons"]
    t0 = time.monotonic()
    count = {"mute": 0, "action": 0}

    def make(name, pin):
        btn = Button(pin, pull_up=True, bounce_time=0.03)
        down = {}

        def pressed():
            down["t"] = time.monotonic()
            count[name] += 1
            print(f"{time.monotonic() - t0:5.1f} s  {name} (GPIO{pin}) pressed", flush=True)

        def released():
            print(f"{time.monotonic() - t0:5.1f} s  {name} released after "
                  f"{time.monotonic() - down.get('t', time.monotonic()):.2f} s", flush=True)
        btn.when_pressed, btn.when_released = pressed, released
        print(f"{name}: GPIO{pin}, reads {'PRESSED (stuck low?)' if btn.is_pressed else 'open'}")
        return btn

    keep = [make("mute", b["mute_gpio"]), make("action", b["action_gpio"])]
    print(f"press the buttons; watching for {a.seconds:.0f} s")
    time.sleep(a.seconds)
    print(f"presses seen: mute {count['mute']}, action {count['action']}")
    del keep


if __name__ == "__main__":
    main()
