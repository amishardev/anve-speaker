"""The box's modes (states), what can happen to it (events) and what it asks
its hardware to do in response (commands).

Plain data only. The rules that connect them live in machine.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .redact import Secret


class State(str, Enum):
    SETUP = "SETUP"
    CONNECTING = "CONNECTING"
    ONLINE_UNCLAIMED = "ONLINE_UNCLAIMED"
    READY = "READY"
    OFFLINE = "OFFLINE"
    RESETTING = "RESETTING"


class SetupError(str, Enum):
    """Why a new Wi-Fi network didn't work. The setup page explains each one."""

    WRONG_PASSWORD = "wrong_password"
    NOT_FOUND = "not_found"
    NO_ANSWER = "no_answer"
    NO_ADDRESS = "no_address"


class OfflineReason(str, Enum):
    NO_NETWORK = "no_network"  # no Wi-Fi or cable connection at all
    ANVE_UNREACHABLE = "anve_unreachable"  # on a network, but it can't reach ANVE


class Attempt(str, Enum):
    NEW = "new"  # Wi-Fi details just typed on the setup page
    SAVED = "saved"  # whatever the box already has: its saved Wi-Fi or a cable


class Timer(str, Enum):
    HANDOVER = "handover"  # lets the phone get its answer before the setup Wi-Fi goes
    CONNECT = "connect"  # gives up on a connection attempt
    OFFLINE_GRACE = "offline_grace"  # how long a lost connection lasts before OFFLINE
    SETUP_IDLE = "setup_idle"  # setup opened but unused: back to the saved Wi-Fi
    RESET_SHOW = "reset_show"  # keeps the reset flash visible before setup starts


# ---------------------------------------------------------------- events


@dataclass(frozen=True)
class Booted:
    """The agent has started: power on, restart, or recovery after a crash."""


@dataclass(frozen=True)
class WifiDetailsReceived:
    """A network name and password arrived. Today from the setup page; later
    from Bluetooth too, through this same event."""

    ssid: str
    password: Secret


@dataclass(frozen=True)
class JoinSucceeded:
    """The network being tried is up and the box has an address on it."""


@dataclass(frozen=True)
class JoinFailed:
    reason: SetupError


@dataclass(frozen=True)
class NetworkUp:
    """Some network connection (Wi-Fi or cable) is up."""

    via: str = "wifi"


@dataclass(frozen=True)
class NetworkDown:
    """No network connection is left."""


@dataclass(frozen=True)
class AnveReachable:
    """ANVE's servers answered."""


@dataclass(frozen=True)
class AnveUnreachable:
    """ANVE's servers didn't answer, or the network blocked them."""


@dataclass(frozen=True)
class OwnerChanged:
    """The claim layer says the box now has an owner, or no longer has one."""

    claimed: bool


@dataclass(frozen=True)
class HoldForSetup:
    """The button was held for 5 s and let go before 10 s."""


@dataclass(frozen=True)
class HoldForReset:
    """The button has been held for 10 s."""


@dataclass(frozen=True)
class TimerFired:
    timer: Timer


Event = (
    Booted
    | WifiDetailsReceived
    | JoinSucceeded
    | JoinFailed
    | NetworkUp
    | NetworkDown
    | AnveReachable
    | AnveUnreachable
    | OwnerChanged
    | HoldForSetup
    | HoldForReset
    | TimerFired
)


# ---------------------------------------------------------------- commands


@dataclass(frozen=True)
class SetLight:
    pattern: str


@dataclass(frozen=True)
class StartHotspot:
    """Scan for networks, then open the setup Wi-Fi and the setup page."""


@dataclass(frozen=True)
class StopHotspot:
    """Close the setup Wi-Fi and the setup page."""


@dataclass(frozen=True)
class JoinNew:
    ssid: str
    password: Secret


@dataclass(frozen=True)
class JoinSaved:
    """Bring up whatever the box already has: a cable, or its saved Wi-Fi."""


@dataclass(frozen=True)
class CancelJoin:
    pass


@dataclass(frozen=True)
class CheckAnve:
    """Ask the cloud link to check, now, whether ANVE can be reached."""


@dataclass(frozen=True)
class StartTimer:
    timer: Timer
    seconds: float


@dataclass(frozen=True)
class CancelTimer:
    timer: Timer


@dataclass(frozen=True)
class WipeEverything:
    """Factory reset: forget Wi-Fi, ownership and the device token."""


Command = (
    SetLight
    | StartHotspot
    | StopHotspot
    | JoinNew
    | JoinSaved
    | CancelJoin
    | CheckAnve
    | StartTimer
    | CancelTimer
    | WipeEverything
)
