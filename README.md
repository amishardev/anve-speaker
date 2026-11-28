# ANVE smart speaker (Raspberry Pi 5)

A DIY smart speaker that is a client of the ANVE assistant platform, with its light
strip, its first-time setup and a phone app.

**Start with [docs/PROJECT.md](docs/PROJECT.md)**: the whole project in one place, from the
first sound on 16 Nov 2026 to today: what was built, what was measured, what works, what
does not, and what was learned. [ANVE-Speaker-Progress.pdf](ANVE-Speaker-Progress.pdf) is the
older one-page summary (21 Nov 2026).

| Folder | What it is |
|---|---|
| `anve-device/` | The firmware on the Pi: voice pipeline, wake word, ANVE client, music and commands, setup pages, the LUMINA light strip (`anve-device/lumina/`), services, tests. See its `README.md`, `MEASUREMENTS.md` and `CAPABILITIES.md`. |
| `anve-app/` | "ANVE Home", the phone app (React Native / Expo). See its `README.md`. |
| `deploy.sh` | Copies `anve-device/` to the Pi. |
| `docs/` | The project's history and state (`PROJECT.md`) and reports to the ANVE Box team (`reports/`). |

This is a snapshot of the working folder. The step-by-step commit history of the firmware
is in the git repository on the Pi itself. No keys, passwords or box identity are in this
repository; they live in root-only files on the Pi.

Note: `anve-device/README.md` grew section by section. Its opening status, "What is
measured" and "Known limits" parts are dated 16 Nov 2026 and are older than the PDF; where
they disagree, the PDF and the later sections are current.

Since 28 Nov 2026 the ANVE Box team's own code that the speaker uses (`anve-device/anvebox_agent/`,
three of their tools and the setup page's assets) is included, copied unchanged; see
`anve-device/anvebox_agent/VENDORED.md`. The team also commits here (protocol 2, 28 Nov).
Addresses of the real device and its network are replaced with placeholders. Voice
recordings and training data are not in the repository.
