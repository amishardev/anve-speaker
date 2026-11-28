# The ANVE speaker: the whole project in one place

A DIY smart speaker on a Raspberry Pi 5, built by Amish Sharma between 16 and 28 November
2026 with Claude as the engineer at the keyboard. It answers "Hey ANVE" out loud, plays
music, records meetings, drives a light strip, and is set up from a phone or a browser.

This page is the history and the state of the project: what was built, what was measured,
what works, what does not, and what was learned. The numbers come from the Pi itself and
from tests run during the work. Where something was never checked, it says so.

Other places to look:

| File | What it holds |
|---|---|
| [`../README.md`](../README.md) | The map of this repository |
| [`../anve-device/README.md`](../anve-device/README.md) | How the firmware is installed and run |
| [`../anve-device/MEASUREMENTS.md`](../anve-device/MEASUREMENTS.md) | Audio measurements from the first week |
| [`../anve-device/CAPABILITIES.md`](../anve-device/CAPABILITIES.md) | What the old platform answered, request by request |
| [`../anve-device/models/WAKE_MODEL.md`](../anve-device/models/WAKE_MODEL.md) | The trained wake model: data, licences, results |
| [`reports/meetings-on-box-2026-11-27.md`](reports/meetings-on-box-2026-11-27.md) | The report to the ANVE Box team on meeting recording |
| [`../ANVE-Speaker-Progress.pdf`](../ANVE-Speaker-Progress.pdf) | A one-page summary as of 21 November |

## 1. What it is

- **The brain** is not on the device. "Hey ANVE" is spotted on the Pi; the request then
  goes to a cloud voice model and the spoken answer comes back. Until 24 November that
  cloud was the ANVE platform (`app.anveai.com`, a device key). Since then it is the ANVE
  Box cloud's Gemini voice, proved with the box's own identity. The old platform can be
  switched back with one line of configuration.
- **Nothing leaves the device before the wake word.** Between talks there is no
  connection for sound at all on the new cloud.
- **The device does some things itself:** music from YouTube Music and free radio in its
  own browser, volume, stop, next, the time, what is playing, and its lights.

## 2. The hardware

| Part | What was used | What was learned |
|---|---|---|
| Computer | Raspberry Pi 5, 16 GB, Raspberry Pi OS (Debian 13) | Plenty of headroom; the limits were power and audio, never memory or CPU |
| Microphone | A USB conference microphone (Jieli "UACDemo", mono) | It levels itself and clips on loud sound; its gain control changes nothing |
| Loudspeaker | A Bluetooth speaker (Portronics SoundDrum 1) | About 0.2 to 0.3 s of delay that wanders; switches itself off when idle |
| Lights | A 60-LED WS2812B strip on SPI0 (GPIO10) | Driven directly at 3.3 V, no level shifter; must have its own charger |
| Power | First a phone charger, then the official 27 W supply | The phone charger caused most of the first week's trouble (section 6) |
| Buttons | Mute on GPIO17, action on GPIO27 (in the code) | Never confirmed as wired on this unit |

## 3. How the software is put together

Everything is in `anve-device/`. It is plain Python with few dependencies.

```
microphone -> PipeWire echo canceller (WebRTC) -> gain (dsp.py) -> speech detector (Silero)
                                                        |
                    +-----------------------------------+------------------+
                    |                                                      |
            wake word (wake.py)                                    meeting recorder
   text rules + the owner's recordings + a trained model            (meetings.py)
                    |
        a talk with the cloud (boxvoice.py or client.py), shaped by turn.py
                    |
     what was said -> nlu.py -> the speaker does it itself (skills.py), or the cloud answers
                    |
            playback.py -> echo canceller -> Bluetooth speaker        lights (lumina/)
```

| File | Job |
|---|---|
| `main.py` | The device: ties everything below together |
| `wake.py` | "Hey ANVE" on the device: Whisper tiny.en on speech segments, the owner's recordings matched by sound, a small trained model, and bursts cut out of music |
| `turn.py` | Which microphone frames go to the cloud and when, so a person has time to start and can pause |
| `boxvoice.py` | The ANVE Box cloud's voice: one connection per talk |
| `client.py` | The old ANVE platform's session socket |
| `nlu.py` | Everyday English and Hinglish to one device action, with context |
| `skills.py` | Music, radio, the speaker's own browser, volume |
| `meetings.py` | A meeting in the room, cut into 55 s pieces and sent to the cloud |
| `boxlink.py` | The box's link to the ANVE Box cloud (online, added to an account, "blink my box") |
| `provision.py`, `portal/` | First-time setup: a hotspot, a page that opens by itself, sealed settings |
| `appapi.py`, `pairing.py` | The phone app's sealed channel on the local network |
| `panel.py` | A control page on the Pi's loopback address |
| `setup_web.py` | A local page that checks and repairs the device |
| `lumina/` | The light strip: its own program, fed with the speaker's state |
| `anvebox_agent/` | The ANVE Box team's code, copied unchanged (see its `VENDORED.md`) |

