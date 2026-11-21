"""One renderer per thing the strip can show. Each takes a `Ctx` and returns a frame."""
from __future__ import annotations

from . import errors, listening, media, processing, ready, setup, speaking


class Levels:
    """Sound levels for the renderers, all 0..1 and already smoothed. The `_real` flags
    say whether a level comes from actual audio or nothing is known."""
    mic = out = bass = mid = treble = beat = 0.0
    mic_real = out_real = music_real = False


class Ctx:
    """What a renderer may use."""
    def __init__(self, n: int, t: float, colours: dict, levels: Levels, settings: dict,
                 variant: str = "", progress=None, entry: list | None = None):
        self.n, self.t, self.c, self.lv, self.s = n, t, colours, levels, settings
        self.variant, self.progress, self.entry = variant, progress, entry


RENDERERS = {
    "STARTING": setup.starting,
    "SETUP_REQUIRED": setup.required,
    "SETUP_IN_PROGRESS": setup.in_progress,
    "CONNECTING": setup.connecting,
    "READY": ready.ready,
    "WAKE_WORD_DETECTED": listening.wake,
    "LISTENING": listening.listening,
    "RECORDING": listening.recording,
    "PROCESSING": processing.processing,
    "THINKING": processing.thinking,
    "SEARCHING": processing.searching,
    "TOOL_EXECUTING": processing.tool,
    "SPEAKING": speaking.speaking,
    "MUSIC_PLAYING": media.playing,
    "MUSIC_PAUSED": media.paused,
    "MUTED": errors.muted,
    "DISCONNECTED": errors.disconnected,
    "ERROR": errors.error,
    "ALARM": errors.alarm,
    "SHUTTING_DOWN": errors.shutting_down,
    "OFF": errors.off,
}

# the animations a person can choose between, per state (the first is the default)
VARIANTS = {
    "READY": ["drift", "breathe", "dots", "glow", "off"],
    "LISTENING": ["waveform", "bars"],
    "THINKING": ["orbit", "flow"],
    "SPEAKING": ["waveform", "bars"],
    "MUSIC_PLAYING": ["smooth", "festival", "ambient", "rainbow"],
}

# looks that change slowly: these are drawn at a lower frame rate to save the processor
SLOW = {"READY", "MUTED", "DISCONNECTED", "MUSIC_PAUSED", "SETUP_REQUIRED", "OFF"}
