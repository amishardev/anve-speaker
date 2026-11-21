#!/usr/bin/env bash
# Tune the Raspberry Pi for its one job: being the ANVE speaker.
#
#   ~/anve-device/tools/optimise_pi.sh           apply (safe to run again)
#   ~/anve-device/tools/optimise_pi.sh --status  show what is in effect, change nothing
#   ~/anve-device/tools/optimise_pi.sh --undo    put everything back
#
# Every change is small, has a reason, and can be undone. Nothing here touches the Wi-Fi
# connection itself, the desktop (the speaker's browser needs it) or remote access
# (SSH, VNC), and nothing touches the boot files. The sound priority takes effect after
# a reboot; the script says so.
#
#   1. Sound first. PipeWire (microphone, echo canceller, speaker) may use real-time
#      priority, so music and speech do not stutter while the wake-word model or the
#      browser is busy. Before: its audio threads ran like any other program.
#   2. Wi-Fi power saving off. With it on, the radio sleeps between packets: replies and
#      the app's requests arrive up to ~100 ms late, in bursts.
#   3. Services this box does not use are switched off: printing (cups), NFS helpers
#      (rpcbind, nfs-blkmap), modems. rpcbind was listening on the whole network.
#   4. The journal is limited to 200 MB on the SD card, kept across restarts, and
#      written to the card every 15 s instead of every 5 minutes: after a freeze or a
#      power cut the last lines before it are there to read (on 19 Nov the minute before
#      a hang was simply missing).
#   5. If the kernel itself crashes, the Pi restarts after 5 s instead of sitting there
#      until the watchdog notices (the watchdog, 1 minute, was already on).
#   6. The daily rebuild of the manual-page index is off: a burst of work at a random
#      time that a speaker has no use for.
#   7. The Wi-Fi sign-in check runs every 45 s and wrote three lines to the journal each
#      time (about 240 an hour). Now it is silent unless it has something to say.
#   8. A "black box" (tools/blackbox.sh): voltage, temperature and load, noted every 15 s,
#      and read back after every restart.
#
# Looked at and left alone, on purpose: the automatic-update timers (nothing is set up to
# upgrade by itself, so they do nothing worth stopping), the desktop and its helpers (the
# speaker's browser shows on it; they use about 350 MB of 16 GB), VNC and SSH.
#
# Tried and taken out again (18 Nov 2026): capping the CPU from the first moment of boot
# with arm_freq=1800 in config.txt. The cap took effect, and the kernel still logged
# "Undervoltage detected!" twice in the first 12 s of boot, exactly as without it. The
# dip at boot does not come from the CPU clock, so the line only added risk. This script
# removes it if it finds it. What fixes the dip is a 5 V / 5 A supply (or a shorter,
# thicker USB-C cable).
set -euo pipefail
U="${SUDO_USER:-$(id -un)}"; N="$(id -u "$U")"
RT=/etc/systemd/system/user@"$N".service.d/anve-realtime.conf
PS=/etc/NetworkManager/conf.d/anve-wifi-powersave.conf
JD=/etc/systemd/journald.conf.d/anve.conf
JD_OLD=/etc/systemd/journald.conf.d/99-anve-persistent.conf
SC=/etc/sysctl.d/90-anve.conf
WQ=/etc/systemd/system/anve-wifi-login.service.d/quiet.conf
BB=/etc/systemd/system/anve-blackbox.service
HERE="$(cd "$(dirname "$0")/.." && pwd)"
SYSCTL="$(command -v sysctl || echo /usr/sbin/sysctl)"
CFG=/boot/firmware/config.txt
UNUSED="cups.service cups.socket cups.path cups-browsed.service rpcbind.service rpcbind.socket nfs-blkmap.service ModemManager.service"
MARK="# ANVE speaker: CPU capped for a 3 A power supply (tools/optimise_pi.sh)"
IW="$(command -v iw || echo /usr/sbin/iw)"

