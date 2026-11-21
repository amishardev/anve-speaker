# ANVE Home (phone app)

The app for the ANVE box (the Raspberry Pi speaker in `../anve-device`). Android first;
written in React Native (Expo) so the same code builds for iPhone.

What it does:

1. **First-time setup, once.** Get started → sign in to ANVE → *Find my ANVE box* (the
   box's own Wi-Fi hotspot `ANVE-xxxx`) → name and room → choose the home Wi-Fi → done.
   After that the app always opens on Home.
2. **Home.** What the box is doing, what is playing, play / pause / next, volume.
3. **Approvals.** Everything ANVE or an agent will not do without a yes, with a
   notification when something new waits. Approve or decline.
4. **Profile.** ANVE account, box details, notifications, factory reset.

## How it talks to things

| To | How | Code |
|---|---|---|
| The box during setup | its hotspot, `http://10.42.0.1`; secrets are sealed for the box's public key | `src/lib/setup.ts`, `modules/anve-wifi` |
| The box afterwards | home Wi-Fi, port 8738; every request signed and encrypted with the token from setup | `src/lib/box.ts`, `src/lib/crypto.ts` |
| ANVE | HTTPS with the person's own sign-in (cookie session) | `src/lib/anve.ts` |

Approvals go **only** to ANVE with the person's sign-in. The box is a room device and may
never approve anything; it only tells the app "something is waiting".

Notifications: the ANVE platform has no push service, so the app is told by the box
(instantly, while the app is open or recently used) and asks ANVE itself; when the app is
closed, Android lets it check about every 15 minutes (`src/lib/notify.ts`).

## The look

One design for Android and iPhone (`src/ui.tsx`), built on ANVE's own design system (the
web app's `styles.css`): IBM Plex Sans for words and Plex Mono for figures, quiet grey
surfaces, one violet tint, and "ink" for the appliance itself. Light and dark follow the phone.

- **Colours are roles** (`T.surface`, `T.card`, `T.ink`, `T.accent`, `T.box` ...), each with a
  light and a dark value. `python scripts/contrast.py` prints the contrast of every text
  colour against its surface and fails below 4.5:1.
- **The box is drawn as what it is**: a dark puck with its ring of 24 lights (`Orb`), in the
  colours the real ring uses (`anve_device/ring.py`): teal when ready, cyan listening,
  violet thinking, green speaking, red for microphone off or trouble.
- **Animations** (`src/motion.tsx`) come from LottieFiles and stand where an icon would:
  looking for the box, joining Wi-Fi, done, and music playing. They are recoloured in the
  app's own colours when played, so they follow light and dark. Sources and licence are in
  `assets/lottie/SOURCES.md`. The player is the app's own small module
  (`modules/anve-lottie`, Airbnb's Lottie for Android); on iPhone a plain icon is shown
  until that side is written.
- Everything that moves stands still when the phone asks for reduced motion.
- **Text follows the phone's text size.** Buttons, fields and rows have minimum heights,
  not fixed ones; checked at 160 %.
- **Controls** are at least 44 x 44, respond the moment they are touched, and every
  icon-only control has a spoken label. All three tabs show their name.
- Android's back button goes one step back in setup, and from Approvals or Profile to Home.

Screens from the Android emulator are in `screenshots/` (light and dark).

## Build (Windows)

```
powershell -ExecutionPolicy Bypass -File build-android.ps1
```

The result is `dist\anve-home.apk`. The script builds in `C:\a` because the
Android C++ tools fail on long folder paths; that folder can be deleted at any time.

## Test without a real account

```
python scripts/mock_anve.py            # a stand-in ANVE: demo / demo-pass-1
npx tsx scripts/box_live_test.ts <box address> <pairing code>   # the box client, from this computer
```

In the app's sign-in screen tap "change" and enter `http://10.0.2.2:8790` (emulator).

## iPhone

The same app, from the same code. Prepared and checked as far as a Windows machine can:
it type-checks and its iPhone JavaScript bundle builds (`npx expo export --platform ios`).
It has **not** been compiled into an iPhone app or run on an iPhone yet: that needs a Mac,
or Expo's build service (which builds on a Mac in the cloud).

What differs from Android:

- **Joining the box's Wi-Fi is done by hand.** iOS does not let an app list Wi-Fi
  networks. The app registers the box with ANVE first (while the phone still has
  internet), then asks the person to join the network starting with `ANVE-` in Settings
  and come back. Written, never run against a box. (Joining from inside the app is
  possible on iPhone with Apple's "Hotspot Configuration" permission, which needs a paid
  Apple developer account; not done yet.)
- **Animations** are played by the standard `lottie-react-native` (`src/player.ios.tsx`)
  and the screen's edges come from `react-native-safe-area-context` (`src/insets.ios.ts`).
  Both are left out of the Android build (`react-native.config.js`), which keeps using
  the app's own small modules.
- **Finding the box on the home Wi-Fi** uses its name (`anve-xxxx.local`); iOS asks once
  for permission to reach devices on the local network.
- The app's own Android modules (`modules/anve-wifi`, `modules/anve-lottie`) are not part
  of the iPhone build.

### Building it

Build profiles are in `eas.json`. With an Expo account (free), signed in on this machine
(`npx eas-cli@latest login`):

```bash
npx eas-cli@latest build --platform ios --profile simulator   # compiles it; runs in a Mac's iPhone simulator, no Apple account needed
npx eas-cli@latest build --platform ios --profile preview     # installs on your own iPhone; needs the Apple Developer Program (99 USD a year)
```

On a Mac with Xcode: `npx expo run:ios --device`.

To try it on an iPhone without any of that: install "Expo Go" from the App Store, run
`npx expo start` here, and scan the code it shows. That runs the app's screens and logic
inside Expo Go (same Wi-Fi as this laptop needed); it is a preview, not the installable app.
