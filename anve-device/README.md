# ANVE speaker on a Raspberry Pi 5

Firmware for a DIY smart speaker whose brain is the ANVE platform. The Pi is a client of
ANVE's assistant socket (`docs/assistant-protocol.md` in the platform repository,
protocol version 1); nothing in the platform is changed.

Status on 16 Nov 2026: built and tested against a local mock of the server. Not yet run
against `app.anveai.com` (no device key on the Pi yet). See "What is measured" and
"Known limits" before relying on it.

## Hardware as built

| Part | What is actually connected |
|---|---|
| Computer | Raspberry Pi 5, 16 GB, Raspberry Pi OS (Debian 13), Wi-Fi |
| Microphone | iBall 360Meet X on USB (shows up as "UACDemoV1.0", mono) |
| Speaker | Portronics SoundDrum 1 over **Bluetooth** (A2DP). There is no USB sound card. |
| Power | 45 W dual-port charger; the Pi negotiates only 3 A (see "Power") |
| LEDs | WS2812B strip on GPIO10 (SPI0 MOSI). Driver written; **switched off in config** for now |
| Buttons | mute on GPIO17 (pin 11 to GND pin 9), action on GPIO27 (pin 13 to GND pin 14). Code written; not wired or tested yet |

## How it works

```
microphone -> PipeWire echo canceller (WebRTC: echo, 80 Hz high-pass, noise suppression)
           -> Silero speech detector -> gain control
           -> wake word on the Pi (faster-whisper tiny.en)     nothing is uploaded before it
           -> ANVE socket (Opus, 40 ms frames)                 after a wake / push-to-talk /
                                                               while a meeting is recorded
ANVE reply -> jitter buffer -> echo-cancel sink -> Bluetooth speaker
```

- `anve_device/client.py` the socket: `hello`, `ready`, heartbeat, backoff, and a full
  stop after `unauthorised`, `origin`, `rate` or `concurrent`.
- `anve_device/playback.py` the echo canceller (loaded for as long as the program
  runs), playback with jitter buffer and instant flush, earcons.
- `anve_device/wake.py` "Hey ANVE" and spoken "stop", on the device.
- `anve_device/dsp.py` gain control. `anve_device/main.py` ties it together.
- `anve_device/led.py`, `ring.py` the LED strip. `tools/` tests and helpers.

## Install

```bash
cd ~/anve-device
python3 -m venv --system-site-packages .venv
.venv/bin/pip install websockets faster-whisper
cp config.example.toml config.toml        # then edit: speaker name, Bluetooth address
tools/install_service.sh                  # installs and enables the service
tools/set_key.sh                          # asks for the device key; the service then starts
```

Needs from apt: `pipewire`, `wireplumber`, `libspa-0.2-bluetooth`,
`libwebrtc-audio-processing-1-3`, `libopus0`, `sox`, `espeak-ng` (tests only),
`python3-numpy`, `python3-spidev`, `python3-gpiozero`, `python3-lgpio`.

## The setup page

The Pi serves a setup page (`anve_device/setup_web.py`, service `anve-setup`). It checks
power, network, microphone, speaker, device key, service and the link to ANVE; its
**Set up everything** button works through the failing checks by itself (reconnects the
speaker, installs and starts the service, enables the CPU cap) and then says what only a
person can do. It also takes the device key, pairs a Bluetooth speaker, sets the volume,
plays a test sound, tests the microphone, asks ANVE to say hello, runs the read-only
capability sweep and shows the service log.

It listens on the Pi's own loopback address only, because the Wi-Fi here is an open
network and the key is typed into it. Open it from a laptop through SSH:

```bash
ssh -N -L 8737:127.0.0.1:8737 <user>@<pi-address>
```

then browse to `http://localhost:8737`.

## The device key

Create it in ANVE: Assistant page, Devices, kind **room**. A room device can never
confirm an action by voice: starting a recording, saving a document and similar actions
wait for Confirm on the owner's own ANVE screen.

`tools/set_key.sh` stores it in `/etc/anve-device/key` (owner root, mode 600). systemd
passes it to the service as a credential. It is not in this folder, not in git, and not
in the logs (the websocket library is kept above DEBUG for that reason).

## Using it

