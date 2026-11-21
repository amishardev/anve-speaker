#!/usr/bin/env bash
# What LUMINA is doing right now, and how to open its dashboard.   ./status.sh
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DIR="$(dirname "$HERE")"
PYTHON="$DIR/.venv/bin/python"
[ -x "$PYTHON" ] || PYTHON="$(command -v python3)"
cd "$DIR"
echo "service:   $(systemctl is-active lumina.service 2>/dev/null || echo 'not installed')"
"$PYTHON" - <<'EOF'
import json, socket, urllib.request
from lumina.config import Settings, dashboard_key
s = Settings()
port = s.values["dashboard"]["port"]
try:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=3) as r:
        st = json.loads(r.read())
    real = st["strip"]["driver"] == "spi"
    print("showing:  ", st["visual"] + (" (demo; really " + st["real"] + ")" if st["demo"] else ""))
    print("assistant:", "reporting" if st["alive"] else "not reporting")
    print("LEDs:     ", ("ON, " if real else "off (preview only), ") + f"{st['strip']['count']} LEDs, brightness "
          f"{round(st['settings']['brightness'] * 100)} % (limit {round(st['settings']['max_brightness'] * 100)} %), about {st['power_ma']} mA")
    if st["strip"]["problem"]:
        print("problem:  ", st["strip"]["problem"])
    if st.get("error"):
        print("error:    ", st["error"].get("code", ""), st["error"].get("message", ""))
except OSError as e:
    print("LUMINA is not answering on port", port, f"({e})")
try:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as k:
        k.connect(("10.255.255.255", 1))
        ip = k.getsockname()[0]
except OSError:
    ip = "<this Pi's address>"
print(f"dashboard: http://{ip}:{port}/   key: {dashboard_key(s.path.parent)}")
EOF
