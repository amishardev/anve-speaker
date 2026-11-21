# ANVE smart speaker (Raspberry Pi 5)

A DIY smart speaker that is a client of the ANVE assistant platform, with its light
strip, its first-time setup and a phone app.

**Start with [ANVE-Speaker-Progress.pdf](ANVE-Speaker-Progress.pdf)**: one page on what is
built, what was measured and what is still open (21 Nov 2026).

| Folder | What it is |
|---|---|
| `anve-device/` | The firmware on the Pi: voice pipeline, wake word, ANVE client, music and commands, setup pages, the LUMINA light strip (`anve-device/lumina/`), services, tests. See its `README.md`, `MEASUREMENTS.md` and `CAPABILITIES.md`. |
| `anve-app/` | "ANVE Home", the phone app (React Native / Expo). See its `README.md`. |
| `deploy.sh` | Copies `anve-device/` to the Pi. |

This is a snapshot of the working folder. The step-by-step commit history of the firmware
is in the git repository on the Pi itself. No keys, passwords or box identity are in this
repository; they live in root-only files on the Pi.

Note: `anve-device/README.md` grew section by section. Its opening status, "What is
measured" and "Known limits" parts are dated 16 Nov 2026 and are older than the PDF; where
they disagree, the PDF and the later sections are current.

Public copy: files that belong to the ANVE Box team's private repository are left out (see `anve-device/anvebox_agent/VENDORED.md`), and addresses of the test network are replaced with placeholders.
