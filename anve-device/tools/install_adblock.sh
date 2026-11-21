#!/usr/bin/env bash
# Install (or update) an ad blocker in the speaker's OWN browser only.
#
#   ~/anve-device/tools/install_adblock.sh
#
# It downloads the latest uBlock Origin Lite release (the Manifest V3 version, the one
# current Chromium accepts) from its official repository,
# https://github.com/uBlockOrigin/uBOL-home, and unpacks it into
# ~/.anve-browser-ext/ublock-origin-lite. The speaker's browser loads every extension
# folder it finds under ~/.anve-browser-ext; your normal Chromium is not touched.
#
# To remove it: rm -rf ~/.anve-browser-ext/ublock-origin-lite, then restart the service.
#
# Note: YouTube's terms do not allow ad blocking, and YouTube sometimes shows a warning
# or stops playback when it detects one. In its default "basic" mode this extension
# blocks ad and tracker requests but may not remove every YouTube video ad.
set -euo pipefail
DEST="$HOME/.anve-browser-ext/ublock-origin-lite"
API=https://api.github.com/repos/uBlockOrigin/uBOL-home/releases/latest
URL="$(curl -fsSL -m 20 "$API" | python3 -c 'import json,sys
for a in json.load(sys.stdin)["assets"]:
    if a["name"].endswith(".chromium.zip"): print(a["browser_download_url"]); break')"
case "$URL" in https://github.com/uBlockOrigin/uBOL-home/releases/download/*) ;; *) echo "unexpected download address: $URL" >&2; exit 1;; esac
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
echo "downloading $(basename "$URL")"
curl -fsSL -m 120 -o "$TMP/ubol.zip" "$URL"
mkdir -p "$TMP/x" && (cd "$TMP/x" && python3 -m zipfile -e "$TMP/ubol.zip" .)
test -f "$TMP/x/manifest.json" || { echo "the archive has no manifest.json" >&2; exit 1; }
rm -rf "$DEST"; mkdir -p "$(dirname "$DEST")"; mv "$TMP/x" "$DEST"
python3 -c 'import json,sys; m=json.load(open(sys.argv[1])); print("installed:", m["name"], m["version"], "(manifest v%s)" % m["manifest_version"])' "$DEST/manifest.json"
echo "size: $(du -sh "$DEST" | cut -f1). It loads the next time the speaker's browser starts."
