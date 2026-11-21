#!/usr/bin/env bash
# Try the setup hotspot on a box that is already set up, WITHOUT changing anything.
#
#   ~/anve-device/tools/hotspot_trial.sh [seconds]            default 360, at most 900
#   ~/anve-device/tools/hotspot_trial.sh --apply [seconds]    set up again, FOR REAL: what
#       the phone sends is applied (new Wi-Fi, name, key; the old key is kept as
#       /etc/anve-device/key.before-resetup). If nothing was applied when the time is up,
#       the box goes back to the Wi-Fi and key it had.
#
# For that time the box leaves its Wi-Fi and opens "ANVE-xxxx". A phone with the app can
# find it, join it, read the Wi-Fi list and send its settings; the box checks them,
# answers "this was a test" and stores nothing. Then it goes back to the Wi-Fi it was on.
# A computer that joins it gets the setup page (portal/), which says "test run" too.
# The key, the saved Wi-Fi and the "set up" mark are not touched.
#
# While it runs the box is NOT reachable over its normal Wi-Fi (no SSH, no speaker).
# Two things bring it back: the setup program itself when the time is up, and, in case
# that program has a fault, an independent timer (hotspot_revert.sh) 90 s later (5 minutes
# later with --apply). A test stays open while a setup page is open on it, and closes 30 s
# after a completed flow once no page is asking any more; never later than its time.
set -euo pipefail
CMD=trial
if [ "${1:-}" = "--apply" ]; then CMD=again; shift; fi
SECS="${1:-360}"
[ "$SECS" -ge 30 ] && [ "$SECS" -le 900 ] || { echo "seconds must be 30..900"; exit 1; }
HERE="$(cd "$(dirname "$0")" && pwd)"
[ -e /var/lib/anve-device/provisioned ] || { echo "This box is not set up: it is in setup mode already."; exit 1; }
systemctl is-active --quiet anve-provision || { echo "anve-provision is not running"; exit 1; }
PREV="$(nmcli -t -f GENERAL.CONNECTION dev show wlan0 | cut -d: -f2-)"
[ -n "$PREV" ] || { echo "The box is not on a Wi-Fi now; not starting a test."; exit 1; }

# The independent way back, armed BEFORE anything changes. A real set-up-again may still
# be busy when the window ends (the page holds the hotspot up to 2 minutes for "I have the
# link", then the box joins the Wi-Fi), so its safety timer waits longer.
LATE=90
[ "$CMD" = again ] && LATE=300
sudo systemctl stop anve-trial-revert.timer anve-trial-revert.service 2>/dev/null || true
sudo systemctl reset-failed anve-trial-revert.service 2>/dev/null || true
sudo systemd-run --quiet --unit=anve-trial-revert --on-active="$((SECS + LATE))s" \
  --description="ANVE: back to Wi-Fi after a hotspot test" /bin/bash "$HERE/hotspot_revert.sh" "$PREV"

python3 - "$SECS" "$CMD" <<'PY'
import json, socket, sys
s = socket.socket(socket.AF_UNIX)
s.connect("/run/anve-provision.sock")
s.sendall(json.dumps({"cmd": sys.argv[2], "seconds": int(sys.argv[1])}).encode() + b"\n")
a = json.loads(s.makefile().readline() or "{}")
if not a.get("ok"):
    print("The setup program refused the test:", a.get("error"))
    sys.exit(1)
print(f"Hotspot {a['ssid']} opens in about 10 seconds, for up to {a['seconds']} s"
      + (" (settings WILL be applied)." if a.get("applies") else " (test: nothing is changed)."))
PY
if [ "$CMD" = again ]; then
  echo "If nothing is sent, back on \"$PREV\" by itself at about $(date -d "+$((SECS + 20)) seconds" +%H:%M:%S); safety timer at $(date -d "+$((SECS + LATE)) seconds" +%H:%M:%S)."
else
  echo "Back on \"$PREV\" by itself at $(date -d "+$((SECS + 20)) seconds" +%H:%M:%S) at the latest; safety timer at $(date -d "+$((SECS + LATE)) seconds" +%H:%M:%S)."
fi
