# wake_head.onnx: the trained "Hey ANVE" / "ANVE" model (23 Nov 2026)

A small convolution net (1.1 MB) over the Whisper tiny.en encoder vectors the speaker
already computes (`wake.Spotter.embed`). Output: how sure it is that a clip STARTS with
"Hey ANVE" or "ANVE". Made on the owner's laptop with `tools/wake_train/` (gen_tts.py,
build_data.py, train.py, export.py); runs on the Pi with ONNX Runtime (`wake.Head`).

## What it learned from, and the licences

| Source | Used for | Licence (read at the source, 23 Nov 2026) |
|---|---|---|
| Whisper tiny.en weights, faster-whisper | the encoder | MIT |
| Kokoro-82M v1.0, English voices only (af_, am_, bf_, bm_; 28 voices) | the phrase in 8 spellings, and 54 near-misses ("Hey Andy", "Hey Annie", "Stop.") | Apache-2.0 (huggingface.co/hexgrad/Kokoro-82M). The CC BY Japanese and French voices were not used. |
| kokoro-onnx | running Kokoro | MIT |
| LibriSpeech dev-clean | other speech (negatives) and the "request" spoken after the phrase | CC BY 4.0 (openslr.org/12). Credit: Panayotov, Chen, Povey, Khudanpur, "LibriSpeech: an ASR corpus based on public domain audio books", ICASSP 2015. |
| The owner's own recordings | 16 phrase clips for training, 17 held out; room talk and music bursts as negatives | the owner's; never left the Pi and the laptop |

No openWakeWord or microWakeWord model or data is in it.

## Measured on clips it never saw (limit 0.8)

| Held-out clips | This model | Sample matching (the 23 Nov afternoon method) |
|---|---|---|
| Owner "Hey ANVE", second recording round | 12 of 12 | 11 of 12 |
| Owner bare "ANVE" | 5 of 5 | 5 of 5 |
| The phrase in 4 voices it never heard | 369 of 384 | 20 of 384 |
| Near-misses in those 4 voices (must not wake) | 3 of 398 woke | 2 of 398 |
| Audiobook speech, 6 unseen readers | 1 of 1375 woke | not run |
| Room talk, music bursts, "stop" | 0 of 69 woke | 0 of 69 |

Not measured: real people other than the owner; the phrase over music (the model was not
trained on clips with music under the voice, and is not used on the music path).
Laptop and Pi give the same encoder vectors (cosine 0.999 per frame).