`anve-app/` is "ANVE Home", the phone app (React Native / Expo): finds the box, sets it up,
shows what it is doing, and lists approvals from the ANVE platform.

## 4. The story, day by day

**16 November: first sound.** Key-based SSH, the first audio survey, the protocol client
against a mock server (21 of 21 checks), Whisper for the wake word, the echo canceller
loaded into PipeWire without restarting it. The planned USB sound card never appeared;
the Bluetooth speaker took its place. The owner stored the device key himself through a
local setup page, and at 23:38 the first full turn ran on the real server.

**17 November: music and manners.** Local skills from the cloud's transcript: "play ..."
in the speaker's own Chromium, free radio, pause, next, volume. Music dips and is
muffled while someone talks to the speaker. The listening chime the owner liked was kept;
a robotic "let me think" voice he disliked was replaced by a soft two-note pulse. The
campus Wi-Fi's sign-in page cut the internet at night; an automatic sign-in was built
that the owner fills in himself (credentials never pass through chat).

**18 November: the phone app.** "ANVE Home" for Android, a sealed pairing channel, and
first-time setup through a hotspot. The first real setup from a phone worked end to end
at 23:45. A tuning change to a boot file earlier that evening left the Pi dead until it
was power-cycled; the change was removed and the rule "never edit boot files for tuning"
was made.

**19 November: lights, iPhone, the wake word, the team.** LUMINA, the light strip
program, with its dashboard and 80-odd tests. The app on iPhone through Expo Go; setup
by hand from an iPhone confirmed by the owner. A factory reset that had never run on
real hardware hung; fixed and proven the same night. The wake word needed three or four
tries: the cause was a dropped segment while a decode was running, fixed. Turn-taking
(`turn.py`) so the speaker stops cutting people off after the chime. In the evening the
ANVE Box team's cloud link was added next to everything else, and a setup page that
opens by itself on the hotspot (proven on an Apple device, never on a Windows or macOS
computer).

**20 and 21 November: the strip lights up.** The strip worked once the right end was
wired. Then the Pi lost power whenever the strip's brightness changed: both were on one
multi-port charger. A cut in the middle of a deploy left thirty files empty on the card;
they were repaired from the laptop. With the strip on its own charger the cuts at
brightness changes stopped.

**22 and 23 November: the charger, then the wake word for real.** The Pi kept dying
under load. The black box (a 15 s recorder of voltage, temperature and load) showed
power really was lost; the Pi was on a phone charger. With the official 27 W supply:
5.15 V, no cuts. The wake word was then measured honestly with a beep test, and rebuilt
three times in one evening (section 5). A control page, Hinglish understanding and
"stop the music" over music followed.

**24 November: a new cloud.** The team's handover moved the speaker's voice to the ANVE
Box cloud. The box was added to the owner's account, and Gemini answered through the
speaker about two seconds after the wake word.

**26 and 27 November: who made you, and meetings.** "Who made you?" is answered "I was
made by Amish Sharma." Then the team's second handover: recording a meeting in the room.
Built in a day, and a two-minute recording from the real microphone went through.

**28 November: public.** The code went to this repository. The same morning the team
merged their own change here, "protocol 2", in which the cloud runs the box's body
(listen, play, stop, record). At the time of writing that change is in the repository
but has not been run on this Pi.

## 5. The wake word: what was tried and what it scored

All numbers are from this Pi, this microphone and the owner's voice unless said otherwise.

