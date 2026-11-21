#!/usr/bin/env bash
# The independent way back after a hotspot test (started as a timer by hotspot_trial.sh,
# as root). If the box is still on its setup hotspot, or on no Wi-Fi at all, take the
# hotspot down and rejoin the Wi-Fi named in $1. It tries for about three minutes.
# It does nothing when the box is already back on a Wi-Fi.
#
# "Set up again" (hotspot_trial.sh --apply) deletes the Wi-Fi profile the box was on when
# someone sends another Wi-Fi. The setup program keeps a root-only copy for that time
# (/etc/anve-device/wifi.before-resetup) and puts it back itself; if that program has a
# fault, this script does: at once when the profile is gone, and from the fourth try on
# even over a profile that is there but does not connect.
PREV="${1:-}"
KEPT=/etc/anve-device/wifi.before-resetup
PROFILE=/etc/NetworkManager/system-connections/anve-wifi.nmconnection
put_back() {
  [ -s "$KEPT" ] || return 0
  if [ "$1" -ge 4 ] || ! nmcli -t -f NAME con show | grep -qx "anve-wifi"; then
    install -m 600 -o root -g root "$KEPT" "$PROFILE" && nmcli con load "$PROFILE" >/dev/null 2>&1 \
      && echo "try $1: put the kept Wi-Fi profile back"
  fi
}
for i in 1 2 3 4 5 6 7 8; do
  CON="$(nmcli -t -f GENERAL.CONNECTION dev show wlan0 | cut -d: -f2-)"
  STATE="$(nmcli -t -f GENERAL.STATE dev show wlan0 | cut -d: -f2-)"
  if [ "$CON" != "anve-setup" ] && [ "${STATE%% *}" = "100" ]; then
    echo "on Wi-Fi \"$CON\": nothing to do"
    exit 0
  fi
  echo "try $i: on \"$CON\" (state $STATE): taking the hotspot down and rejoining \"$PREV\""
  nmcli con down id anve-setup >/dev/null 2>&1
  nmcli con delete id anve-setup >/dev/null 2>&1
  put_back "$i"
  { [ -n "$PREV" ] && nmcli -w 30 con up id "$PREV"; } || nmcli -w 30 dev connect wlan0
  sleep 15
done
echo "could not get back on Wi-Fi"
exit 1
