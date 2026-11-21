#!/usr/bin/env bash
# Stop LUMINA: the strip fades out and every LED is switched off.   ./stop.sh
set -euo pipefail
sudo systemctl stop lumina.service
echo "LUMINA stopped; the LEDs are off. It starts again at the next boot (./uninstall.sh removes it)."
