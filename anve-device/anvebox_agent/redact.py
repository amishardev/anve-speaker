"""Keeps secrets (Wi-Fi passwords, keys) out of logs, reprs and error messages."""

from __future__ import annotations

import hmac


class Secret:
    """Holds a secret string. Printing, logging or repr() shows only ***.

    Call reveal() only at the single point where the secret is handed to the
    system that needs it (for example NetworkManager).
    """

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "Secret(***)"

    def __str__(self) -> str:
        return "***"

    def __format__(self, spec: str) -> str:
        return "***"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Secret):
            return NotImplemented
        return hmac.compare_digest(self._value.encode(), other._value.encode())

    def __hash__(self) -> int:
        return hash(("Secret", self._value))

    def __len__(self) -> int:
        return len(self._value)

    def __bool__(self) -> bool:
        return bool(self._value)

    def __reduce__(self):  # never pickle a secret by accident
        raise TypeError("Secret values cannot be pickled")
