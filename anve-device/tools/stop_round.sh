#!/bin/bash
# "ANVE stop" end to end: music on, a beep, 12 s to say it; did the music stop?
S() { curl -s -m 3 http://127.0.0.1:8739/api/state; }
M() { curl -s -m 10 -X POST -H "X-Anve-Panel: 1" -H "Content-Type: application/json" -d "{\"do\":\"$1\"}" http://127.0.0.1:8739/api/media >/dev/null; }
: > /tmp/stoptest.log
for i in $(seq 1 ${1:-8}); do
  S | grep -q "\"paused\":true" && M resume
  for k in $(seq 1 30); do S | grep -q "\"state\":\"idle\"" && S | grep -q "\"paused\":false,\"ducked\":false" && break; sleep 1; done
  sleep 5
  pw-play "$(dirname "$0")/beep.wav"; t=$(date +%H:%M:%S); r=no
  for k in $(seq 1 12); do sleep 1; S | grep -q "\"paused\":true" && { r="stopped after ${k}s"; break; }; done
  echo "$i $t $r" >> /tmp/stoptest.log
done
pw-play "$(dirname "$0")/beep.wav"; sleep 0.3; pw-play "$(dirname "$0")/beep.wav"
echo done >> /tmp/stoptest.log