| Approach | Result |
|---|---|
| Whisper tiny.en writes the phrase, text rules accept it (the first design) | On a phone charger: unusable, the Pi died mid-answer. On the right charger, quiet room: **3 of 12** |
| Whisper base.en instead | Slower (about 2 s) and no better |
| Top-5 guesses, forced scoring of the phrase's tokens | Did not separate the phrase from other speech |
| The owner's own recordings matched by sound (encoder vectors + dynamic time warping) | **10 of 10** in a quiet room; 0 false wakes in 146 other clips; knows one voice only |
| A small trained model over the same encoder vectors (28 synthetic voices, audiobook speech, the owner's clips) | Held-out owner clips **17 of 17**; four unseen synthetic voices **369 of 384** (matching alone: 20); near-misses like "Hey Andy" 3 of 398 woke |
| Live, rules or model | A handful of tries only: the model caught ones the rules missed and the other way round; silent on all room talk heard |

**Over the speaker's own music** it is a different problem. At 50 % volume the raw
microphone clipped (one sample in twenty) and the echo canceller removed only 15 dB of
the music, which left it as loud as a voice; ten tries were heard as nothing at all.
Holding music to the loudness of 30 % volume stopped the clipping (28 dB removed).
With that, and matching short bursts and 3 s windows to the owner's recordings: about
**4 of 10**. Looser limits made the speaker wake on songs by itself, and the cloud then
answered the music, so they were tightened again. Once it is awake, "stop" pauses the
music every time it was tried (5 of 5).

Not measured: any real person other than the owner.

## 6. What went wrong, and what it taught

- **Power.** A phone charger, and later one charger shared with the light strip, caused
  dozens of restarts, a dead boot and one corrupted deploy. Use the official 5 V / 5 A
  supply, give the strip its own, and `sync` after copying files before restarting anything.
- **The microphone and the loudspeaker are too close.** Above about 30 to 40 % volume
  the microphone overloads and no software gets the voice back. A microphone away from
  the loudspeaker, or a microphone array, is the real fix.
- **A made-up name has no spelling.** Whisper writes "ANVE" as "Andy", "AVE", "and we".
  Matching sound, not text, is what worked.
- **Do not trust a labeller that was given the answer.** A large model told to expect
  "ANVE stop" wrote it for every burst of music.
- **Test paths that have never run before with a way back.** A factory reset and a
  set-up-again mode both had faults that only the real radio showed. Timed test modes
  with an independent revert were added after that.
- **Recordings kept for diagnosis must really be kept.** A pruning rule that sorted by
  name deleted every new clip for an hour.
- **Ask before making noise.** A dozen beep rounds in one evening was too many.

## 7. What works today

- "Hey ANVE" and a bare "ANVE" in a quiet room, in the owner's voice.
- Spoken answers from the ANVE Box cloud about two seconds after the wake word;
  follow-ups without the wake word; the talk ends by itself in silence.
- Music by voice from YouTube Music, radio, pause, next, exact and relative volume,
  in English and Hinglish, with "a bit more" understood from context.
- "Stop" after the wake word while music plays.
- Recording a meeting from the control page: pieces, a mark, the cloud's "stopped".
- First-time setup from an Android phone and from an iPhone; the setup page on an
  Apple device.
- The light strip following the speaker's state, with its own charger.
- Start-up after a reboot with nobody logged in, including the campus Wi-Fi sign-in.

## 8. What does not work yet, or was never checked

- The wake word while music plays: about 4 of 10.
- "ANVE stop" in one breath over music: 0 real stops in 8 tries.
- People other than the owner, on the real device.
- Starting, marking and stopping a meeting by voice; a network drop in the middle of a
  real recording; the 30 minute comparison against a laptop.
- The buttons.
- The setup page on a Windows or macOS computer.
- The team's protocol 2 on this Pi.
- Timers, alarms and confirm-on-screen, which the old platform had and the new cloud
  does not.
- A few answers the speaker gives itself ("what is playing", the volume, cloud notices)
  come in a plain built-in voice, because the new cloud does not speak a typed line.

## 9. Rules this project keeps

- No key, password, box identity, claim link or Wi-Fi sign-in in git, in logs or in chat.
  They live in root-only files on the Pi and reach programs as systemd credentials.
- The journal holds states and numbers, never what was said.
- Room sound is kept only when the owner switches diagnostics on, for a limited time,
  and never during a meeting. Meeting sound goes only to the cloud and is deleted from
  the card when the cloud has it.
- The cloud platforms are not changed from here; the device is their client.
- The ANVE Box team's code is copied unchanged.

## 10. Who did what

- **Amish Sharma** built the speaker: the hardware, every decision, every test with his
  own voice, the accounts and keys.
- **The ANVE Box team** wrote the cloud, the box link, box voice, meeting recording and
  protocol 2, and the handovers this work followed.
- **Claude** (Anthropic) wrote the firmware, the app and the tests in sessions with Amish,
  on his instructions.
