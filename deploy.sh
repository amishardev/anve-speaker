#!/usr/bin/env bash
# Copy the local anve-device/ tree to ~/anve-device on the Pi (the Pi's .git, .venv and
# local config are left alone). Usage: ./deploy.sh ["commit message"]
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# By name: the Pi's address on the campus Wi-Fi changes (it did on 19 Nov 2026). Its host
# key is stored under the first address it had, hence HostKeyAlias.
PI="${ANVE_PI:-pi@anve-xxxx.local}"
SSH=(ssh -i "$HOME/.ssh/anve_pi_ed25519" -o BatchMode=yes -o IdentitiesOnly=yes -o ConnectTimeout=10 -o HostKeyAlias="${ANVE_PI_HOSTKEY:-anve-pi}" "$PI")
tar -C "$HERE/anve-device" --exclude=.git --exclude=__pycache__ --exclude=.venv -cf - . \
  | "${SSH[@]}" 'mkdir -p ~/anve-device && tar -C ~/anve-device -xf - && cd ~/anve-device && find . -name "*.sh" -exec chmod +x {} + ; echo deployed: $(find . -path ./.git -prune -o -path ./.venv -prune -o -type f -print | wc -l) files'
if [ "${1:-}" != "" ]; then
  "${SSH[@]}" "cd ~/anve-device && git add -A && git commit -q -m \"$1\" -m \"Co-Authored-By: Claude Fable 5.2 <noreply@anthropic.com>\" && git log --oneline -1"
fi
