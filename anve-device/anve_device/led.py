"""WS2812B strip on a Raspberry Pi 5, driven from SPI0 MOSI (GPIO10, pin 19).

rpi_ws281x does not work on the Pi 5 (its PWM/DMA tricks target the older SoCs), so the
800 kHz one-wire signal is made with SPI instead: the bus runs at 6.4 MHz and every
WS2812 bit is sent as one SPI byte, 0b11000000 for a 0 (about 0.31 us high) and
0b11111000 for a 1 (about 0.78 us high). Only MOSI is used; clock and chip-select are
ignored by the strip.

Brightness is capped in software (`max_brightness`, default 0.35): the strip is fed from
the Pi's 5 V pin, and 30 LEDs at full white would draw about 1.8 A.
"""
import threading

import numpy as np

SPI_HZ = 6_400_000
_BIT0, _BIT1 = 0xC0, 0xF8
# byte value -> its 8 SPI bytes, most significant bit first
_LUT = np.array([[_BIT1 if (v >> (7 - b)) & 1 else _BIT0 for b in range(8)]
                 for v in range(256)], dtype=np.uint8)
_LEAD = bytes(8)            # MOSI low before the first bit
_RESET = bytes(260)         # > 300 us low: latch (newer WS2812B need 280 us)


class Strip:
    """`count` RGB pixels. Colours are (r, g, b) in 0..255 before the brightness cap."""

    def __init__(self, count: int = 30, max_brightness: float = 0.35, bus: int = 0,
                 device: int = 0, order: str = "GRB", gamma: float = 2.2):
        import spidev
        self.count = int(count)
        self.max_brightness = max(0.0, min(1.0, float(max_brightness)))
        self.order = [("RGB".index(c)) for c in order.upper()]
        self.gamma = gamma
        self._lock = threading.Lock()
        self._spi = spidev.SpiDev()
        self._spi.open(bus, device)
        self._spi.max_speed_hz = SPI_HZ
        self._spi.mode = 0
        self._last = None

    def encode(self, pixels) -> bytes:
        """The SPI byte stream for an array of shape (count, 3), values 0..255."""
        px = np.asarray(pixels, dtype=np.float32).reshape(-1, 3)[:self.count]
        if px.shape[0] < self.count:
            px = np.vstack([px, np.zeros((self.count - px.shape[0], 3), np.float32)])
        px = np.clip(px, 0, 255) / 255.0
        px = np.power(px, self.gamma) * self.max_brightness * 255.0
        wire = np.rint(px[:, self.order]).astype(np.uint8).reshape(-1)
        return _LEAD + _LUT[wire].tobytes() + _RESET

    def show(self, pixels):
        data = self.encode(pixels)
        with self._lock:
            if data == self._last:
                return
            self._last = data
            # writebytes2 sends a large buffer in one transfer (no 4 KiB chunk gaps)
            self._spi.writebytes2(data)

    def fill(self, rgb):
        self.show(np.tile(np.asarray(rgb, dtype=np.float32), (self.count, 1)))

    def off(self):
        self._last = None
        self.fill((0, 0, 0))

    def close(self):
        try:
            self.off()
        finally:
            self._spi.close()
