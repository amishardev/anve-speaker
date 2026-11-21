#!/usr/bin/env bash
# Join this box to the ANVE Box cloud (github.com/ANVEAI/anve-box).   tools/install_boxlink.sh
#
#   1. Gives the box its identity, once and for life (the team's factory step):
#      /etc/anvebox/identity.json, SECRET, root-only. It never leaves the box.
#   2. Writes the public record next to this repo: ~/anvebox-record.json. That file is safe
#      to share; whoever runs the cloud adds it there ("register-box").
#   3. Installs and starts the link (anve-boxlink.service). Until the cloud knows the
#      box, the link is turned away and keeps trying: that is expected.
#
# Running it again changes nothing that exists: the identity is never made twice.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
USER_NAME="${SUDO_USER:-$(id -un)}"
USER_ID="$(id -u "$USER_NAME")"
HOME_DIR="$(dirname "$HERE")"
PY="$HERE/.venv/bin/python"
ID=/etc/anvebox/identity.json
RECORD="$HOME_DIR/anvebox-record.json"

sudo install -d -m 700 -o root -g root /etc/anvebox
if sudo test -f "$ID"; then
  echo "This box already has its identity; it is kept."
else
  # the record is written beside the identity first (root may not write into another
  # user's file in /tmp), then copied out: it is public
  sudo env PYTHONPATH="$HERE" "$PY" "$HERE/tools/anvebox_factory.py" new --out "$ID" --record /etc/anvebox/record.json
  sudo chown root:root "$ID"; sudo chmod 600 "$ID"
  sudo cat /etc/anvebox/record.json > "$RECORD"
fi
if [ ! -s "$RECORD" ]; then
  # the record can always be made again from the identity: it holds nothing secret
  sudo env PYTHONPATH="$HERE" "$PY" -c "
import json, sys
from anve_device.boxlink import load_identity
from pathlib import Path
print(json.dumps(load_identity(Path('$ID')).registration_record(), indent=2))" > "$RECORD"
fi
chmod 644 "$RECORD"

sed -e "s|@USER@|$USER_NAME|g" -e "s|@UID@|$USER_ID|g" -e "s|@HOME@|$HOME_DIR|g" \
  "$HERE/systemd/anve-boxlink.service" | sudo tee /etc/systemd/system/anve-boxlink.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable anve-boxlink.service >/dev/null 2>&1
sudo systemctl restart anve-boxlink.service
sleep 4
echo "link service : $(systemctl is-active anve-boxlink.service)"
echo "public record: $RECORD   (safe to share: send it to whoever runs the cloud)"
journalctl -u anve-boxlink -n 4 --no-pager -o cat | sed 's/^/  /'
