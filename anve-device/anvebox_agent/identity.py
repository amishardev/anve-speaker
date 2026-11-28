"""Who the box is.

- `device_id`: printed on the card; the last 4 characters name its setup Wi-Fi.
- `setup_password`: the setup Wi-Fi's password, printed on the card.
- `claim_secret`: one-time proof that you hold the box, behind the card's QR
  code and the setup page's link. The cloud stores only a hash of it.
- `device_key`: the box's private key. It signs the cloud's challenge on every
  connection (docs/box-cloud-agreement.md, part A) and never leaves the box.
  The cloud stores only the public half.

On a real box the factory script writes these to /etc/anvebox/identity.json,
readable by root only. The simulator makes a pretend identity of the same shape.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .redact import Secret

# Crockford base32: no I, L, O or U, so an ID read off a printed card is unambiguous.
ID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
# Typed from a card on a phone: lowercase, and no characters that look alike.
PASSWORD_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"

LINK_SIGNING_PREFIX = "anvebox-link-v1"
# Talking out loud through the box (part C of the agreement): a different text
# from the link's, so an answer to one challenge can't stand in for the other.
VOICE_SIGNING_PREFIX = "anvebox-voice-v1"
# Recording a meeting through the box (part D): its own text again.
MEETING_SIGNING_PREFIX = "anvebox-meeting-v1"
CLAIM_HASH_PREFIX = "anvebox-claim-v1"


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64u_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def claim_hash(claim_secret: str) -> str:
    """What the cloud stores instead of the claim secret."""
    return hashlib.sha256(f"{CLAIM_HASH_PREFIX}:{claim_secret}".encode()).hexdigest()


def link_message(nonce: str, device_id: str) -> bytes:
    """The exact bytes a box signs to answer the cloud's challenge."""
    return f"{LINK_SIGNING_PREFIX}:{nonce}:{device_id}".encode()


def voice_message(nonce: str, device_id: str) -> bytes:
    """The exact bytes a box signs to open a voice talk (box voice)."""
    return f"{VOICE_SIGNING_PREFIX}:{nonce}:{device_id}".encode()


def meeting_message(nonce: str, device_id: str) -> bytes:
    """The exact bytes a box signs to record a meeting (part D)."""
    return f"{MEETING_SIGNING_PREFIX}:{nonce}:{device_id}".encode()


@dataclass(frozen=True)
class Identity:
    device_id: str
    setup_password: Secret
    claim_secret: Secret
    device_key: Secret  # base64url of the raw 32-byte Ed25519 private key

    @property
    def short_id(self) -> str:
        return self.device_id[-4:]

    @property
    def setup_ssid(self) -> str:
        return f"ANVE-Setup-{self.short_id}"

    def _private_key(self) -> Ed25519PrivateKey:
        return Ed25519PrivateKey.from_private_bytes(b64u_decode(self.device_key.reveal()))

    @property
    def public_key(self) -> str:
        raw = self._private_key().public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        return b64u(raw)

    def sign_challenge(self, nonce: str) -> str:
        return b64u(self._private_key().sign(link_message(nonce, self.device_id)))

    def sign_voice_challenge(self, nonce: str) -> str:
        return b64u(self._private_key().sign(voice_message(nonce, self.device_id)))

    def sign_meeting_challenge(self, nonce: str) -> str:
        return b64u(self._private_key().sign(meeting_message(nonce, self.device_id)))

    def registration_record(self) -> dict:
        """Everything the cloud needs to know about this box. Public only."""
        return {
            "device_id": self.device_id,
            "public_key": self.public_key,
            "claim_hash": claim_hash(self.claim_secret.reveal()),
        }


def _random(alphabet: str, length: int) -> str:
    return "".join(secrets.choice(alphabet) for _ in range(length))


def new_claim_secret() -> str:
    """20 Crockford characters: about 100 bits, so it can't be guessed."""
    return _random(ID_ALPHABET, 20)


def _new_device_key() -> str:
    raw = Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    )
    return b64u(raw)


def new_identity() -> Identity:
    password = "-".join(_random(PASSWORD_ALPHABET, 4) for _ in range(3))
    return Identity(
        device_id=_random(ID_ALPHABET, 10),
        setup_password=Secret(password),
        claim_secret=Secret(new_claim_secret()),
        device_key=Secret(_new_device_key()),
    )


def save(identity: Identity, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "device_id": identity.device_id,
        "setup_password": identity.setup_password.reveal(),
        "claim_secret": identity.claim_secret.reveal(),
        "device_key": identity.device_key.reveal(),
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def load(path: Path) -> Identity:
    """Load an identity file that must already exist (a real box's, made by the
    factory). Raises OSError, ValueError or KeyError if it's missing or incomplete."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return Identity(
        device_id=data["device_id"],
        setup_password=Secret(data["setup_password"]),
        claim_secret=Secret(data["claim_secret"]),
        device_key=Secret(data["device_key"]),
    )


def load_or_create(path: Path) -> Identity:
    """Load the identity file, creating it if missing. An older file without a
    claim secret or key (from milestone 1.1) gets them added, keeping its ID."""
    if not path.exists():
        identity = new_identity()
        save(identity, path)
        return identity
    data = json.loads(path.read_text(encoding="utf-8"))
    identity = Identity(
        device_id=data["device_id"],
        setup_password=Secret(data["setup_password"]),
        claim_secret=Secret(data.get("claim_secret") or new_claim_secret()),
        device_key=Secret(data.get("device_key") or _new_device_key()),
    )
    if "claim_secret" not in data or "device_key" not in data:
        save(identity, path)
    return identity
