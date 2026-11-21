#!/usr/bin/env bash
# Start LUMINA.
#   ./start.sh               start the service as it is set up
#   ./start.sh --hardware    from now on, drive the real LED strip (asks first)
#   ./start.sh --simulate    from now on, keep the LEDs off and only show the preview
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DIR="$(dirname "$HERE")"
PYTHON="$DIR/.venv/bin/python"
[ -x "$PYTHON" ] || PYTHON="$(command -v python3)"
cd "$DIR"
case "${1:-}" in
  --hardware)
    cat <<'EOF'
This switches the real LED strip on. Before you say yes, check:
  - the strip has its OWN 5 V supply (not the Pi's 5 V pin), and the grounds are joined
  - data goes from GPIO10 (pin 19) through the level shifter and the 330 ohm resistor
  - the 1000 uF capacitor sits across the strip's supply
It starts at 5 % brightness; the limit is 20 % and about 1.5 A for the whole strip.
EOF
    read -r -p "Switch the LEDs on? (yes/no) " answer
    [ "$answer" = "yes" ] || { echo "Left as it was."; exit 0; }
    # start low: 5 % (raise it in the dashboard once the strip and the Pi's supply look fine)
    "$PYTHON" -m lumina --set strip.driver=spi --set brightness=0.05
    echo "The LEDs start at 5 % brightness. Check 'vcgencmd get_throttled' while they are on." ;;
  --simulate)
    "$PYTHON" -m lumina --set strip.driver=simulator ;;
  "") ;;
  *) echo "usage: ./start.sh [--hardware | --simulate]"; exit 2 ;;
esac
if systemctl list-unit-files lumina.service >/dev/null 2>&1 && systemctl cat lumina.service >/dev/null 2>&1; then
  sudo systemctl restart lumina.service
  sleep 2
  "$HERE/status.sh"
else
  echo "The service is not installed (./install.sh). Running here instead; Ctrl-C stops it."
  exec "$PYTHON" -m lumina --preview
fi
