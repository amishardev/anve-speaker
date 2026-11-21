"""Where the assistant's messages arrive: a UDP socket on this machine only.

UDP because the sender must never wait: a datagram to a port nobody listens on is simply
lost, so a stopped LUMINA cannot slow the assistant down. Only 127.0.0.1 is listened on,
so only programs on this machine can speak here.
"""
from __future__ import annotations

import asyncio
import json

TYPES = ("event", "state", "status", "level", "pattern")


def parse(data: bytes) -> dict | None:
    """A datagram as a bus message, or None when it is not one."""
    if len(data) > 4096:
        return None
    try:
        m = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(m, dict) or m.get("type") not in TYPES:
        return None
    if not isinstance(m.get("meta", {}), dict):
        m["meta"] = {}
    m["source"] = str(m.get("source") or "assistant")[:24]
    return m


class _Protocol(asyncio.DatagramProtocol):
    def __init__(self, publish):
        self.publish = publish
        self.bad = 0

    def datagram_received(self, data, addr):
        m = parse(data)
        if m is None:
            self.bad += 1
        else:
            self.publish(m)

    def error_received(self, exc):               # ICMP errors and the like: nothing to do
        pass


async def listen(publish, host: str = "127.0.0.1", port: int = 8741):
    """Returns (transport, protocol); close the transport to stop."""
    loop = asyncio.get_running_loop()
    return await loop.create_datagram_endpoint(lambda: _Protocol(publish), local_addr=(host, port))
