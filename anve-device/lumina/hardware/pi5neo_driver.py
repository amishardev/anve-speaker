"""The real strip: WS2812B on a Raspberry Pi 5, from SPI0 MOSI (GPIO10, pin 19).

The old PWM/DMA libraries (rpi_ws281x) do not work on the Pi 5. The signal is made with
SPI instead, the way the Pi5Neo library does it: the bus runs at 6.4 MHz and every LED bit
is sent as one SPI byte, 0b11000000 for a 0 (about 0.31 us high) and 0b11111000 for a 1
(about 0.78 us high). Only the data line is used. This file sends that same signal through
`spidev` directly, so nothing extra has to be installed.

Wiring this expects: the strip on its own 5 V supply (60 LEDs can draw far more than the
Pi's pins may give), grounds joined, data through a 74AHCT125 level shifter and a 330 ohm
resistor, a 1000 uF capacitor across the strip's supply.
"""
from __future__ import annotations

SPI_HZ = 6_400_000
_BIT0, _BIT1 = 0xC0, 0xF8
_LUT = [bytes(_BIT1 if (v >> (7 - b)) & 1 else _BIT0 for b in range(8)) for v in range(256)]
_LEAD = bytes(8)             # the line low before the first bit
_RESET = bytes(260)          # more than 300 us low: the LEDs take the new colours


def encode(wire: list) -> bytes:
    """The SPI bytes for a list of (a, b, c) values already in the strip's colour order."""
    lut = _LUT
    return _LEAD + b"".join(lut[a] + lut[b] + lut[c] for a, b, c in wire) + _RESET


class SpiStrip:
    name = "spi"

    def __init__(self, count: int = 60, bus: int = 0, device: int = 0):
        import spidev                                   # only on the Pi
        self.count = count
        self._spi = spidev.SpiDev()
        self._spi.open(bus, device)
        self._spi.max_speed_hz = SPI_HZ
        self._spi.mode = 0
        self.frames = 0
        self.closed = False

    def show(self, wire: list):
        # writebytes2 sends the whole frame in one transfer (no gaps the LEDs would read as an end)
        self._spi.writebytes2(encode(wire))
        self.frames += 1

    def off(self):
        self.show([(0, 0, 0)] * self.count)

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.off()
        finally:
            self._spi.close()


def open_strip(settings: dict):
    """The strip the settings ask for, and a note when that was not possible. Asking for
    the real strip and not getting it never stops LUMINA: it carries on in memory."""
    from .led_simulator import Simulator
    strip = settings["strip"]
    if strip["driver"] == "spi":
        try:
            return SpiStrip(strip["count"], strip["spi_bus"], strip["spi_device"]), ""
        except Exception as e:                          # noqa: BLE001  (no spidev, no device, no permission)
            return Simulator(strip["count"], strip["order"]), f"LED strip not available ({type(e).__name__}: {e})"
    return Simulator(strip["count"], strip["order"]), ""
