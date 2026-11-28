"""What the agent needs from its link to ANVE.

check() asks, once, whether ANVE can be reached; the answer (True or False)
arrives later through the listener. The real link (milestone 1.3) registers the
box, sends heartbeats and proves the box's identity on every call.
"""

from __future__ import annotations

from typing import Callable, Protocol


class CloudLink(Protocol):
    def listen(self, listener: Callable[[bool], None]) -> None: ...

    def check(self) -> None: ...

    def cancel(self) -> None: ...
