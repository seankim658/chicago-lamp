"""CTA API keys, kept out of every repr, error message and stored text.

CTA issues two keys. Bus Tracker calls use the bus key, and Train Tracker and
GTFS-Realtime calls use the train key.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

BUS_KEY_VARIABLE = "CTA_BUS_KEY"
TRAIN_KEY_VARIABLE = "CTA_TRAIN_KEY"
REDACTED = "REDACTED"

# A key must be a long run of letters and digits. It then reads the same inside a
# URL as anywhere else, so plain substring replacement finds every copy of it,
# and a stray space or quote pasted into .env is caught here.
_KEY_PATTERN = re.compile(r"[A-Za-z0-9]{16,}")


class KeyKind(StrEnum):
    BUS = "bus"
    TRAIN = "train"


class ApiKeyError(ValueError):
    """A key is missing or malformed. The message names the variable, never the key."""


@dataclass(frozen=True)
class ApiKeys:
    bus: str = field(repr=False)
    train: str = field(repr=False)

    def __post_init__(self) -> None:
        _check_key(BUS_KEY_VARIABLE, self.bus)
        _check_key(TRAIN_KEY_VARIABLE, self.train)

    @classmethod
    def from_environ(cls, environ: Mapping[str, str]) -> "ApiKeys":
        return cls(
            bus=_read_variable(environ, BUS_KEY_VARIABLE),
            train=_read_variable(environ, TRAIN_KEY_VARIABLE),
        )

    def for_kind(self, kind: KeyKind) -> str:
        if kind is KeyKind.BUS:
            return self.bus
        return self.train

    def redact(self, text: str) -> str:
        """Return text with every copy of either key replaced by REDACTED."""
        for key in sorted({self.bus, self.train}, key=len, reverse=True):
            text = text.replace(key, REDACTED)
        return text


def _read_variable(environ: Mapping[str, str], variable: str) -> str:
    value = environ.get(variable, "")
    if not value:
        raise ApiKeyError(f"{variable} is not set")
    return value


def _check_key(variable: str, key: str) -> None:
    if not _KEY_PATTERN.fullmatch(key):
        raise ApiKeyError(
            f"{variable} must be 16 or more letters and digits only, "
            f"got {len(key)} characters (the value is not shown)"
        )
