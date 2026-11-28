#!/bin/bash
# 10 beeps; each waits until the speaker is idle, then leaves 6 s to say the phrase
: > /tmp/beeps.log
for i in $(seq 1 10); do
  for k in $(seq 1 40); do
    curl -s -m 2 http://127.0.0.1:8739/api/state | grep -q "\"state\":\"idle\"" && break; sleep 1
  done
  sleep 1.5
  pw-play "$(dirname "$0")/beep.wav"
  echo "$i $(date +%H:%M:%S)" >> /tmp/beeps.log
  sleep 6
done
pw-play "$(dirname "$0")/beep.wav"; sleep 0.3; pw-play "$(dirname "$0")/beep.wav"
echo done >> /tmp/beeps.log
