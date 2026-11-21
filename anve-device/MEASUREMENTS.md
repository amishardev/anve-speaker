# Measurements, 16 Nov 2026

Everything here was measured on this Pi, in one room, with the iBall 360Meet X on USB
and the Portronics SoundDrum 1 on Bluetooth. Nothing was measured against the real ANVE
server yet. Recordings are in `recordings/` on the Pi (not in git).

## Noise suppression: same take, four ways

One 32-second take (`diag_raw.wav`, and `diag_clean.wav` recorded at the same moment
through the echo canceller). The owner says "Hey ANVE, what's the weather today" five
times in the first 12 s; after that only the room is heard. "Speech" and "room" are
levels in dBFS; "gap" is the difference. Transcribed on the Pi with Whisper.

| Version | Speech | Room | Gap | Phrases ending "weather today" (base.en / tiny.en) | Words Whisper invented in the room-only part (base / tiny) |
|---|---|---|---|---|---|
| Raw microphone | -13.1 | -24.4 | 11.3 | 6 / 0 | 0 / 0 |
| WebRTC NS (inside the echo canceller, live) | -13.4 | -26.3 | 12.9 | 5 / 0 | 0 / 0 |
| RNNoise on the raw take (ffmpeg `arnndn`, model `sh.rnnn`) | -14.7 | -39.6 | 24.9 | 4 / 4 | 25 / 28 |
| WebRTC NS, then RNNoise | -15.8 | -47.8 | 31.9 | 4 / 0 | 25 / 0 |
| FFT denoiser on raw (ffmpeg `afftdn`) | -13.1 | -24.7 | 11.6 | 0 / 0 | 11 / 0 |

Reading it:
- The raw count of 6 for 5 phrases is Whisper repeating itself; treat these counts as
  rough.
- RNNoise removes far more of this room's sound, but one phrase is lost and what it
  leaves behind makes Whisper invent words where nobody spoke.
- WebRTC NS removes little of this particular sound (2 dB) but a lot of steady noise:
  the idle room measured -34 dBFS raw and -52 dBFS after it. It adds no delay of its
  own and no false words.
- **Chosen: WebRTC NS**, inside the echo canceller. RNNoise cost 0.86 s of CPU for 32 s
  of audio, so CPU is not what rules it out; the false words are.
- DeepFilterNet was not tested (it needs PyTorch or a prebuilt binary).
- This is one take from one position. The 1 m / 3 m proof (`tools/proof_record.py`) is
  still to be recorded.

## Echo

Speech sample (espeak-ng, 19.5 s) played through the echo-cancel sink; raw microphone
and cancelled output recorded together (`tools/erle_test.py`).

| Speaker volume | Raw echo at the mic | After cancelling | Reduction | Mic samples clipped |
|---|---|---|---|---|
| 40 % | -12 dBFS | -29 dBFS (the room's level) | at least 17 dB | 0.04 % |
| 50 % | -9 dBFS | -17 dBFS | 7 to 10 dB | 0.3 % |
| 60 % | -8 dBFS | -16 to -24 dBFS | 8 to 16 dB | 0.6 to 0.8 % |

- First 4 s of the first reply: 3 to 8 dB while the canceller converges.
- Bluetooth output delay: 195 to 213 ms direct; 270 to 316 ms through the canceller,
  moving about 27 ms within one reply.
- Pre-delaying the reference (`buffer.play_delay`) made it worse.
- The microphone's ALSA gain control changes nothing.

## Delay

- Echo canceller + noise suppression: the cleaned signal lags the raw one by about
  20 ms (two recorders started together, so +/- 20 ms).
- Framing adds 40 ms (the server counts 40 ms per frame); Silero and the gain stage
  cost 0.7 ms per frame.
- Wake word: about 1.0 s for the main decode and 0.35 s for the second check, at
  1.8 GHz on 2 threads. The decision is usually known while the request is still being
  spoken, because a segment is checked once it is 1.3 s long.

## Wake word

- On the owner's recorded phrases, replayed: 3 of 4 segments wake (two phrases ran
  together into one segment).
- Whisper window against accuracy, same clips: 30 s window 10 of 16 recognised (encoder
  765 ms), 10 s window 6 of 16 (131 ms), 6 s window 2 of 16 (64 ms).
- Live: no usable rate yet. In one 3.6-minute window with nobody speaking: 0 decodes,
  0 false wakes.

## Power

| CPU cap | Threads | Lowest 5 V rail | Under-voltage |
|---|---|---|---|
| 1.5 to 2.4 GHz | 1 or 2 | 4.83 V or higher | none |
| 1.5 to 2.1 GHz | 4 | 4.80 V or higher | none |
| 2.4 GHz | 4 | 4.67 V | yes |