- **"Hey ANVE"**, then the request. After each answer there is a follow-up window of
  about 8 seconds in which you can speak again without the wake word.
- **"Stop"** while it is talking interrupts it. So does "Hey ANVE".
- **Mute button**: stops all listening and tells the server. Hold 3 s to retry the
  server after it refused the device.
- **Action button**: hold to talk (push-to-talk); a tap while it is talking interrupts.
  During a meeting recording the wake word is not a command, so hold the action button
  to say "stop recording and make the minutes"; a double tap sends `record.stop`.
- Timers and alarms ring three times, then the label is spoken. They ring on whichever
  of the owner's ANVE sessions is open first, which can be a browser tab.

### Music, the browser and sounds (done by the speaker itself)

ANVE has no tool for playing media on a device, and the platform is not changed. The
speaker reads what ANVE heard and handles these itself (`anve_device/skills.py`), then
stops ANVE's own answer. You may hear the start of "I can't play music" first.

| Say | What happens |
|---|---|
| "play *song* by *artist*" (optionally "on YouTube") | its own Chromium opens YouTube, picks the first result and plays it |
| "play some music", "play jazz / lofi / classical / indie" | a free internet radio stream |
| "Hey ANVE" while music plays | the music drops to half volume and keeps playing while it listens and answers |
| "pause" / "stop", "play" / "resume", "next song" | as they say; a plain "stop" also works without the wake word |
| "volume up" / "volume down" | speaker volume by 10 %, between 10 % and 60 % |
| "open youtube", "open wikipedia.org", "search for *x* in the browser" | opens it in the speaker's browser |

- The browser is a separate profile (`~/.anve-browser`), driven on `127.0.0.1:9223`. Its
  sound goes through the echo canceller. `tools/install_adblock.sh` puts uBlock Origin
  Lite into that browser only.
- While music plays at full volume nothing is streamed to ANVE; the wake word is looked
  for in a rolling 2.5 s window, because singing never ends a "speech" segment.
- Sounds: a soft rising chime the moment the wake word is recognised, and a quiet
  two-note pulse while ANVE works (`[thinking] mode`: "tone", "voice" or "off").
- Check the sentence parser with `.venv/bin/python tools/skills_test.py`.

LED looks (when the strip is enabled): faint teal idle; blue listening (cyan while audio
goes to a cloud model) with a white voice bar; purple dot thinking; green speaking;
solid red muted; amber recording; slow red blink no server / no device; red-orange
double blink the server refused the key.

## Day to day

```bash
sudo systemctl status anve-device        # is it running
journalctl -u anve-device -f             # live log (no secrets, no transcripts)
sudo systemctl restart anve-device
bluetoothctl connect AA:BB:CC:DD:EE:FF   # the speaker, if it is free and will not connect
```

The service starts on boot, restarts after a crash with a delay that grows from 10 s to
5 min, reconnects to the server with the protocol's backoff (1, 2, 4, 8, 15, 30 s), and
reloads the echo canceller when the microphone or the speaker comes back.

### When the light stays red / it will not connect

| Log line | Meaning | What to do |
|---|---|---|
| `not reconnecting: the server said 'unauthorised'` | the key is wrong or revoked | new key: `tools/set_key.sh` |
| `... 'rate'` | more than 30 sessions in an hour | wait, then hold mute 3 s or restart the service |
| `... 'concurrent'` | two ANVE sessions already open for this account | close the browser's assistant, then as above |
| `audio devices missing` | microphone or speaker not found | plug in the mic; switch on the speaker and free it from the phone |
| `link down (connect_failed...)` | no internet or campus portal | it retries by itself |

## Tests

```bash
.venv/bin/python tools/client_selftest.py            # 21 protocol checks against the mock
.venv/bin/python tools/mock_server.py --port 8765 --key anve_dev_mock   # a stand-in server
.venv/bin/python -m anve_device.main --url ws://127.0.0.1:8765/api/assistant/ws --key-file <file>
python3 tools/erle_test.py --speech recordings/ref_speech.wav          # echo return loss
```

The mock is not the platform: it has no model and answers with a fixed recording.

## What is measured (16 Nov 2026, this room, this hardware)

