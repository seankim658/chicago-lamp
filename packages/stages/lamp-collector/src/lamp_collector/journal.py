"""The coverage journal, one row per call attempt, in one file per UTC hour.

Every attempt gets a row, whether or not CTA answered, so a gap in the archive
can be told apart from a feed with nothing to report. The folder is the poll's
Chicago date, matching the raw files.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime

from lamp_storage import LocalStore

from .fetch import FetchResult
from .raw import CallId, collection_date
from .responses import Reading

JOURNAL_PREFIX = "coverage/journal/"


@dataclass(frozen=True)
class CallRecord:
    """One finished call attempt, with how it read and where its body was saved."""

    call: CallId
    result: FetchResult
    reading: Reading
    raw_path: str | None


def journal_key(poll_time_utc: datetime) -> str:
    """The journal file for a poll, named by the poll's hour, not the time of the call.

    A retry sent just after the hour still joins its poll's file.
    """
    folder = f"{JOURNAL_PREFIX}{collection_date(poll_time_utc).isoformat()}"
    return f"{folder}/{poll_time_utc.strftime('%Y%m%dT%HZ')}.jsonl"


def journal_row(record: CallRecord) -> dict[str, object]:
    call, result, reading = record.call, record.result, record.reading
    return {
        "feed": result.request.feed.value,
        "poll_time_utc": _timestamp(call.poll_time_utc),
        "call_index": call.call_index,
        "attempt": call.attempt,
        "request_url": result.request.public_url,
        "request_time_utc": _timestamp(result.request_time_utc),
        "response_time_utc": _timestamp(result.response_time_utc),
        "status": reading.status.value,
        "error": reading.error,
        "http_status": result.http_status,
        "vehicle_count": reading.vehicle_count,
        "raw_path": record.raw_path,
    }


class JournalWriter:
    def __init__(self, store: LocalStore) -> None:
        self._store = store

    def record(self, record: CallRecord) -> None:
        line = json.dumps(journal_row(record), separators=(",", ":"))
        self._store.append_line(journal_key(record.call.poll_time_utc), line.encode())

    def repair_newest(self) -> str | None:
        """Drop a row a crash left half-written in the newest journal file.

        Only the file being written when the collector stopped can hold one.
        Return that file's key when a row was dropped.
        """
        newest = max(self._store.list_keys(JOURNAL_PREFIX), default=None)
        if newest is None or not self._store.drop_torn_line(newest):
            return None
        return newest


def _timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat(timespec="microseconds")
