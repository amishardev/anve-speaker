"""A strip that exists only in memory: for tests, for the dashboard preview, and for
running LUMINA before the real strip is wired or switched on."""
from __future__ import annotations


class Simulator:
    name = "simulator"

    def __init__(self, count: int = 60, order: str = "GRB"):
        self.count, self.order = count, order
        self.frames = 0
        self.last: list = [(0, 0, 0)] * count
        self.closed = False

    def show(self, wire: list):
        if self.closed:
            raise RuntimeError("the strip is closed")
        self.last = list(wire)
        self.frames += 1

    def rgb(self) -> list:
        """The last frame as (r, g, b), whatever the wire order is."""
        idx = ["RGB".index(ch) for ch in self.order.upper()]
        out = []
        for px in self.last:
            c = [0, 0, 0]
            for pos, channel in enumerate(idx):
                c[channel] = px[pos]
            out.append(tuple(c))
        return out

    def off(self):
        self.show([(0, 0, 0)] * self.count)

    def close(self):
        if not self.closed:
            self.off()
            self.closed = True


def ansi(rgb_frame: list) -> str:
    """One line of coloured blocks for a terminal that knows 24-bit colour."""
    return "".join(f"\x1b[48;2;{r};{g};{b}m " for r, g, b in rgb_frame) + "\x1b[0m"
