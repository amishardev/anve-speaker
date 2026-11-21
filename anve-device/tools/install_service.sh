#!/usr/bin/env bash
# Install (or update) the ANVE speaker as a systemd service for the current user.
#
#   ~/anve-device/tools/install_service.sh
#
# It does not start the service unless the device key exists (tools/set_key.sh).
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
U="$(id -un)"; N="$(id -u)"; H="$HOME"

[ -f "$HERE/config.toml" ] || cp "$HERE/config.example.toml" "$HERE/config.toml"

# the user's PipeWire session must exist at boot, with nobody logged in
sudo loginctl enable-linger "$U"

sed -e "s|@USER@|$U|g" -e "s|@UID@|$N|g" -e "s|@HOME@|$H|g" \
  "$HERE/systemd/anve-device.service" | sudo tee /etc/systemd/system/anve-device.service >/dev/null
sed -e "s|@USER@|$U|g" -e "s|@UID@|$N|g" -e "s|@HOME@|$H|g" \
  "$HERE/systemd/anve-setup.service" | sudo tee /etc/systemd/system/anve-setup.service >/dev/null
sed -e "s|@USER@|$U|g" -e "s|@UID@|$N|g" -e "s|@HOME@|$H|g" \
  "$HERE/systemd/anve-wifi-login.service" | sudo tee /etc/systemd/system/anve-wifi-login.service >/dev/null
sudo install -m 644 "$HERE/systemd/anve-wifi-login.timer" /etc/systemd/system/anve-wifi-login.timer
sed -e "s|@USER@|$U|g" -e "s|@UID@|$N|g" -e "s|@HOME@|$H|g"   "$HERE/systemd/anve-provision.service" | sudo tee /etc/systemd/system/anve-provision.service >/dev/null

# --- what the phone app needs ---
# where the box keeps its name, room and "has been set up" mark (not secret)
sudo install -d -m 755 -o "$U" -g "$(id -gn)" /var/lib/anve-device
sudo install -d -m 700 -o root -g root /etc/anve-device
# the secret shared with paired phones: made once, root-only, never shown
sudo test -s /etc/anve-device/app-token || \
  head -c 32 /dev/urandom | od -An -tx1 | tr -d '[:space:]' | \
  sudo install -m 600 -o root -g root /dev/stdin /etc/anve-device/app-token
# A box that already has its key was set up by hand, before the app existed: mark it as
# set up, so it never opens the setup hotspot by surprise.
if sudo test -f /etc/anve-device/key && [ ! -e /var/lib/anve-device/provisioned ]; then
  date '+%F %T (set up by hand)' > /var/lib/anve-device/provisioned
fi
# The box's name on the network: anve-xxxx.local (xxxx = end of the board's serial
# number), so the app finds it without anyone knowing an address.
ID="$(awk '/^Serial/{print tolower(substr($3, length($3)-3))}' /proc/cpuinfo)"
NAME="anve-${ID:-box}"
if [ "$(hostname)" != "$NAME" ]; then
  OLD="$(hostname)"
  [ -d /etc/cloud/cloud.cfg.d ] && echo 'preserve_hostname: true' | sudo tee /etc/cloud/cloud.cfg.d/99-anve-hostname.cfg >/dev/null
  if grep -q '^127\.0\.1\.1' /etc/hosts; then
    sudo sed -i "s/^127\.0\.1\.1.*/127.0.1.1 $NAME/" /etc/hosts
  else
    echo "127.0.1.1 $NAME" | sudo tee -a /etc/hosts >/dev/null
  fi
  sudo hostnamectl set-hostname "$NAME"
  echo "network name: $OLD.local -> $NAME.local (the address stays the same)"
fi
sudo install -d -m 755 /etc/avahi/services
sudo tee /etc/avahi/services/anve.service >/dev/null <<EOF
<?xml version="1.0" standalone='no'?>
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group>
  <name>ANVE $ID</name>
  <service>
    <type>_anve._tcp</type>
    <port>8738</port>
    <txt-record>id=$ID</txt-record>
  </service>
</service-group>
EOF
sudo install -m 644 "$HERE/systemd/anve-cpu-cap.service" /etc/systemd/system/anve-cpu-cap.service
sudo systemctl daemon-reload
sudo systemctl enable anve-cpu-cap.service anve-device.service anve-setup.service anve-wifi-login.timer anve-provision.service >/dev/null 2>&1
sudo systemctl start anve-wifi-login.timer
# `start`, not `restart`: this script is also run BY the setup page
sudo systemctl start anve-cpu-cap.service anve-setup.service
sudo systemctl restart anve-provision.service
echo "installed: anve-device.service (user $U, uid $N), linger $(loginctl show-user "$U" -p Linger --value)"
if sudo test -f /etc/anve-device/key; then
  sudo systemctl restart anve-device.service
  sleep 3
  systemctl --no-pager --lines=0 status anve-device.service | sed -n '1,4p'
else
  echo "No device key yet. Run tools/set_key.sh, and the service starts by itself."
fi
