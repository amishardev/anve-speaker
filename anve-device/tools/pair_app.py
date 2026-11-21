#!/usr/bin/env python3
"""Let one phone pair with a speaker that is already set up.

    ~/anve-device/tools/pair_app.py          prints a 6-digit code, valid for 5 minutes
    ~/anve-device/tools/pair_app.py --close  closes the pairing window

In the app: "I already set up my box" -> type the code. The code works for those 5
minutes (close it earlier with --close); five wrong codes close the window. Run it as
the user the speaker runs as (not root)."""
import json
import secrets
import sys
import time
from pathlib import Path

FILE = Path("/run/anve/pair-code")
MINUTES = 5


def main() -> int:
    if not FILE.parent.is_dir():
        print("The speaker is not running (no /run/anve). Start it: sudo systemctl start anve-device")
        return 1
    if "--close" in sys.argv:
        FILE.unlink(missing_ok=True)
        print("Pairing is closed.")
        return 0
    code = f"{secrets.randbelow(1_000_000):06d}"
    tmp = FILE.with_suffix(".tmp")
    tmp.touch(mode=0o600)
    tmp.write_text(json.dumps({"code": code, "until": time.time() + MINUTES * 60}))
    tmp.replace(FILE)
    print(f"Pairing code: {code[:3]} {code[3:]}   (valid for {MINUTES} minutes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
