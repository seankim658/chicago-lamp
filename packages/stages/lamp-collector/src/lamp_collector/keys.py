"""CTA API keys, kept out of every repr, error message and stored text.

Bus Tracker calls use the bus key and Train Tracker calls the train key. The
GTFS-Realtime key is optional, and without it those feeds are not called.
"""

import re
from dataclasses import dataclass, field
from enum import StrEnum

BUS_KEY_VARIABLE = "CTA_BUS_KEY"
TRAIN_KEY_VARIABLE = "CTA_TRAIN_KEY"
GTFSRT_KEY_VARIABLE = "CTA_GTFSRT_KEY"
REDACTED = "REDACTED"

# A key must be a long run of letters and digits. It then reads the same inside a
# URL as anywhere else, so plain substring replacement finds every copy of it,
# and a stray space or quote pasted into .env is caught here.
_KEY_PATTERN = re.compile(r"[A-Za-z0-9]{16,}")


class KeyKind(StrEnum):
    BUS = "bus"
    TRAIN = "train"
    GTFSRT = "gtfsrt"


class ApiKeyError(ValueError):
    """A key is malformed or missing. The message names the variable, never the key."""


@dataclass(frozen=True)
class ApiKeys:
    bus: str = field(repr=False)
    train: str = field(repr=False)
    gtfsrt: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        _check_key(BUS_KEY_VARIABLE, self.bus)
        _check_key(TRAIN_KEY_VARIABLE, self.train)
        if self.gtfsrt is not None:
            _check_key(GTFSRT_KEY_VARIABLE, self.gtfsrt)

    def has(self, kind: KeyKind) -> bool:
        return kind is not KeyKind.GTFSRT or self.gtfsrt is not None

    def for_kind(self, kind: KeyKind) -> str:
        if kind is KeyKind.BUS:
            return self.bus
        if kind is KeyKind.TRAIN:
            return self.train
        if self.gtfsrt is None:
            raise ApiKeyError(f"{GTFSRT_KEY_VARIABLE} is not set, so GTFS-Realtime can't be called")
        return self.gtfsrt

    def redact(self, text: str) -> str:
        """Return text with every copy of any key replaced by REDACTED."""
        for key in sorted(self._all(), key=len, reverse=True):
            text = text.replace(key, REDACTED)
        return text

    def _all(self) -> set[str]:
        keys = {self.bus, self.train}
        if self.gtfsrt is not None:
            keys.add(self.gtfsrt)
        return keys


def _check_key(variable: str, key: str) -> None:
    if not _KEY_PATTERN.fullmatch(key):
        raise ApiKeyError(
            f"{variable} must be 16 or more letters and digits only, "
            f"got {len(key)} characters (the value is not shown)"
        )
