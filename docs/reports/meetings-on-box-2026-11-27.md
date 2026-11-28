# Report to the ANVE Box team: meetings on the box (5.3b), 27 Nov 2026

From Amish's Claude session in `anve-speaker` (`anve-device/`), after reading
`docs/handoff-amish-meetings-on-box.md` at `ANVEAI/anve-box` main `0b56043`.

## Where it stands

Steps 1 to 3 are built and running on the Pi. A 2 minute recording from the real
microphone went through end to end. Step 4 (the 30 minute real-room check) is not done.

## The commits

- Speaker (on the Pi's local repository, not pushed anywhere yet): `fe94319` (the
  feature) and `9527820` (folder permissions, tidy-up).
- Vendored `anvebox_agent`: copied unchanged from `ANVEAI/anve-box` `0b56043`
  (`identity.py`, `cloud/link.py`, `cloud/meeting.py`; `cloud/voice.py` as before).
  Nothing in it was edited.
- Cloud address: `https://box-cloud.example` (box link, voice and meetings).

## Step 1: the tool

`tools/box-meeting/record.py` on the Pi with the box's own identity, a 108 s made-up
meeting (three synthetic voices, no room sound), `--mark 30 --drop 60:30`:

    recording meeting <id> (up to 10800 s; 14400 s left today)
    2 pieces of 55 s, 108 s in all
      a tap at 30 s
    ended: stopped

So the identity, the account (Amish's, allowed to record) and the cloud work. Whether
the transcript and minutes appeared in the chats has not been looked at by this session;
Amish can confirm in the web app.

## Step 2 and 3: what was built

- `anve_device/meetings.py`, `MeetingRecorder` around your `MeetingRecording`.
- **Capture:** the speaker's existing 16 kHz mono stream after the PipeWire echo
  canceller and automatic gain, not gated. A plain room capture is not built yet; the
  comparison belongs to step 4.
- **Pieces:** cut every 55 s by sample count, no gap, no overlap; `start_ms` is the
  samples before the piece. Encoded off the event loop and handed to `add_piece()` in
  order.
- **Format:** Ogg Opus through `ffmpeg -c:a libopus -b:a 24k` (WAV if ffmpeg fails).
  Measured: 145 kB and 134 kB for 55 s pieces, about 9.5 MB an hour. The card has 47 GB
  free; before starting, the speaker checks there is room for 3 hours.
- **Start:** by voice (`ended` with reason `meeting` from the voice talk), by holding
  the action button for 3 s, or from the speaker's local control page.
- **During a meeting:** no voice talks, music paused, the recording light on. A tap
  marks; holding the button 1.5 s stops. After the wake word the next 3 s go to
  `command()`: `mark` plays the mark sound, `stop` cuts the last piece and stops.
- **Spoken lines:** recorded clips, no network needed ("This meeting is being
  recorded", "Recording stopped", "Recording ends in a minute") and a two-note mark
  sound. "This meeting is being recorded" plays when the cloud's `started` arrives.
- **After a power cut:** at start-up a meeting folder that still holds something is
  given to `MeetingRecording` and told to stop, so its pieces go up.
- **Folder:** `/var/lib/anve-device/meetings/<when>/`, mode 0700.

## The real-microphone test (2 minutes, from the control page)

    14:52:37 meeting recording started (47871 MB free on the card)
    14:52:38 the cloud has the meeting (it may last 10800 s more)
    14:53:09 mark at 32.0 s
    14:53:33 piece 0 on the card (55 s, 145 kB, ogg)
    14:54:28 piece 1 on the card (55 s, 134 kB, ogg)
    14:54:39 stopped after 122 s
    14:54:40 piece 2 on the card (12 s, 35 kB, ogg)
    14:54:40 meeting over: stopped (3 pieces, 316 kB sent as ogg)

Tests without a network: `tools/meeting_test.py` 13 of 13 against a stand-in for
`MeetingRecording` (cutting, order, marks, the command clip, stop, leftovers).

## Not tested yet

- Starting by voice ("ANVE, record this meeting"), marking and stopping by voice.
- The buttons (it is not confirmed that buttons are wired on this Pi).
- A network drop in the middle of a real recording, and a restart in the middle.
- The 30 minute real-room check against the laptop (5.3c), and echo-cancelled against
  plain capture.

## Choices you may want to change, and questions

1. **The long press.** The action button is already push-to-talk (hold to talk), so a
   3 s hold first opens a voice talk and then drops it to start a meeting. Say if you
   want a different gesture.
2. **Leftovers after a power cut are stopped, not continued.** The microphone was not
   recording while the speaker was off, so the meeting's clock would have a hole.
3. **Pieces the cloud will never take.** For `ended` and `unknown_meeting` the speaker
   deletes what is left on the card; a folder the cloud keeps refusing for 7 days is
   deleted too. `MeetingRecording` itself clears the folder only on `stopped`,
   `time_limit` and `daily_limit`, so the other reasons would otherwise keep meeting
   sound on the card for ever. Is that the behaviour you want from the box?
4. **Mute during a meeting** does nothing to the recording at present. Should the mute
   button pause or stop it?
5. **`ended` texts** are spoken by the speaker's plain built-in voice (espeak), since
   they are not known in advance. The same is true of box voice's `ended` texts.
