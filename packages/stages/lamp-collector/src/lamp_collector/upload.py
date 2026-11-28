"""One pass of copying buffered files to object storage.

Raw files are uploaded as soon as they are seen, since the collector writes
each one whole and never changes it. A journal file is uploaded once 5 minutes
have passed since its hour ended and it ends in a whole row. Every upload is
create-only, so an identical object already there counts as uploaded, and a
different one is reported and never replaced. Buffered files are never removed.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from lamp_storage import LocalStore, ObjectConflictError, StorageError

from .journal import JOURNAL_PREFIX
from .upload_log import UploadLog, upload_entry

logger = logging.getLogger(__name__)

UPLOADED_PREFIXES = ("raw/", JOURNAL_PREFIX)
JOURNAL_DELAY = timedelta(minutes=5)
MAX_FAILURES_IN_A_ROW = 3

_JOURNAL_NAME = "%Y%m%dT%HZ.jsonl"


class Archive(Protocol):
    def put_new(self, key: str, data: bytes) -> bool: ...


@dataclass(frozen=True)
class UploadReport:
    uploaded: int = 0
    already_there: int = 0
    waiting: int = 0
    failed: int = 0
    stopped_early: bool = False

    @property
    def ok(self) -> bool:
        return self.failed == 0


@dataclass
class _Tally:
    """Counts for a pass in progress."""

    uploaded: int = 0
    already_there: int = 0
    waiting: int = 0
    failed: int = 0
    failures_in_a_row: int = 0

    def report(self, *, stopped_early: bool) -> UploadReport:
        return UploadReport(
            self.uploaded, self.already_there, self.waiting, self.failed, stopped_early
        )


class Uploader:
    def __init__(
        self,
        buffer: LocalStore,
        archive: Archive,
        log: UploadLog,
        *,
        max_failures_in_a_row: int = MAX_FAILURES_IN_A_ROW,
    ) -> None:
        self._buffer = buffer
        self._archive = archive
        self._log = log
        self._max_failures_in_a_row = max_failures_in_a_row

    def run_pass(self, now: datetime) -> UploadReport:
        """Upload every file that is due and not yet logged, and log each one.

        The pass stops early after several failures in a row, since storage is
        then likely unreachable. Files that fail stay for the next pass.
        """
        done = self._log.entries()
        tally = _Tally()
        for key in self._buffered_keys():
            if key in done:
                continue
            self._upload_if_due(key, now, tally)
            if tally.failures_in_a_row >= self._max_failures_in_a_row:
                logger.error("stopped after %d failed uploads in a row", tally.failures_in_a_row)
                return self._finish(tally, stopped_early=True)
        return self._finish(tally, stopped_early=False)

    def _buffered_keys(self) -> list[str]:
        return [key for prefix in UPLOADED_PREFIXES for key in self._buffer.list_keys(prefix)]

    def _upload_if_due(self, key: str, now: datetime, tally: _Tally) -> None:
        try:
            due = _is_due(key, now)
        except ValueError as error:
            logger.error("%s", error)
            tally.failed += 1
            return
        if not due:
            tally.waiting += 1
            return
        data = self._buffer.get(key)
        if not _is_whole(key, data):
            tally.waiting += 1
            return
        self._upload(key, data, tally)

    def _upload(self, key: str, data: bytes, tally: _Tally) -> None:
        try:
            wrote = self._archive.put_new(key, data)
        except ObjectConflictError as error:
            logger.error("%s, so the buffered copy is kept and not uploaded", error)
            tally.failed += 1
            return
        except StorageError as error:
            logger.error("%s", error)
            tally.failed += 1
            tally.failures_in_a_row += 1
            return
        self._log.record(upload_entry(key, data))
        if wrote:
            tally.uploaded += 1
        else:
            tally.already_there += 1
        tally.failures_in_a_row = 0

    def _finish(self, tally: _Tally, *, stopped_early: bool) -> UploadReport:
        report = tally.report(stopped_early=stopped_early)
        logger.info(
            "uploaded %d, already there %d, waiting %d, failed %d",
            report.uploaded,
            report.already_there,
            report.waiting,
            report.failed,
        )
        return report


def _is_due(key: str, now: datetime) -> bool:
    """Raise ValueError for a journal file whose name gives no hour."""
    if not key.startswith(JOURNAL_PREFIX):
        return True
    name = key.rsplit("/", 1)[-1]
    try:
        hour = datetime.strptime(name, _JOURNAL_NAME).replace(tzinfo=UTC)
    except ValueError:
        raise ValueError(f"journal file {key!r} is not named for a UTC hour") from None
    return now >= hour + timedelta(hours=1) + JOURNAL_DELAY


def _is_whole(key: str, data: bytes) -> bool:
    """A journal file ending in half a row waits until the collector drops it."""
    if not key.startswith(JOURNAL_PREFIX) or data.endswith(b"\n"):
        return True
    logger.warning("journal file %s ends in half a row, so it waits", key)
    return False
