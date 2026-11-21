# LUMINA

The light strip as the voice assistant's face: 60 WS2812B LEDs on a Raspberry Pi 5 that
show what the assistant is **really** doing. When it listens they show listening; when it
thinks, thinking; when it speaks, speaking. Every look comes from an event the assistant
reported, never from guessing at microphone noise.

LUMINA is its own small program beside the assistant. The assistant tells it what is
happening; LUMINA keeps the state, decides what is worth showing, draws it at 30 frames a
second and is the only program that touches the strip. If LUMINA stops, the assistant
does not notice; if the assistant stops, the strip says so.

## Wiring (before the LEDs are switched on)

| What | Where |
|---|---|
| Strip 5 V and GND | its **own** regulated 5 V supply (5 A). Not the Pi's 5 V pin |
| Grounds | strip GND, supply GND and a Pi GND pin joined |
| Data | Pi GPIO10 (pin 19, SPI0 MOSI) -> 74AHCT125 level shifter -> 330 ohm -> strip DIN |
| Capacitor | 1000 uF across the strip's 5 V and GND, close to the strip |

60 LEDs at full white draw about 3.6 A. LUMINA limits brightness to 20 % and the whole
strip to an estimated 1.5 A (`power_limit_ma`), but a limit in software is not a reason to
feed the strip from the Pi.

## Brightness

Three settings, each under the limit `max_brightness` (20 % unless raised on the Pi itself):

| Setting | When |
|---|---|
| `brightness` | at rest: ready, music paused, microphone off, errors, connecting |
| `active_brightness` | from the wake word to the end of the reply: wake, listening, thinking, speaking (and an alarm) |
| `bright_when` | `conversation` (default, as above) or `listening`: `active_brightness` only while the box listens; thinking and speaking stay at `brightness` |
| `music_brightness` | music playing |

The brightness rises with the look (at once for the wake word) and falls over about
1.5 s, so the strip settles back after a conversation. After the wake pulse the wake look
stays until listening begins (at most 3 s), so there is no flash of the resting look in
between. `power_limit_ma` still applies on top: a frame that would draw more is dimmed as a
whole, so a fully lit strip "at 100 %" shows at about 40 % of full white.

Meant for the ANVE speaker: at rest 20 %, after "Hey ANVE" and with music 100 %, limit
100 %, power limit 1.5 A, the resting look `glow` (the whole strip evenly lit). On 21 Nov
2026, with those settings, the Pi lost its power three times in five minutes: twice within
seconds of a large change in the strip's brightness (5 % to 20 %, and 20 % to dark), once
while booting with the strip dark. LUMINA starting at 20 % after those restarts did not cut
it, and the Pi had lost power before the strip was wired, so the cause is not established:
the strip sharing the Pi's supply, or the Pi's own supply. Until that is settled all three
brightnesses are held at 5 % (sliders only: the limit is still 100 %). The settings, for then:

```bash
python -m lumina --set max_brightness=1.0 --set brightness=0.2 --set animations.READY=glow \
  --set music_audio=loopback --set music_target=anve_ec_sink --set voice_delay_s=0.5 --set music_delay_s=0.2
```

The old PWM libraries (`rpi_ws281x`) do not work on the Pi 5. LUMINA makes the signal
over SPI, the way the Pi5Neo library does (6.4 MHz, one SPI byte per LED bit); it needs
SPI switched on (`dtparam=spi=on`) and the Python module `spidev`.

## Install, start, stop

```bash
cd ~/anve-device/lumina
./install.sh            # a service that starts with the Pi; the LEDs stay OFF
./status.sh             # what it shows, the dashboard address and its key
./start.sh --hardware   # switch the real strip on (asks first; starts at 5 % brightness)
./start.sh --simulate   # LEDs off again, preview only
./stop.sh               # fade out, all LEDs off
./uninstall.sh
```

The first time with real LEDs: `./start.sh --hardware` sets the brightness to 5 %. Watch
the strip and run `vcgencmd get_throttled`: if it is not `0x0`, or its last digit is not
0, the Pi's own supply is short, and the strip must not be fed from the Pi. Then raise
the brightness in the dashboard, up to the 20 % limit.