status() {
  echo "sound priority  : $(ps -eLo cls,rtprio,comm | awk '$3 ~ /data-loop/ && $1 == "FF" {n++} END {print (n ? n " PipeWire audio threads at real-time priority" : "normal priority (not real-time)")}')$([ -f "$RT" ] || echo ' [not configured]')"
  echo "Wi-Fi power save: $($IW dev wlan0 get power_save 2>/dev/null | awk '{print $3}')$([ -f "$PS" ] && echo ' (off at every boot)')"
  R="$(for s in $UNUSED; do if systemctl is-active --quiet "$s" 2>/dev/null; then printf '%s ' "$s"; fi; done)"
  echo "unused services : ${R:-none running}"
  echo "boot file       : $(grep -q '^arm_freq=' "$CFG" && echo "has $(grep '^arm_freq=' "$CFG") (left over; run this script to remove it)" || echo 'untouched')"
  echo "power           : $(vcgencmd get_throttled) (0x0 = no undervoltage since boot); supply $(vcgencmd get_config usb_max_current_enable 2>/dev/null)"
  echo "journal         : $(journalctl --disk-usage 2>/dev/null | sed 's/.*take up //')"
  echo "journal to card : every $(grep -hs '^SyncIntervalSec=' "$JD" | cut -d= -f2 || true)$([ -f "$JD" ] && grep -q '^SyncIntervalSec=' "$JD" || echo '5 min (the default)')"
  echo "kernel crash    : $([ "$($SYSCTL -n kernel.panic 2>/dev/null)" -gt 0 ] 2>/dev/null && echo "restart after $($SYSCTL -n kernel.panic) s" || echo 'waits for the watchdog')"
  echo "watchdog        : $(systemctl show -p RuntimeWatchdogUSec --value 2>/dev/null) (resets a frozen Pi)"
  M="$(systemctl is-enabled man-db.timer 2>/dev/null || true)"
  echo "man-page rebuild: ${M:-not installed}"
  echo "sign-in check   : $([ -f "$WQ" ] && echo 'quiet unless something happens' || echo 'logs every run')"
  echo "black box       : $(systemctl is-active anve-blackbox.service 2>/dev/null || true)$([ -s /var/lib/anve-device/blackbox ] && echo "; now $(cut -d' ' -f3- /var/lib/anve-device/blackbox)")"
  [ -s /var/lib/anve-device/blackbox.history ] && echo "last restart    : $(tail -1 /var/lib/anve-device/blackbox.history)"
  echo "boot            : $(systemd-analyze 2>/dev/null | head -1 | sed 's/Startup finished in //')"
}

if [ "${1:-}" = "--status" ]; then status; exit 0; fi

if [ "${1:-}" = "--undo" ]; then
  sudo rm -f "$RT" "$PS" "$JD" "$SC" "$WQ"
  sudo rmdir "$(dirname "$WQ")" 2>/dev/null || true
  # the journal keeps its files across restarts, as before this script's newer version
  printf '# ANVE device: keep logs across reboots so a crash can be diagnosed\n[Journal]\nStorage=persistent\nSystemMaxUse=200M\n' | sudo tee "$JD_OLD" >/dev/null
  sudo systemctl restart systemd-journald
  sudo "$SYSCTL" -q -w kernel.panic=0 >/dev/null 2>&1 || true
  sudo systemctl enable --now man-db.timer >/dev/null 2>&1 || true
  sudo systemctl disable --now anve-blackbox.service >/dev/null 2>&1 || true
  sudo rm -f "$BB"
  sudo sed -i "/^$MARK\$/d; /^arm_freq=1800\$/d" "$CFG"
  for s in $UNUSED; do
    systemctl list-unit-files "$s" >/dev/null 2>&1 && sudo systemctl unmask "$s" >/dev/null 2>&1 || true
  done
  sudo systemctl enable --now cups.service cups-browsed.service rpcbind.socket >/dev/null 2>&1 || true
  sudo systemctl daemon-reload
  sudo nmcli general reload conf >/dev/null 2>&1 || true
  sudo "$IW" dev wlan0 set power_save on 2>/dev/null || true
  echo "undone. Reboot for the sound priority and the CPU cap to go back."
  exit 0