The Pi negotiates 3 A from the charger. It froze once under Whisper on 4 threads before
the cap was in place.

## Proof recording attempt, 23:02

`tools/proof_record.py` ran two 32-second takes, but nobody read the script: the "you
alone" part of both takes is at room level (-36 and -31 dBFS raw) and the raw transcripts
contain only the assistant's own sample speech. So these takes say nothing about a voice
at 1 m or 3 m. They do measure the echo once more, at 40 % volume:

| Take | Raw mic while the assistant talks | Cleaned | Reduction | Transcript of the cleaned take |
|---|---|---|---|---|
| first | -14.1 dBFS | -36.4 dBFS | 22 dB | "so people" (the assistant's words are gone) |
| second | -13.7 dBFS | -25.8 dBFS | 12 dB | invented words, none of the assistant's |

In the first take the speech detector kept the uplink gate closed for the whole reply
(-65 dBFS would have been sent). In the second it opened on the louder residue
(-27.5 dBFS). The spread between the two takes (22 dB and 12 dB) is the Bluetooth path
behaving differently from one reply to the next.

## Proof recording, second attempt, 23:05

The owner read the script this time, but only after the assistant's sample had finished:
each take has about 6 s of the owner's voice, at its end. So there is still no take of
the owner talking OVER the assistant, and no word error rate against the full script.

| | 1 m take | 3 m take |
|---|---|---|
| Owner's voice, raw microphone | -24.0 dBFS | -22.7 dBFS |
| Owner's voice, as sent to ANVE | -20.5 dBFS | -22.7 dBFS |
| Assistant talking, raw microphone | -13.9 dBFS | -13.2 dBFS |
| Assistant talking, cleaned | -30.7 dBFS | -19.7 dBFS |
| Room before anyone speaks, raw / cleaned | -38.0 / -53.2 dBFS | -36.8 / -51.0 dBFS |
| Speech detector on the owner's voice (median probability) | 0.62 | 0.32 |

Transcripts (Whisper base.en):
- Raw 1 m: the assistant's whole sample, then "The quarterly review is on tikutahastay
  at 3 in the afternoon. Please book the-".
- Cleaned 1 m: only the owner: "quarterly review is on Qthal's day. I clean the
  afternoon. Please book the". The assistant's words are gone; "Thursday at three" is
  misheard in both versions.
- Raw 3 m: the assistant's sample, then "The short summary to everyone who attended and
  followed for the next Monday morning at 10."
- Cleaned 3 m: only the owner: "... summary to everyone who attended and follow further
  next Monday morning at 10."

What changed because of it: at 3 m the speech detector is unsure (median 0.32), and the
uplink gate was cutting the start of phrases ("The quarterly" reached Whisper as
"Early"). The uplink now runs 80 ms late so the gate can open with hindsight, opens at a
probability of 0.15 and holds for 0.6 s. Replayed on these takes, the 3 m phrase goes
out whole. Cost: 80 ms more delay on the uplink.

## Live on app.anveai.com, 23:38 to 23:43

Engine reported by the server: `live`, cloud (Gemini Live on Vertex AI, us-central1),
`mode=wake`, Opus, `voice_confirm` false (room device).

| What | Result |
|---|---|
| Synthetic voice, one full turn | wake in 1.3 s, `web_answer` ok, reply spoken, follow-up window 17.6 s; "Sambalpur" heard as "Mumbai" |
| Owner's voice, wake word | 2 of 3 attempts (the missed one was heard as "Hey, I'm B") |
| Owner's voice, turns answered | 6 of 6 once awake: 2 after a wake word, 4 as follow-ups without it |
| Requests as the server heard them | "How is the weather in Sambalpur?", "How many followers does Nicky Romero has?", "How's the weather in Kanpur now?" and three more |
| Tool calls | 4 `web_answer`, all succeeded |
| Server latency, end of speech to first audio | about 1.0 s for a plain reply; 4.9 to 7.1 s when a web search runs |
| Wake decision time | 1.2 to 1.5 s |
| Spoken "stop" | not attempted in this window |
| Under-voltage during the session | none |

A bug found here and fixed: after the echo canceller reloaded, PipeWire had re-linked the
device's capture stream to the Bluetooth speaker's own hands-free input, so the service
looked healthy and heard nothing. Both streams now refuse to be moved
(`node.dont-reconnect`), are reopened when the canceller reloads, and a watchdog checks
the capture's real source every 10 s.
