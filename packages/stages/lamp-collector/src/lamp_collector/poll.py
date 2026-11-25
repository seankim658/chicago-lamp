"""One poll of one feed, from its calls to their raw files and journal rows."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime

from .feeds import FeedRequest
from .fetch import Clock, Fetcher, FetchResult, utc_now
from .journal import CallRecord, JournalWriter
from .raw import CallId, RawWriter
from .responses import Status, read_response


@dataclass(frozen=True)
class Poll:
    """The calls one poll makes, and the last moment a failed call may be tried again.

    retry_by leaves room for a retry to finish before the next poll is due.
    """

    poll_time_utc: datetime
    requests: tuple[FeedRequest, ...]
    retry_by: datetime


@dataclass(frozen=True)
class CallOutcome:
    """Every attempt at one call of a poll, the first one first."""

    attempts: tuple[CallRecord, ...]

    @property
    def final(self) -> CallRecord:
        return self.attempts[-1]


class Recorder:
    """Save an attempt's raw response, then read it, then journal it."""

    def __init__(self, raw: RawWriter, journal: JournalWriter) -> None:
        self._raw = raw
        self._journal = journal

    def record(self, call: CallId, result: FetchResult) -> CallRecord:
        raw_path = self._raw.write(call, result)
        record = CallRecord(call, result, read_response(result), raw_path)
        self._journal.record(record)
        return record


class PollRunner:
    """Make every call of a poll at the same time, so one slow call can't delay the rest.

    A call that is not ok is tried once more, if retry_by has not passed. A
    storage failure is raised once every call has finished, so the other calls
    are still saved.
    """

    def __init__(self, fetcher: Fetcher, recorder: Recorder, clock: Clock = utc_now) -> None:
        self._fetcher = fetcher
        self._recorder = recorder
        self._clock = clock

    def run(self, poll: Poll) -> list[CallOutcome]:
        """Return each call's outcome, in the order of poll.requests."""
        if not poll.requests:
            return []
        with ThreadPoolExecutor(max_workers=len(poll.requests)) as pool:
            futures = [pool.submit(self._call, poll, index) for index in range(len(poll.requests))]
        return [future.result() for future in futures]

    def _call(self, poll: Poll, index: int) -> CallOutcome:
        first = self._attempt(poll, index, 1)
        if first.reading.status is Status.OK or self._clock() >= poll.retry_by:
            return CallOutcome((first,))
        return CallOutcome((first, self._attempt(poll, index, 2)))

    def _attempt(self, poll: Poll, index: int, attempt: int) -> CallRecord:
        call = CallId(poll.poll_time_utc, index, attempt)
        return self._recorder.record(call, self._fetcher.fetch(poll.requests[index]))