fi

# 1. real-time priority for the user's PipeWire (needs a reboot: the limits are read
#    when the user's session starts)
sudo install -d -m 755 "$(dirname "$RT")"
sudo tee "$RT" >/dev/null <<'EOF'
# ANVE speaker (tools/optimise_pi.sh): let PipeWire run its audio threads with
# real-time priority. PipeWire asks for priority 88 by itself when it is allowed to.
[Service]
LimitRTPRIO=95
LimitNICE=-19
LimitMEMLOCK=268435456
EOF

# 2. Wi-Fi power saving off: now (no reconnect) and at every boot
sudo tee "$PS" >/dev/null <<'EOF'
# ANVE speaker (tools/optimise_pi.sh): 2 = power saving off
[connection]
wifi.powersave = 2
EOF
sudo nmcli general reload conf >/dev/null 2>&1 || true
sudo "$IW" dev wlan0 set power_save off 2>/dev/null || true

# 3. services nobody uses on a speaker
for s in $UNUSED; do
  if systemctl list-unit-files "$s" 2>/dev/null | grep -q "$s"; then
    sudo systemctl disable --now "$s" >/dev/null 2>&1 || true
  fi
done

# (an earlier version of this script added a CPU cap to config.txt: take it out again)
if grep -qF "$MARK" "$CFG"; then
  sudo sed -i "/^$MARK\$/d; /^arm_freq=1800\$/d" "$CFG"
fi

# 4. journal size
sudo install -d -m 755 "$(dirname "$JD")"
printf '# ANVE speaker (tools/optimise_pi.sh): kept across restarts, at most 200 MB, and on\n# the card within 15 s, so the last lines before a freeze or a power cut survive\n[Journal]\nStorage=persistent\nSystemMaxUse=200M\nSyncIntervalSec=15s\n' | sudo tee "$JD" >/dev/null
sudo rm -f "$JD_OLD"                       # its two settings are in the file above now
sudo systemctl restart systemd-journald

# 5. a kernel crash restarts the Pi after 5 s (now, and at every boot)
printf '# ANVE speaker (tools/optimise_pi.sh)\nkernel.panic = 5\n' | sudo tee "$SC" >/dev/null
sudo "$SYSCTL" -q -w kernel.panic=5 >/dev/null

# 6. no daily rebuild of the manual-page index
sudo systemctl disable --now man-db.timer >/dev/null 2>&1 || true

# 7. the Wi-Fi sign-in check says nothing when there is nothing to say (the script marks
#    its own noteworthy lines as "notice" or "warning", so those still show)
if [ -f /etc/systemd/system/anve-wifi-login.service ]; then
  sudo install -d -m 755 "$(dirname "$WQ")"
  printf '# ANVE speaker (tools/optimise_pi.sh)\n[Service]\nLogLevelMax=notice\n' | sudo tee "$WQ" >/dev/null
fi

# 8. the black box
sed -e "s|@HOME@|$(dirname "$HERE")|g" "$HERE/systemd/anve-blackbox.service" | sudo tee "$BB" >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable anve-blackbox.service >/dev/null 2>&1
sudo systemctl restart anve-blackbox.service

sudo systemctl daemon-reload
status
echo
if ps -eLo cls,comm | awk '$2 ~ /data-loop/ && $1 == "FF" {n++} END {exit !n}'; then
  echo "Applied. Everything above is in effect now; nothing needs a restart."
else
  echo "Applied. All of it is in effect now except the sound priority, which starts with the Pi's next restart."
fi