Out of the box the LED output is the **simulator**: everything runs, the dashboard shows
the strip live, and no LED lights. The real strip lights only after `./start.sh
--hardware`. A missing or broken settings file means the simulator again.

Without the service: `python -m lumina --preview` draws the strip in the terminal, and
`python -m lumina.demo_assistant` (in a second terminal) plays a pretend assistant through
every state.

## How the assistant reports

```python
from lumina_client import AssistantLEDs
leds = AssistantLEDs()

leds.setup_required()            leds.wake_word_detected()      leds.speaking(audio_stream=player)
leds.connecting()                leds.listening()               leds.ready()
leds.ready()                     leds.processing()              leds.music_playing()
leds.muted() / leds.unmuted()    leds.thinking()                leds.music_paused()
leds.disconnected()              leds.searching()               leds.music_stopped()
leds.error("Connection failed")  leds.tool_executing()          leds.level(mic=0.4) / leds.level(out=0.7)
```

or by name: `leds.set_state("listening")`, `leds.emit("ai.thinking")`, and the whole state
at once: `leds.status(connection="connected", voice="idle", media="playing", ...)`.

Each call is one UDP datagram to `127.0.0.1:8741`. It never waits and never raises.
Sending `status(...)` every second or two is worth it: it tells LUMINA the assistant is
alive, and it is the authority that puts right whatever a lost message left wrong.

Other ways in: `POST /api/event {"name": "ai.thinking"}` and `POST /api/state` on the
dashboard port, the same messages over its WebSocket (`/ws`), and MQTT (below).

### Where the ANVE speaker is hooked

One small file, `anve_device/lights.py`, and one place in `anve_device/main.py`:

| In the speaker | What LUMINA is told |
|---|---|
| `Device.run()` creates `Lights(self)` when `[lumina] enabled = true` | (the speaker then never opens the strip itself) |
| `Device.look()`, called on every change of state | the whole state: connection, fault, microphone, listening, thinking, tool, speaking, music, alarm |
| `Device.mic_loop()` -> `ring.level()` | the microphone's level while listening; the reply's own level while speaking |
| wake word heard -> `ring.flash(white)` | `wake_word.detected` |
| a confirmation is waiting -> `ring.flash(amber)` | `assistant.notice` |
| speaker stopping -> `ring.close()` | `assistant.shutdown` |
| every second | the whole state again (also the "I am alive" signal) |

"Setup needed" comes from the speaker's own marker file
(`/var/lib/anve-device/provisioned`, the `setup_marker` setting): while it does not exist,
the strip shows setup needed.

Three looks are never shown by the ANVE speaker itself, because nothing in it says so:
SEARCHING (ANVE gives no "searching the web" signal), PROCESSING (ANVE goes straight from
listening to thinking) and SETUP IN PROGRESS (the setup program is not hooked, on purpose:
it is the one that can take the Pi off the Wi-Fi). They exist, they are tested, and they
can be driven through the API or tried from the dashboard.

## What is shown

The state is not one word but several facts at once (`state_manager.py`): power, fault,
setup, connection, microphone, voice, processing, speech, media, alarm. Music can play
while the assistant thinks. What the strip shows is picked by priority:

1. shutting down
2. an error (three short red pulses, pause)
3. an alarm ringing (white flashes)
4. microphone off (the whole strip red, breathing). A listening look is never shown then
5. setup needed (amber breathing, a soft pulse now and then), setup in progress (blue to cyan)
6. disconnected (three small red marks pulsing); connecting (cyan dots travelling)
7. wake word (a bright pulse from the middle across all 60), listening (cyan wave that follows the microphone's level), recording
8. processing, thinking (violet, circling), searching (a scanner), running a tool (fast cyan particles)
9. speaking (blue, cyan and white wave that follows the reply's own loudness)
10. music playing, music paused
11. ready (one faint glow drifting, almost nothing; or `glow`: the whole strip evenly lit, a faint wave running along it)

Two refinements. With the microphone off, a reply being spoken still shows, between red
ends (`muted_overlay`; switch it off for a strict "red and nothing else"). While the
assistant thinks and music plays, the music keeps glowing faintly underneath (`blend_media`).

A state that should end but whose "finished" event never came is dropped after a while
(listening 45 s, thinking 2 min, speaking 3 min); the assistant's `status` messages keep
real long states alive. If the assistant stops reporting for 8 s the strip shows
disconnected. Until it has reported at all, the strip shows connecting, never ready.

## Sound

Three separate paths, never mixed up:

* **Microphone** (listening): the assistant measures its own microphone and sends the
  level. LUMINA does not open the microphone. No level arriving means the wave rests.
* **The assistant's voice** (speaking): the assistant sends the loudness of the reply it
  is playing. The microphone is never used for this. With no level, the wave moves like
  speech by the clock.
* **Music**: shown because the player says it plays. Reacting to the sound itself is
  optional (`music_audio = "loopback"` records a copy of what goes to the speaker through
  PipeWire and splits it into bass, mids, treble and beats). It is **off** by default.
  `music_target` names the output it copies (on the ANVE speaker `anve_ec_sink`, which
  music and the assistant's voice both go through). Quiet music is turned up first (up
  to 30 dB), so the lights move as much at a low volume as at a loud one; near-silence is
  not.

A Bluetooth speaker plays late. `voice_delay_s` and `music_delay_s` hold the lights back
by that much, so they move with the sound in the room; the microphone is never held back.
On the ANVE speaker: 0.5 s for the voice (the reply is written about 0.25 s ahead, and the
speaker adds about 0.3 s), 0.2 s for music. Both are estimates, adjustable in the dashboard.

## Dashboard

`http://<the Pi>:8742/`, from a phone or PC on the same network; `./status.sh` prints the
address and the key it asks for. It shows the strip live, every part of the assistant's
state, the event log, and lets you change the three brightnesses, theme, colours, speed,
animation per state, music mode, direction, how long the lights wait for the voice and the
music, and the number of LEDs used. Changes are stored at once in
`~/.config/lumina/settings.json`.

"Try a state" shows a look for 20 seconds. It is marked as a demo, it does not change the
assistant's state, and anything real that matters (wake word, listening, microphone off,
an error, a lost connection) ends it at once.

The dashboard cannot switch the real LEDs on or raise the brightness limit or the power
limit: those are changed on the Pi itself (`./start.sh --hardware`,
`python -m lumina --set max_brightness=0.3`).

## Themes

Futuristic Cyan (default), Google-inspired, Alexa-inspired, Cyberpunk, Minimal Ambient,
Purple AI, in `data/themes.json`. The two named after other products borrow a colour mood
only. Any single colour can be changed on top of a theme.

## Other makers' speakers, MQTT, Home Assistant

An Amazon Echo or a Google Nest speaker does not tell anyone outside when it is
listening, thinking or speaking; there is no supported interface for it, and LUMINA does
not try to get it from private interfaces or from the sound in the room. What Home
Assistant can give for such a speaker is its `media_player` state, and
`integrations/home_assistant.py` turns that into the music looks. `integrations/mqtt.py`
accepts events and states on `lumina/event` and `lumina/state` and publishes
`lumina/status`. Both are off by default, and both have only been tested against
stand-ins so far, not a real broker or a real Home Assistant.

## Tests

```bash
python -m unittest discover -s lumina/tests -t .
```

91 checks, all in simulation: every state and every transition in order, priorities,
duplicate, unknown, out-of-order and simultaneous events, missed events, the assistant
stopping and restarting, a strip that is missing or dies, a browser that vanishes
mid-stream, the limits on brightness and power, and the real UDP, HTTP and WebSocket paths.

## Layout

```
lumina/
  controller.py         the program: takes events in, draws, shows
  state_manager.py      the state, the priorities, the timeouts, the history
  compositor.py         cross-fades, overlays, brightness and power limits, dithering
  event_bus.py          thread-safe queue between the adapters and the controller
  assistant_client.py   AssistantLEDs: what the assistant calls
  config.py             defaults, your settings, themes
  states/               one renderer per look
  effects/              the building blocks: breathing, pulses, scanners, waves, music
  audio/                smoothing, loudness, bands; the optional loopback
  hardware/             the SPI strip and the simulator
  integrations/         UDP (the assistant), dashboard server, MQTT, Home Assistant
  dashboard/            the web page
  data/                 themes and the shipped defaults
  tests/
```