| What | Result |
|---|---|
| Protocol self-test against the mock | 21 of 21, with Opus and with PCM16 |
| Echo at the mic, speaker at 40 % | -12 dBFS raw, -29 dBFS after cancelling (the room's own level): at least 17 dB, after about 4 s of the first reply |
| Echo, speaker at 50 to 60 % | the mic clips; only 7 to 16 dB |
| Bluetooth output delay | 195 to 213 ms direct, 270 to 316 ms through the canceller, wandering about 27 ms within a reply |
| Steady room noise | -34 dBFS raw, -52 dBFS after WebRTC noise suppression |
| Wake word on the owner's recorded phrases | 3 of 4 (one recording, replayed; not a live rate) |
| Wake decision time | about 1.0 s main decode + 0.35 s second check, at 1.8 GHz on 2 threads |
| False wakes | 0 in 3.6 min of room sound with nobody speaking (too short to quote a rate) |
| Power | under-voltage at 2.4 GHz on 4 threads (4.67 V); none at 1.8 GHz on 2 threads |

## Known limits

- **Not yet tested against the real server.** Everything above is against the mock.
- **Wake word accuracy is modest.** Whisper hears the name as "and we" unless biased;
  the platform's own notes report a 34 % miss rate for this approach. A trained
  wake-word model (see the platform's `docs/wake-word.md`) is the real fix.
- **Bluetooth speaker.** About 0.3 s of extra delay, weaker echo cancelling than a wired
  output, sound already sent to the speaker (about 0.2 s) cannot be recalled on "stop",
  and the speaker must be free from other phones to connect.
- **Speaker volume is capped at 40 %** because above that the speaker overloads the
  microphone. The microphone's gain control has no effect on Linux. Moving the speaker
  further from the microphone would raise the cap.
- **Power.** The Pi needs its own 5 V / 5 A supply. Until then `anve-cpu-cap.service`
  holds the CPU at 1.8 GHz and the wake word uses 2 threads.
- **Processing delay** is above the 60 ms target: PipeWire capture, the echo canceller's
  block size and 40 ms framing add up to roughly 100 ms before the uplink. Not measured
  precisely yet.
- LEDs are off, buttons are unwired, and the noise-suppressor comparison (WebRTC vs
  RNNoise vs DeepFilterNet) and the 1 m / 3 m proof recordings are still to do.

## The phone app and first-time setup

The app is in `../anve-app` (ANVE Home). On the speaker three things serve it:

| What | Where | Notes |
|---|---|---|
| First-time setup | `anve_device/provision.py`, `anve-provision.service` (root) | A box without `/var/lib/anve-device/provisioned` opens the hotspot `ANVE-xxxx` (10.42.0.1, port 80) and takes Wi-Fi, name, room and ANVE key from the app, sealed for its public key. A box that is set up only waits for a factory reset. |
| The app's API | `anve_device/appapi.py`, inside the speaker, port 8738 | Now playing, volume, pause/next, "an approval is waiting", pairing, rename, reset. Every request is signed and encrypted with the token in `/etc/anve-device/app-token` (root-only). |
| Finding the box | `anve-xxxx.local` and the service `_anve._tcp` (avahi) | xxxx = last four characters of the board's serial number. |

    tools/pair_app.py              a 6-digit code so one more phone can pair (5 minutes)
    tools/provision_test.py        setup, end to end, with a pretend Wi-Fi (changes nothing)
    sudo tools/appapi_test.py      the app's API on the running speaker
    tools/optimise_pi.sh           sound priority, Wi-Fi power saving off, unused services off

Not done yet: the real hotspot has never been switched on (it takes the Pi off its Wi-Fi,
so it needs someone next to it with a phone). `ANVE_RECOVERY=1` in the unit (reopen the
hotspot when a set-up box has lost its Wi-Fi for 3 minutes) is written and off.
Approvals are never answered through the speaker: a room device must not approve
anything, so the app answers them at ANVE with the person's own sign-in.

## The light strip (LUMINA)

The 60-LED strip is drawn by LUMINA, a separate small program in `lumina/` with its own
service (`lumina.service`) and its own README (`lumina/README.md`). The speaker only
reports its state to it (`anve_device/lights.py`, switched by `[lumina]` in config.toml);
it does not open the strip itself any more. The LED output is off until someone runs
`lumina/start.sh --hardware`; until then LUMINA runs in preview only, and its dashboard
(`lumina/status.sh` prints the address and key) shows the strip live.

## Keeping the Pi reliable

`tools/optimise_pi.sh` applies a small set of changes (`--status` shows them, `--undo`
removes them); its header says what each one is for. None needs a restart, and none
changes how the Pi boots.

`tools/blackbox.sh --show` lists the Pi's restarts with what it looked like just before
each one (5 V supply, low-voltage flags, temperature, load) and whether the power really
went or the Pi froze with the power on. The hardware watchdog restarts a frozen Pi after
a minute. On 19 Nov 2026 most restarts were of that second kind, under heavy load.

## The ANVE Box cloud (added 19 Nov 2026)

Beside the speaker, a second program links the box to the ANVE Box team's cloud
(`github.com/ANVEAI/anve-box`, protocol 1, part A of their `docs/box-cloud-agreement.md`).
The older platform link above is untouched; the two run side by side.

| What | Where | Notes |
|---|---|---|
| The link | `anve_device/boxlink.py`, `anve-boxlink.service` (user, not root) | Signed hello, mode, `light.identify` / `light.show` on the Pi's green LED and on LUMINA, owner and claim state. |
| The team's own code | `anvebox_agent/`, `tools/anvebox_factory.py` | Exact copies, see `anvebox_agent/VENDORED.md`. Not edited here. |
| Who the box is | `/etc/anvebox/identity.json` (root, 600) | Made once by `tools/install_boxlink.sh`. SECRET: never printed, never copied off the box. |
| Its claim state | `/var/lib/anvebox/state.json` | Owner yes/no, current claim secret; written to a temporary file and renamed. |
| What the cloud needs | `/etc/anvebox/record.json`, copied to `~/anvebox-record.json` | Safe to share: the box's ID, public key and the hash of its claim secret. The team adds it to the cloud. |

    tools/install_boxlink.sh       identity (once), record, service
    sudo tools/claim_link.sh       the link that adds the box to an account (SECRET; run it yourself)
    .venv/bin/python tools/boxlink_test.py     the link against a stand-in cloud (44 checks)
    .venv/bin/python -m anve_device.boxlink --status

Until the team has added the record, the cloud refuses the box ("doesn't know box ...")
and the link keeps retrying; that is the expected state, not a fault. The cloud's
address is `[boxlink] cloud` in `config.toml`.

## Setup from a computer: the hotspot's own page (added 19 Nov 2026)

The setup hotspot now also serves a page, so a box can be set up from a computer or any
browser with no app: join `ANVE-xxxx`, the page opens by itself, choose the Wi-Fi, copy
the link that adds the box to an account. `portal/README.md` has the details, what is the
team's and what is ours, and the one deliberate difference from the team's page (this
hotspot is an open network, so the page seals everything in the browser).

It is not the admin page of "The setup page" above (`setup_web.py`, loopback only).
The phone app's setup is unchanged.

Two things to know. A box set up from the page WITHOUT the voice key is online and in
the ANVE Box cloud but has no "Hey ANVE", and the app cannot add the key afterwards (the
speaker does not run without it): it has to be set up again, `tools/hotspot_trial.sh
--apply` or a factory reset. And on an iPhone, joining `ANVE-xxxx` now shows this page in
a sheet first; to carry on in the app, close it and choose "Use Without Internet".
`Environment=ANVE_CAPTIVE=0` in `anve-provision.service` stops the page opening by itself.

    python tools/portal_preview.py     the page on a laptop, with a pretend network
    python3 tools/provision_test.py    79 checks, the page's included (on the Pi)
    python tools/portal_test.py        the page's sealing against the box's (laptop, Node)
    python tools/build_portal.py       rebuild portal/assets/seal (laptop; downloads nothing)

Not tried on the real hotspot yet: opening it takes the Pi off its Wi-Fi.
`tools/hotspot_trial.sh` does that for a few minutes and changes nothing; the page then
runs every step and the box answers "test run". What is checked without it: the page and
every refusal against the pretend network, and the "every name leads to the box" line
against the Pi's own dnsmasq on a private port.
