"""CloudClaim: who owns the box, as the cloud records it, and the box's claim
secret (decisions D14 and D15).

- The box keeps its own copy of "claimed", so it knows even when offline.
- The claim secret behind the setup page's link changes after every claim,
  so no link works twice, and after every factory reset. The box sends the
  cloud only its hash.
- A factory reset marks the box "was reset" until the cloud has heard it, so
  the cloud releases the old owner even if the reset happened offline.

The identity file's claim secret (the one on the printed card) is only the
first one.
"""

from __future__ import annotations

from typing import Callable, Protocol

from ..identity import claim_hash, new_claim_secret
from ..model import OwnerChanged
from .link import WebSocketLink


class ClaimDisk(Protocol):
    """Where the box keeps its claim state (in the simulator: SimStore.disk)."""

    claimed: bool
    claim_secret: str | None
    was_reset: bool


class CloudClaim:
    def __init__(
        self,
        *,
        disk: ClaimDisk,
        save: Callable[[], None],
        first_secret: str,
        link: WebSocketLink,
    ) -> None:
        self._disk = disk
        self._save = save
        if not disk.claim_secret:
            disk.claim_secret = first_secret
            save()
        self._listener: Callable[[object], None] = lambda event: None
        self._link = link
        link.attach_claim(self)

    # ------------------------------------------------------------ for the agent (ClaimAuthority)

    def listen(self, listener: Callable[[object], None]) -> None:
        self._listener = listener

    def is_claimed(self) -> bool:
        return self._disk.claimed

    def refresh(self) -> None:
        pass  # the cloud pushes owner changes; there is nothing to ask

    def forget(self) -> None:
        """Factory reset: no owner, a fresh claim secret, and a note for the
        cloud. A box still connected (by cable) reconnects to deliver it now."""
        was_claimed = self._disk.claimed
        self._disk.claimed = False
        self._disk.claim_secret = new_claim_secret()
        self._disk.was_reset = True
        self._save()
        self._link.reconnect()
        if was_claimed:
            self._listener(OwnerChanged(False))

    @property
    def current_secret(self) -> str:
        return self._disk.claim_secret or ""

    # ------------------------------------------------------------ for the link

    def hello_fields(self) -> dict:
        return {"claim_hash": claim_hash(self.current_secret), "was_reset": self._disk.was_reset}

    def on_welcome(self, sent: dict) -> None:
        """The cloud has read the hello that carried `sent`. If that hello told
        it about the latest reset, the note has done its job. (A reset after
        the hello made a new secret, so the hashes differ and the note stays.)"""
        if (
            self._disk.was_reset
            and sent.get("was_reset") is True
            and sent.get("claim_hash") == claim_hash(self.current_secret)
        ):
            self._disk.was_reset = False
            self._save()

    def on_owner(self, claimed: bool, rotate: bool) -> str | None:
        """The cloud's word on the owner. Returns the new claim secret's hash
        when the cloud asked for a new secret."""
        new_hash = None
        if rotate:
            self._disk.claim_secret = new_claim_secret()
            new_hash = claim_hash(self._disk.claim_secret)
        changed = claimed != self._disk.claimed
        self._disk.claimed = claimed
        if rotate or changed:
            self._save()
        if changed:
            self._listener(OwnerChanged(claimed))
        return new_hash
