"""A check of the buffer for gaps in collection and for the disk filling up.

It only reads files, so it runs beside the collector without touching it. A
feed is healthy while its newest successful poll is under 10 minutes old. A
poll succeeds when every one of its calls ended ok, on either attempt.
"""

import json
import shutil
from collections import defaultdict
from collections.abc import Collection, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from lamp_storage import LocalStore

from .feeds import Feed
from .journal import journal_key
from .responses import Status

MAX_GAP = timedelta(minutes=10)
MIN_FREE_BYTES = 10 * 10**9


class InvalidJournalRowError(ValueError):
    """A journal row lacks a field the check reads, or holds the wrong type."""


@dataclass(frozen=True)
class _Attempt:
    feed: str
    poll_time_utc: datetime
    call_index: int
    ok: bool


class HealthCheck:
    def __init__(
        self,
        store: LocalStore,
        buffer_dir: Path,
        feeds: Collection[Feed],
        *,
        min_free_bytes: int = MIN_FREE_BYTES,
    ) -> None:
        self._store = store
        self._buffer_dir = buffer_dir
        self._feeds = sorted(feeds)
        self._min_free_bytes = min_free_bytes

    def problems(self, now: datetime) -> list[str]:
        """Describe each problem found, or return an empty list when all is well."""
        if not self._buffer_dir.is_dir():
            return [f"buffer: {self._buffer_dir} does not exist"]
        problems = self._feed_problems(now)
        disk = self._disk_problem()
        if disk is not None:
            problems.append(disk)
        return problems

    def _feed_problems(self, now: datetime) -> list[str]:
        newest = _newest_successes(self._recent_attempts(now))
        problems: list[str] = []
        for feed in self._feeds:
            success = newest.get(feed.value)
            if success is None:
                problems.append(f"{feed}: no successful poll in the last 10 minutes")
            elif now - success > MAX_GAP:
                problems.append(f"{feed}: no successful poll since {success:%H:%M:%S} UTC")
        return problems

    def _disk_problem(self) -> str | None:
        free = shutil.disk_usage(self._buffer_dir).free
        if free >= self._min_free_bytes:
            return None
        return (
            f"disk: {free / 10**9:.1f} GB free under {self._buffer_dir}, "
            f"below {self._min_free_bytes / 10**9:.0f} GB"
        )

    def _recent_attempts(self, now: datetime) -> Iterator[_Attempt]:
        """Read the journal files that can hold a poll from the last 10 minutes."""
        keys = sorted({journal_key(_hour(now - MAX_GAP)), journal_key(_hour(now))})
        for key in keys:
            if not self._store.exists(key):
                continue
            # The collector may be partway through appending the last line.
            for line in self._store.get(key).split(b"\n")[:-1]:
                yield _attempt(json.loads(line))


def _newest_successes(attempts: Iterator[_Attempt]) -> dict[str, datetime]:
    """Map each feed to the time of its newest poll in which every call ended ok."""
    calls_ok: defaultdict[tuple[str, datetime], dict[int, bool]] = defaultdict(dict)
    for attempt in attempts:
        calls = calls_ok[(attempt.feed, attempt.poll_time_utc)]
        calls[attempt.call_index] = calls.get(attempt.call_index, False) or attempt.ok
    newest: dict[str, datetime] = {}
    for (feed, poll_time), calls in calls_ok.items():
        if all(calls.values()) and (feed not in newest or poll_time > newest[feed]):
            newest[feed] = poll_time
    return newest


def _attempt(row: object) -> _Attempt:
    if not isinstance(row, dict):
        raise InvalidJournalRowError(f"a journal row must be an object, got {row!r}")
    feed, poll_time = row.get("feed"), row.get("poll_time_utc")
    call_index, status = row.get("call_index"), row.get("status")
    if not isinstance(feed, str) or not isinstance(poll_time, str):
        raise InvalidJournalRowError(f"a journal row needs a feed and poll time, got {row!r}")
    if not isinstance(call_index, int) or not isinstance(status, str):
        raise InvalidJournalRowError(f"a journal row needs a call index and status, got {row!r}")
    return _Attempt(feed, datetime.fromisoformat(poll_time), call_index, status == Status.OK)


def _hour(moment: datetime) -> datetime:
    return moment.replace(minute=0, second=0, microsecond=0)
