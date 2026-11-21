"""Opus for the ANVE socket, through libopus and ctypes (the same way the server does it).

Uplink:   16 kHz mono, one 40 ms packet (640 samples) per WebSocket binary frame.
Downlink: 24 kHz mono, 20 ms packets (480 samples).
If libopus is missing, `available()` is False and the device stays on PCM16.
"""
import ctypes
import ctypes.util

_lib = None
_tried = False
APP_VOIP = 2048
SET_BITRATE = 4002
SET_INBAND_FEC = 4012
SET_PACKET_LOSS_PERC = 4014


def _load():
    global _lib, _tried
    if _tried:
        return _lib
    _tried = True
    for name in (ctypes.util.find_library("opus") or "", "libopus.so.0",
                 "/usr/lib/aarch64-linux-gnu/libopus.so.0"):
        if not name:
            continue
        try:
            lib = ctypes.CDLL(name)
        except OSError:
            continue
        lib.opus_encoder_create.restype = ctypes.c_void_p
        lib.opus_encoder_create.argtypes = [ctypes.c_int32, ctypes.c_int, ctypes.c_int,
                                            ctypes.POINTER(ctypes.c_int)]
        lib.opus_encode.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int,
                                    ctypes.c_char_p, ctypes.c_int32]
        lib.opus_decoder_create.restype = ctypes.c_void_p
        lib.opus_decoder_create.argtypes = [ctypes.c_int32, ctypes.c_int,
                                            ctypes.POINTER(ctypes.c_int)]
        lib.opus_decode.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int32,
                                    ctypes.c_char_p, ctypes.c_int, ctypes.c_int]
        lib.opus_encoder_destroy.argtypes = [ctypes.c_void_p]
        lib.opus_decoder_destroy.argtypes = [ctypes.c_void_p]
        _lib = lib
        break
    return _lib


def available() -> bool:
    return _load() is not None


class Encoder:
    """16 kHz mono PCM16 in, one Opus packet out per call (20 or 40 ms of audio)."""

    def __init__(self, rate: int = 16000, bitrate: int = 24000):
        lib = _load()
        if lib is None:
            raise RuntimeError("libopus is not available")
        self.lib, self.rate = lib, rate
        err = ctypes.c_int()
        self.enc = lib.opus_encoder_create(rate, 1, APP_VOIP, ctypes.byref(err))
        if err.value or not self.enc:
            raise RuntimeError(f"opus_encoder_create failed ({err.value})")
        for req, val in ((SET_BITRATE, bitrate), (SET_INBAND_FEC, 1),
                         (SET_PACKET_LOSS_PERC, 5)):
            lib.opus_encoder_ctl(ctypes.c_void_p(self.enc), ctypes.c_int(req),
                                 ctypes.c_int32(val))
        self.buf = ctypes.create_string_buffer(1500)

    def encode(self, pcm16: bytes) -> bytes:
        n = self.lib.opus_encode(self.enc, pcm16, len(pcm16) // 2, self.buf, 1500)
        if n <= 0:
            raise RuntimeError(f"opus_encode failed ({n})")
        return self.buf.raw[:n]

    def close(self):
        if self.enc:
            self.lib.opus_encoder_destroy(self.enc)
            self.enc = None


class Decoder:
    """One Opus packet in, 24 kHz mono PCM16 out."""

    MAX = 2880                       # 120 ms at 24 kHz

    def __init__(self, rate: int = 24000):
        lib = _load()
        if lib is None:
            raise RuntimeError("libopus is not available")
        self.lib = lib
        err = ctypes.c_int()
        self.dec = lib.opus_decoder_create(rate, 1, ctypes.byref(err))
        if err.value or not self.dec:
            raise RuntimeError(f"opus_decoder_create failed ({err.value})")
        self.buf = ctypes.create_string_buffer(self.MAX * 2)

    def decode(self, packet: bytes) -> bytes:
        n = self.lib.opus_decode(self.dec, packet, len(packet), self.buf, self.MAX, 0)
        return self.buf.raw[:n * 2] if n > 0 else b""

    def close(self):
        if self.dec:
            self.lib.opus_decoder_destroy(self.dec)
            self.dec = None
