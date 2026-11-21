#!/usr/bin/env python3
"""Look at the setup page on a laptop, with no box and no network change.

It starts the setup program in --dry-run (a pretend Wi-Fi: the password "wrong..." is
refused, everything else joins) with a throwaway identity for the ANVE Box cloud, so the
whole page can be walked through, the link step included.

    python tools/portal_preview.py [port]        then open http://127.0.0.1:18080/

Nothing it makes is real: the identity and the link belong to no box."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
port = sys.argv[1] if len(sys.argv) > 1 else "18080"
tmp = Path(tempfile.mkdtemp(prefix="anve-portal-"))
for name in ("state", "etc", "box"):
    (tmp / name).mkdir()
subprocess.run([sys.executable, str(ROOT / "tools" / "anvebox_factory.py"), "new",
                "--out", str(tmp / "box" / "identity.json"), "--record", str(tmp / "box" / "record.json")],
               check=True, stdout=subprocess.DEVNULL, env=dict(os.environ, PYTHONPATH=str(ROOT)))
env = dict(os.environ, ANVE_STATE_DIR=str(tmp / "state"), ANVE_ETC_DIR=str(tmp / "etc"),
           ANVEBOX_IDENTITY=str(tmp / "box" / "identity.json"), ANVEBOX_STATE=str(tmp / "box"),
           PYTHONUNBUFFERED="1")
env.setdefault("ANVE_PORTAL_HOLD_S", "120")
print(f"setup page: http://127.0.0.1:{port}/   (pretend network; files in {tmp})", flush=True)
sys.exit(subprocess.call([sys.executable, "-m", "anve_device.provision", "--dry-run", "--port", port],
                         cwd=ROOT, env=env))
