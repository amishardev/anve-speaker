#!/usr/bin/env bash
# Install LUMINA as a service that starts with the Pi.   ./install.sh
# It does NOT switch the real LEDs on: that is `./start.sh --hardware`, once the strip is
# wired and powered the way README.md says.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DIR="$(dirname "$HERE")"
USER_NAME="${SUDO_USER:-$(id -un)}"
USER_ID="$(id -u "$USER_NAME")"
PYTHON="$DIR/.venv/bin/python"
[ -x "$PYTHON" ] || PYTHON="$(command -v python3)"
run_as() { if [ "$(id -un)" = "$USER_NAME" ]; then "$@"; else sudo -u "$USER_NAME" "$@"; fi; }

echo "LUMINA: installing for user $USER_NAME, from $DIR, with $PYTHON"
"$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' || { echo "Python 3.9 or newer is needed."; exit 1; }
"$PYTHON" -c 'import spidev' 2>/dev/null || echo "  note: the Python module 'spidev' is missing; the real strip needs it (sudo apt install python3-spidev)"
[ -e /dev/spidev0.0 ] || echo "  note: /dev/spidev0.0 does not exist; switch SPI on (sudo raspi-config, Interface Options) before using the real strip"
id -nG "$USER_NAME" | grep -qw spi || echo "  note: $USER_NAME is not in the group 'spi' (sudo usermod -aG spi $USER_NAME)"

# settings and the dashboard key live with the user, outside the program's folder
cd "$DIR"
run_as "$PYTHON" -m lumina --key >/dev/null
if [ -d /var/lib/anve-device ]; then
  # the ANVE speaker's "has been set up" marker: until it exists the strip shows "setup needed"
  run_as "$PYTHON" -m lumina --set setup_marker=/var/lib/anve-device/provisioned >/dev/null
fi

sed -e "s|@USER@|$USER_NAME|g" -e "s|@UID@|$USER_ID|g" -e "s|@DIR@|$DIR|g" -e "s|@PYTHON@|$PYTHON|g" \
  "$HERE/lumina.service" | sudo tee /etc/systemd/system/lumina.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now lumina.service
sleep 2
"$HERE/status.sh"
