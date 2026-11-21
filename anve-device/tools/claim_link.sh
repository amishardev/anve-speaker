#!/usr/bin/env bash
# Print the link that adds this box to an ANVE account.   sudo tools/claim_link.sh
#
# SECRET: whoever opens this link while signed in becomes the box's owner. Run it yourself,
# in your own terminal, and open the link in your own browser. It works once; after that
# the box makes a new one. A box that already has an owner says so instead.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
[ "$(id -u)" = 0 ] || { echo "Run it with sudo: the box's identity is readable by root only."; exit 1; }
cd "$HERE"
exec env PYTHONPATH="$HERE" "$HERE/.venv/bin/python" - <<'PY'
from pathlib import Path
from anve_device import boxlink, config
cfg = config.load(None)
identity = boxlink.load_identity(Path("/etc/anvebox/identity.json"))
disk = boxlink.Disk(Path("/var/lib/anvebox"))
if disk.claimed:
    print(f"Box {identity.device_id} already has an owner. Remove it from that account first, or reset the box.")
else:
    print(f"Box {identity.device_id}. Sign in at {cfg['boxlink']['cloud']} first, then open this link (it works once):")
    print()
    print("  " + boxlink.claim_link(cfg["boxlink"]["cloud"], identity, disk))
    print()
PY
