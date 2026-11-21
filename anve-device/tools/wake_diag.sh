#!/usr/bin/env bash
# A look at what the wake-word recogniser really hears, for a limited time.
#
#   tools/wake_diag.sh on [minutes]   for that long (default 15, at most 60): every piece of
#                                     sound the recogniser decodes is kept as a small sound
#                                     file in recordings/live/, and what it heard is written
#                                     to the journal. It switches itself off.
#   tools/wake_diag.sh off            stop now
#   tools/wake_diag.sh show           what was heard (newest last)
#   tools/wake_diag.sh clean          delete the kept sound files
#
# Outside such a window the speaker keeps no sound and logs no words. The sound files
# never leave this Pi.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
FLAG=/run/anve/diagnose-until
case "${1:-show}" in
  on)
    mins="${2:-15}"; [ "$mins" -gt 60 ] && mins=60
    echo $(( $(date +%s) + mins * 60 )) > "$FLAG"
    echo "Recording what the recogniser hears for $mins minutes (until $(date -d "+$mins min" +%H:%M))." ;;
  off)
    rm -f "$FLAG"; echo "Off." ;;
  clean)
    rm -f "$HERE"/recordings/live/*.wav; echo "Kept sound files deleted." ;;
  show)
    journalctl -u anve-device --no-pager -o short --since "-90min" | grep -E "anve.device: (diag |wake word)" | cut -c8-15,50- | tail -60 ;;
  *) echo "usage: wake_diag.sh on [minutes] | off | show | clean"; exit 2 ;;
esac
