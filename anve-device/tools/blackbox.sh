#!/usr/bin/env bash
# The Pi's "black box": what it looked like just before it went down.
#
# Every 15 seconds one line is written over the last one: the time, the 5 V supply (now and
# the lowest of five readings), whether the Pi has flagged low voltage, temperature, load,
# free memory and the busiest program. When the Pi starts, the line left by the previous
# run is written to the journal and added to a short history, together with how this start
# happened: the clock chip keeps time only while the Pi has power, so "the clock was lost"
# means the power really went, and "the clock kept running" means the Pi froze or was reset
# with the power still on (the watchdog does that after a minute).
#
#   tools/blackbox.sh --show     the history, newest last
#
# Run by anve-blackbox.service (tools/optimise_pi.sh installs it). It costs a few short
# commands every 3 seconds and one tiny write every 15.
set -u
DIR=/var/lib/anve-device
F="$DIR/blackbox"
H="$DIR/blackbox.history"

if [ "${1:-}" = "--show" ]; then
  [ -s "$H" ] && cat "$H" || echo "no restart has been recorded yet"
  [ -s "$F" ] && echo "now: $(cat "$F")"
  exit 0
fi

volts() { vcgencmd pmic_read_adc EXT5V_V 2>/dev/null | sed -E 's/.*=([0-9]+\.[0-9]{2}).*/\1/'; }

mkdir -p "$DIR"
if [ -s "$F" ]; then
  clock="$(journalctl -k -b 0 -o cat 2>/dev/null | grep -m1 'setting system clock to' | sed -E 's/.*setting system clock to ([^ ]+).*/\1/')"
  case "$clock" in
    1970*) how="power was lost (the clock chip had lost its time)" ;;
    "")    how="unknown (no clock line in this start's log)" ;;
    *)     how="power stayed on: a freeze or a reset (the clock chip kept its time)" ;;
  esac
  if journalctl -b -1 -o cat 2>/dev/null | tail -40 | grep -qE 'Reached target.*(Reboot|Power-Off)|Shutting down|systemd-shutdown'; then
    how="an ordinary restart or shutdown"
  fi
  last="$(cat "$F")"
  echo "<5>the previous run's last record: $last | this start: $how"
  { echo "$(date '+%F %T') started; $how; last record of the run before: $last"; } >> "$H"
  tail -n 60 "$H" > "$H.new" && mv "$H.new" "$H"
fi

while :; do
  low=9.99; now=""
  for _ in 1 2 3 4 5; do
    now="$(volts)"
    [ -n "$now" ] && [ "$(printf '%s\n%s\n' "$now" "$low" | sort -n | head -1)" = "$now" ] && low="$now"
    sleep 3
  done
  line="$(date '+%F %T') 5V=${now:-?} lowest=${low} throttled=$(vcgencmd get_throttled 2>/dev/null | cut -d= -f2) "
  line+="temp=$(vcgencmd measure_temp 2>/dev/null | sed -E "s/temp=([0-9.]+).*/\1/")C load=$(cut -d' ' -f1 /proc/loadavg) "
  line+="free=$(awk '/MemAvailable/ {printf "%d", $2 / 1024}' /proc/meminfo)MB "
  line+="busiest=$(ps -eo pcpu,comm --sort=-pcpu | awk 'NR == 2 {print $2 ":" $1 "%"}')"
  printf '%s\n' "$line" > "$F.new" && sync "$F.new" 2>/dev/null && mv "$F.new" "$F"
done
