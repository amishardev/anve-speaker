#!/usr/bin/env bash
# Does the speaker recover by itself? Run ON THE PI while anve-device.service is running.
#
#   tools/resilience_test.sh status      what is up right now
#   tools/resilience_test.sh wifi [60]   drop Wi-Fi for N seconds, then watch it reconnect
#   tools/resilience_test.sh speaker     disconnect the Bluetooth speaker, watch it come back
#   tools/resilience_test.sh mic [90]    you unplug and replug the microphone; this watches
#   tools/resilience_test.sh crash       kill the program, watch systemd restart it
#
# The Wi-Fi test cuts this Pi off the network, SSH included. The restore is scheduled
# with systemd BEFORE the link is cut, so it comes back even if this shell dies.
set -uo pipefail
UNIT=anve-device.service
MAC="$(grep -oE '^bluetooth_mac *= *"[^"]+"' "$(dirname "$0")/../config.toml" 2>/dev/null | cut -d'"' -f2)"
since() { journalctl -u "$UNIT" --since "$1" --no-pager -o short | cut -d' ' -f3,6- | cut -c1-170; }
status() {
  echo "service: $(systemctl is-active $UNIT), restarts $(systemctl show $UNIT -p NRestarts --value), since $(systemctl show $UNIT -p ActiveEnterTimestamp --value)"
  echo "wifi: $(nmcli -t -f GENERAL.STATE,GENERAL.CONNECTION dev show wlan0 2>/dev/null | cut -d: -f2 | paste -sd' ')"
  [ -n "$MAC" ] && echo "speaker: $(bluetoothctl info "$MAC" | grep -oE 'Connected: (yes|no)')"
  echo "mic: $(arecord -l 2>/dev/null | grep -c UACDemo) capture device(s) named UACDemo"
  echo "echo canceller nodes: $(pw-cli ls Node 2>/dev/null | grep -c 'anve_ec_')"
  echo "last link lines:"; journalctl -u "$UNIT" --no-pager -o cat | grep -E "connected|link down|ready:|not reconnecting|audio devices|echo canceller|capturing|capture from" | tail -5 | cut -c1-150
}
case "${1:-status}" in
  status) status ;;
  wifi)
    N="${2:-60}"; T0="$(date '+%Y-%m-%d %H:%M:%S')"
    CON="$(nmcli -t -f GENERAL.CONNECTION dev show wlan0 | cut -d: -f2)"
    echo "$T0 dropping Wi-Fi ($CON) for $N s; restore is scheduled first"
    sudo systemd-run --quiet --on-active="$N" --unit "anve-wifi-restore-$$" nmcli device connect wlan0
    sudo nmcli device disconnect wlan0
    sleep "$((N + 25))"
    echo "--- after: $(date +%T) ---"; status
    echo "--- what the service logged ---"; since "$T0" | grep -E "link down|connected|ready:|retrying|no frame|state error" ;;
  speaker)
    [ -n "$MAC" ] || { echo "no bluetooth_mac in config.toml"; exit 1; }
    T0="$(date '+%Y-%m-%d %H:%M:%S')"; echo "$T0 disconnecting $MAC"
    bluetoothctl disconnect "$MAC" >/dev/null; sleep 45
    status; echo "--- log ---"; since "$T0" | grep -E "Bluetooth|audio devices|echo canceller|capturing|capture from|speaker volume" ;;
  mic)
    N="${2:-90}"; T0="$(date '+%Y-%m-%d %H:%M:%S')"
    echo "$T0 unplug the microphone now, wait 10 s, plug it back in. Watching for $N s."
    for i in $(seq 1 "$((N / 5))"); do sleep 5; echo "  $(date +%T) mic present: $(arecord -l 2>/dev/null | grep -c UACDemo), ec nodes: $(pw-cli ls Node 2>/dev/null | grep -c 'anve_ec_')"; done
    echo "--- log ---"; since "$T0" | grep -E "audio devices|echo canceller|capturing|capture from|microphone" ;;
  crash)
    T0="$(date '+%Y-%m-%d %H:%M:%S')"; PID="$(systemctl show $UNIT -p MainPID --value)"
    echo "$T0 killing pid $PID"; sudo kill -9 "$PID"; sleep 25
    status; echo "--- log ---"; journalctl -u "$UNIT" --since "$T0" --no-pager -o short | cut -d' ' -f3,5- | grep -E "Main process|Scheduled restart|Started|connected|ready:" | cut -c1-150 ;;
  *) sed -n '2,12p' "$0" ;;
esac
