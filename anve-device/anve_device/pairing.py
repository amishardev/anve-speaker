"""The cryptography between the phone app and the speaker. Two things:

1. A sealed message to the speaker (`Box`), used before the two share a secret: during
   first-time setup (the home Wi-Fi password and the device key travel over the setup
   hotspot, which is an open network) and when a phone is paired with a speaker that is
   already set up. The app makes a one-time X25519 key, both sides derive
        key = HKDF-SHA256(X25519(app, speaker), salt = speaker_pub | app_pub, info = INFO)
   and the message is ChaCha20-Poly1305 under that key. Someone listening on the radio
   cannot read it. It does NOT prove to the app that it is talking to the real speaker:
   an attacker who runs a fake hotspot with the same name at that moment could receive
   the message. (Closing that needs a secret printed on the box.)

2. The paired channel (`Channel`), used for everything afterwards. Speaker and app share
   a 32-byte token. Every request carries
        X-ANVE-Auth: v1 <unix time> <nonce> <HMAC-SHA256(token, canonical request)>
   and is refused when the time is more than 90 s off or the nonce was seen before.
   Request and reply bodies are encrypted with a key derived from the token, so on an
   open Wi-Fi nobody can read what is playing or change the volume.

The app side of both is implemented in the app (TypeScript); `seal`, `open_reply`,
`sign` and `Channel.open/seal` here are the reference, and the tests use them."""
import base64
import hashlib
import hmac
import json
import os
import time
from collections import deque

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

INFO = b"anve-setup-v1"
WINDOW_S = 90


class BadMessage(Exception):
    pass


def b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def unb64(s, n: int | None = None) -> bytes:
    try:
        b = base64.b64decode(str(s), validate=True)
    except Exception:                                        # noqa: BLE001
        raise BadMessage("base64") from None
    if n is not None and len(b) != n:
        raise BadMessage("length")
    return b


def _derive(shared: bytes, speaker_pub: bytes, app_pub: bytes) -> bytes:
    return HKDF(hashes.SHA256(), 32, salt=speaker_pub + app_pub, info=INFO).derive(shared)


def _aead_seal(key: bytes, obj: dict, aad: bytes) -> dict:
    n = os.urandom(12)
    ct = ChaCha20Poly1305(key).encrypt(n, json.dumps(obj, separators=(",", ":")).encode(), aad)
    return {"n": b64(n), "c": b64(ct)}


def _aead_open(key: bytes, msg: dict, aad: bytes) -> dict:
    if not isinstance(msg, dict):
        raise BadMessage("shape")
    try:
        obj = json.loads(ChaCha20Poly1305(key).decrypt(unb64(msg.get("n"), 12),
                                                       unb64(msg.get("c")), aad))
    except (InvalidTag, ValueError):
        raise BadMessage("decrypt") from None
    if not isinstance(obj, dict):
        raise BadMessage("shape")
    return obj


class Box:
    """The speaker's side of a sealed message. The key pair lives only in memory."""

    def __init__(self):
        self._sk = X25519PrivateKey.generate()
        self.public = self._sk.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)

    def open(self, msg: dict) -> tuple[dict, bytes]:
        """-> (what the app sent, the key to seal the reply with)"""
        if not isinstance(msg, dict):
            raise BadMessage("shape")
        epk = unb64(msg.get("k"), 32)
        try:
            shared = self._sk.exchange(X25519PublicKey.from_public_bytes(epk))
        except ValueError:
            raise BadMessage("key") from None
        key = _derive(shared, self.public, epk)
        return _aead_open(key, msg, INFO + b"|req"), key

    @staticmethod
    def reply(key: bytes, obj: dict) -> dict:
        return _aead_seal(key, obj, INFO + b"|rep")


def seal(speaker_pub: bytes, obj: dict) -> tuple[dict, bytes]:
    """App side: seal `obj` for the speaker. -> (message, key for the reply)"""
    sk = X25519PrivateKey.generate()
    epk = sk.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    key = _derive(sk.exchange(X25519PublicKey.from_public_bytes(speaker_pub)), speaker_pub, epk)
    return {"k": b64(epk), **_aead_seal(key, obj, INFO + b"|req")}, key


def open_reply(key: bytes, msg: dict) -> dict:
    return _aead_open(key, msg, INFO + b"|rep")


def new_token() -> str:
    return os.urandom(32).hex()


def canonical(method: str, target: str, ts: str, nonce: str, body: bytes) -> bytes:
    return f"{method}\n{target}\n{ts}\n{nonce}\n{hashlib.sha256(body).hexdigest()}".encode()


def sign(token: bytes, method: str, target: str, body: bytes = b"", *, ts: int | None = None,
         nonce: str | None = None) -> str:
    """App side: the value of the X-ANVE-Auth header."""
    ts = str(int(time.time()) if ts is None else ts)
    nonce = nonce or os.urandom(9).hex()
    mac = hmac.new(token, canonical(method, target, ts, nonce, body), hashlib.sha256).hexdigest()
    return f"v1 {ts} {nonce} {mac}"


class Channel:
    """The paired channel: checks request signatures, opens and seals bodies."""

    def __init__(self, token_hex: str):
        self.token = bytes.fromhex(token_hex.strip())
        if len(self.token) != 32:
            raise ValueError("the app token must be 32 bytes")
        self.key = hashlib.sha256(b"anve-app-enc-v1" + self.token).digest()
        self._seen: deque = deque(maxlen=2000)

    def check(self, header: str, method: str, target: str, body: bytes) -> str | None:
        """-> the request's nonce when the signature is good and fresh, else None."""
        p = (header or "").split(" ")
        if len(p) != 4 or p[0] != "v1" or not p[1].isdigit() or not (8 <= len(p[2]) <= 64):
            return None
        want = hmac.new(self.token, canonical(method, target, p[1], p[2], body),
                        hashlib.sha256).hexdigest()
        if not hmac.compare_digest(want, p[3]):
            return None
        if abs(time.time() - int(p[1])) > WINDOW_S or p[2] in self._seen:
            return None
        self._seen.append(p[2])
        return p[2]

    def open(self, body: bytes, nonce: str) -> dict:
        """A request body: {"n", "c"}, bound to this request's nonce."""
        if not body:
            return {}
        try:
            msg = json.loads(body)
        except ValueError:
            raise BadMessage("json") from None
        return _aead_open(self.key, msg, b"req|" + nonce.encode())

    def seal(self, obj: dict, nonce: str) -> dict:
        """A reply body, bound to the request it answers (it cannot be replayed as the
        answer to another request)."""
        return _aead_seal(self.key, obj, b"rep|" + nonce.encode())

    # app side, for the tests
    def seal_request(self, obj: dict, nonce: str) -> bytes:
        return json.dumps(_aead_seal(self.key, obj, b"req|" + nonce.encode())).encode()

    def open_reply(self, msg: dict, nonce: str) -> dict:
        return _aead_open(self.key, msg, b"rep|" + nonce.encode())
