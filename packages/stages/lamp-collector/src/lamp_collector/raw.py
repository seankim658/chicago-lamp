"""Raw responses saved to the buffer exactly as CTA sent them, gzipped.

A file's name says which feed, poll, call and attempt it came from, so the raw
archive can be read without any other record. The folder is the Chicago date of
the poll, and the file name is its UTC time.
"""

import gzip
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from lamp_storage import Store

from .feeds import Feed
from .fetch import FetchResult

CHICAGO = ZoneInfo("America/Chicago")

# The call index is written with two digits.
MAX_CALLS_PER_POLL = 100
ATTEMPTS = (1, 2)


class InvalidCallError(ValueError):
    """A call was described in a way no raw file name can hold."""


@dataclass(frozen=True)
class CallId:
    """Where one call attempt sits in the poll schedule.

    The poll time is the scheduled time in UTC, to the whole second, so every
    call of one poll shares it.
    """

    poll_time_utc: datetime
    call_index: int
    attempt: int

    def __post_init__(self) -> None:
        _check_poll_time(self.poll_time_utc)
        if not 0 <= self.call_index < MAX_CALLS_PER_POLL:
            raise InvalidCallError(
                f"call index must be 0 to {MAX_CALLS_PER_POLL - 1}, got {self.call_index}"
            )
        if self.attempt not in ATTEMPTS:
            raise InvalidCallError(f"attempt must be one of {ATTEMPTS}, got {self.attempt}")


def collection_date(poll_time_utc: datetime) -> date:
    """The Chicago date on which a poll was made."""
    return poll_time_utc.astimezone(CHICAGO).date()


def raw_key(feed: Feed, call: CallId) -> str:
    """The storage key of a call's raw response.

    The extension is the format the feed was asked for, even when CTA answered
    in another one.
    """
    folder = f"raw/{feed.value}/{collection_date(call.poll_time_utc).isoformat()}"
    stamp = call.poll_time_utc.strftime("%Y%m%dT%H%M%SZ")
    return f"{folder}/{stamp}-{call.call_index:02d}-{call.attempt}.{feed.extension}.gz"


class RawWriter:
    def __init__(self, store: Store) -> None:
        self._store = store

    def write(self, call: CallId, result: FetchResult) -> str | None:
        """Save the response body, whatever its status, and return its key.

        Return None when the call got no response, so there is nothing to save.
        Writing the same call again with the same body is safe. A different body
        for the same call raises ObjectConflictError, since a raw file is never
        replaced.
        """
        if result.body is None:
            return None
        key = raw_key(result.request.feed, call)
        self._store.put_new(key, gzip.compress(result.body, mtime=0))
        return key


def _check_poll_time(poll_time: datetime) -> None:
    if poll_time.utcoffset() != timedelta(0):
        raise InvalidCallError(f"poll time must be in UTC, got {poll_time.isoformat()}")
    if poll_time.microsecond:
        raise InvalidCallError(
            f"poll time must be a whole second, since file names drop fractions, "
            f"got {poll_time.isoformat()}"
        )
