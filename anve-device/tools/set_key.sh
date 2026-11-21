#!/usr/bin/env bash
# Store the ANVE device key on this Pi, in a root-only file outside the project.
#
#   ~/anve-device/tools/set_key.sh
#
# It asks for the key (typing is hidden), checks its shape, and writes it to
# /etc/anve-device/key (owner root, mode 600). The key is never shown, logged or
# written anywhere else. Create the key in ANVE: Assistant page > Devices.
set -euo pipefail
DIR=/etc/anve-device
FILE=$DIR/key

printf 'Paste the device key (it will not be shown), then press Enter: '
IFS= read -rs KEY
printf '\n'
KEY="${KEY//[$'\r\n\t ']/}"
if [[ "$KEY" != anve_dev_* ]]; then
  echo "That does not look like a device key: it must start with anve_dev_ . Nothing was saved." >&2
  exit 1
fi
if (( ${#KEY} < 40 || ${#KEY} > 200 )); then
  echo "That key is ${#KEY} characters long, which is not a valid length. Nothing was saved." >&2
  exit 1
fi
sudo install -d -m 700 -o root -g root "$DIR"
printf '%s' "$KEY" | sudo install -m 600 -o root -g root /dev/stdin "$FILE"
unset KEY
echo "Saved: $FILE ($(sudo stat -c '%U, mode %a, %s bytes' "$FILE"))."
if systemctl list-unit-files anve-device.service >/dev/null 2>&1 && \
   systemctl is-enabled anve-device.service >/dev/null 2>&1; then
  sudo systemctl restart anve-device.service && echo "anve-device service restarted."
fi
