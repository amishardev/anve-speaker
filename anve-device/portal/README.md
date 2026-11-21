# The setup page

Setting the box up from a computer, or any browser, with no app. Served by
`anve_device/provision.py` on the box's setup hotspot, beside the phone app's API, which
is unchanged.

## For the person setting up

1. Plug the box in. Its light pulses slowly and the Wi-Fi `ANVE-xxxx` appears.
2. Join `ANVE-xxxx` from the computer. The setup page opens by itself. If it doesn't,
   open any address in a browser, or `http://10.42.0.1/`.
3. Choose the Wi-Fi, type its password, name the box. Two optional parts: the sign-in for
   a Wi-Fi with a login page (campus, hostel, hotel), and the voice assistant's key.
4. The page shows the link that adds the box to an ANVE account. Copy it, then press
   "Connect now" (after two minutes the box goes on by itself).
5. The computer drops back onto its usual Wi-Fi. The page notices and switches on the
   "Add the box to your account" button.

The phone app works exactly as before. On an iPhone, joining `ANVE-xxxx` now brings up
this page in a sheet first: finish there, or close the sheet, choose "Use Without
Internet" and go back to the app.

## What is whose

| File | From |
|---|---|
| `assets/anve.css`, `assets/icons.svg`, `assets/led.js`, `assets/fonts/` | the ANVE Box team's page, `ANVEAI/anve-box` `device/portal/assets` at `d0de5c0`, unchanged |
| `index.html`, `assets/portal.css`, `assets/portal.js` | written here, on the team's `index.html`, `setup.css` and `setup.js` |
| `assets/seal/` | built by `tools/build_portal.py` from `src/seal.mjs` and the noble libraries (see `assets/seal/NOTICE.txt`) |

`GET /api/status` and `GET /api/networks` answer in the shape the team's page reads.

## The one deliberate difference from the team's page

The team's setup Wi-Fi has a password (printed on the box's card), so their page may send
the home Wi-Fi password as plain JSON and read the link from `/api/status`. This box's
setup Wi-Fi is open, because the phone app joins it without asking for a password. On an
open network anyone nearby can read plain traffic, so here:

- the page seals what is typed in the browser, for this box only (X25519, HKDF-SHA256,
  ChaCha20-Poly1305: the app's sealing, `anve_device/pairing.py`);
- the link travels only inside the box's sealed answer; `/api/status` never carries it;
- the team's plain `POST /api/connect` is refused (`sealed_only`).

What sealing does not do:

- Prove to the browser that it is talking to the real box. Someone who runs a fake
  `ANVE-xxxx` at that moment could receive what is typed. The app's setup has the same
  limit.
- Keep a stranger out. While a box is in setup mode, anyone in radio range can join the
  open hotspot, send any valid details and get the sealed answer, link included. That
  link is ownership of the box in the ANVE Box cloud. (The same person could equally set
  the box up with the app: being near a box that is not set up has always been enough.)

The team's card password closes both, and the hotspot can move to it once the app asks
for the card.

## A box set up without the voice key

The page's key field is optional, and a box set up without it is fully in the ANVE Box
cloud (online, claimable, "blink my box"). It has no "Hey ANVE", and the phone app cannot
add the key afterwards: the app talks to the speaker program, which does not start
without its key. To add it, the box is set up again, with the key in the page or with the
app: `tools/hotspot_trial.sh --apply` on the box, or a factory reset. Both need the box's
command line today; there is no button for it yet.

## When the box leaves

The setup Wi-Fi closes by itself: the box joins the Wi-Fi it was given, a test run ends,
or a phone drops an open network that has no internet. The page asks the box "are you
there?" every few seconds (`GET /api/alive`). After two unanswered asks it says so above
whatever step is open, dims what can no longer work, and carries on by itself when the
device is back on `ANVE-xxxx`. During a test run or a set-up-again the chip beside the
name counts down to the closing time.

On the first real hotspot run (19 Nov 2026, an iPhone) the page had none of this: the
test closed 30 seconds after the last request and the page went on showing "Choose your
Wi-Fi". A test now also stays open for as long as a page is open on it, until its time
is up.

## Trying it

- On a laptop, no box: `python tools/portal_preview.py`, then `http://127.0.0.1:18080/`.
- On the box, changing nothing: `tools/hotspot_trial.sh` opens the hotspot for a few
  minutes; the page runs every step and the box answers "test run". That is the only
  reason the page ever says "test": a real setup never does.
- On the box, for real: `tools/hotspot_trial.sh --apply`. The page says "set up again",
  and what is sent replaces the box's Wi-Fi and name (the voice key stays unless a new
  one is typed). If nothing is applied, the box goes back to the Wi-Fi it was on.
- Tests: `python3 tools/provision_test.py` (on the Pi: what is served, refused, stored)
  and `python tools/portal_test.py` (on the laptop: the page's sealing against the box's).
- Off switch: `Environment=ANVE_CAPTIVE=0` in `anve-provision.service` stops the page
  opening by itself. The page stays.
