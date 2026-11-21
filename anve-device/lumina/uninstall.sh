#!/usr/bin/env bash
# Remove the LUMINA service. Your settings are kept.   ./uninstall.sh
set -euo pipefail
sudo systemctl disable --now lumina.service 2>/dev/null || true
sudo rm -f /etc/systemd/system/lumina.service
sudo systemctl daemon-reload
echo "LUMINA is removed and the LEDs are off."
echo "Your settings are still in ~/.config/lumina (delete that folder to forget them)."
echo "The assistant keeps working; it only stops being shown on the strip."
