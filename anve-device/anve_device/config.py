"""Configuration: defaults here, overridden by a TOML file (config.toml in the project
folder, or the path in $ANVE_DEVICE_CONFIG). The device key is NOT in this file: it lives
in a root-only file outside the project (see `key_file`)."""
import copy
import os
import tomllib
from pathlib import Path

DEFAULTS = {
    "server": {
        "url": "wss://app.anveai.com/api/assistant/ws",
        "engine": "live",            # live (cloud voice model) | local
        "mode": "wake",              # wake: this device spots the wake word
        "speak": True,
        "codec": "opus",             # opus if the server has it, else pcm16
        # systemd hands the key over as a credential; the fallback is the file itself
        "key_file": "/etc/anve-device/key",
    },
    "audio": {
        "mic": "UACDemo",            # substring of the microphone's PipeWire name
        "speaker": "SoundDrum",      # substring of the speaker's PipeWire name
        "bluetooth_mac": "",         # if the speaker is Bluetooth: reconnect to this address
        "speaker_volume": 0.40,      # above this the microphone clips on this hardware
        "noise_suppression": True,   # WebRTC NS inside the echo canceller
        "agc_target_db": -18.0,
        "agc_max_gain_db": 15.0,
        "jitter_ms": 80,
        "output_tail_ms": 300,       # speaker delay after the last sample (Bluetooth)
        "keepalive_s": 180,          # a silent tone this often, so the speaker stays on
    },
    "wake": {
        "model": "tiny.en",
        "threads": 2,
        # A phrase still being spoken is looked at once it is this long. 1.3 s cut a bare
        # "Hey ANVE" in the middle of the name; with 1.7 a bare phrase is decoded whole.
        "early_s": 1.7,
        "tokens": 24,                # how much text one decode may produce (see wake.Spotter)
        "word": "hey_anve",
        "barge_in": True,            # spot "stop" / "cancel" / the wake word while it talks
    },
    # the light strip: LUMINA (lumina/) draws it, the speaker reports its state to it.
    # [leds] is the older built-in driver, used only when lumina is switched off.
    "lumina": {"enabled": True, "port": 8741},
    "leds": {"enabled": True, "count": 30, "brightness": 0.35},
    # the link to the ANVE Box cloud (boxlink.py; github.com/ANVEAI/anve-box, protocol 1).
    # led: the Pi LED that plays the cloud's "blink" commands ("" = none)
    "boxlink": {"enabled": True, "cloud": "https://box-cloud.example", "led": "ACT"},
    "buttons": {"mute_gpio": 17, "action_gpio": 27, "tap_s": 0.25},
    "alarm": {"rounds": 3},
    # things the speaker does itself: its own browser, YouTube, free radio (skills.py)
    # duck: music volume kept while listening; announce: ANVE says "Playing ..." first
    # duck: the share of its volume music keeps while you talk to the speaker (it is
    # also muffled then, see skills.Media); announce: ANVE says "Playing ..." first
    # muffle = false: only turn the music down (if muffling ever leaves a song silent)
    "skills": {"enabled": True, "duck": 0.3, "muffle": True, "announce": True},
    # taking turns (turn.py): after the chime you have start_s to begin; the request
    # ends pause_s after your last word
    "listen": {"start_s": 4.0, "pause_s": 1.4},
    # while ANVE works: "tone" = a soft pulse, "voice" = spoken fillers, "off"
    "thinking": {"mode": "tone", "after_s": 0.6, "again_s": 6.0},
    # the phone app's API on the local network (appapi.py). The token it shares with
    # paired phones is root-only, like the device key, and arrives as a credential.
    "app": {"enabled": True, "host": "0.0.0.0", "port": 8738,
            "token_file": "/etc/anve-device/app-token"},
}


def load(path: str | None = None) -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    p = Path(path or os.environ.get("ANVE_DEVICE_CONFIG") or
             Path(__file__).resolve().parent.parent / "config.toml")
    if p.exists():
        with open(p, "rb") as f:
            user = tomllib.load(f)
        for section, values in user.items():
            if isinstance(values, dict) and section in cfg:
                cfg[section].update(values)
            else:
                cfg[section] = values
    cfg["_path"] = str(p)
    # under systemd the key arrives as a credential (LoadCredential=key:/etc/anve-device/key)
    cred = os.environ.get("CREDENTIALS_DIRECTORY")
    if cred and (Path(cred) / "key").exists():
        cfg["server"]["key_file"] = str(Path(cred) / "key")
    if cred and (Path(cred) / "app-token").exists():
        cfg["app"]["token_file"] = str(Path(cred) / "app-token")
    return cfg
