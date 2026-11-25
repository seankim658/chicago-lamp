"""Polls of one feed, on the wall clock, for as long as the collector runs.

Poll times are whole multiples of the feed's interval since midnight UTC, so a
30 second feed polls on :00 and :30 of every minute. A poll that runs late
skips the times it overran instead of catching up, and a poll time is never
used twice, even across a restart, so no raw file name is reused.
"""

import logging
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .feeds import Feed, FeedRequest
from .fetch import TIMEOUT_SECONDS, Clock, utc_now
from .poll import CallOutcome, Poll, PollRunner

logger = logging.getLogger(__name__)

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_DAY = timedelta(days=1)
_RETRY_MARGIN = timedelta(seconds=TIMEOUT_SECONDS)


class InvalidScheduleError(ValueError):
    """A feed was given an interval the wall clock can't line up with."""


@dataclass(frozen=True)
class FeedSchedule:
    """How often a feed is polled, and the calls each poll makes.

    requests is asked afresh for every poll, so it can follow a list that
    changes, such as the bus routes. A poll with no calls is skipped quietly,
    so a feed that has nothing to ask for says so itself.
    """

    feed: Feed
    interval: timedelta
    requests: Callable[[], tuple[FeedRequest, ...]]

    def __post_init__(self) -> None:
        if self.interval <= _RETRY_MARGIN or _DAY % self.interval:
            raise InvalidScheduleError(
                f"a feed's interval must divide a day evenly and be longer than the "
                f"{TIMEOUT_SECONDS:g} s call timeout, got {self.interval} for {self.feed.value}"
            )


@dataclass(frozen=True)
class PollReport:
    feed: Feed
    poll_time_utc: datetime
    outcomes: list[CallOutcome]


PollListener = Callable[[PollReport], None]


def next_poll_time(now: datetime, interval: timedelta) -> datetime:
    """The first poll time strictly after now."""
    return _EPOCH + ((now - _EPOCH) // interval + 1) * interval


def retry_deadline(poll_time: datetime, interval: timedelta) -> datetime:
    """The last moment a retry can start and still finish before the next poll."""
    return poll_time + interval - _RETRY_MARGIN


class Pacer:
    """Tell the time and wait for a moment, until told to stop."""

    def __init__(self, clock: Clock = utc_now) -> None:
        self._clock = clock
        self._stopped = threading.Event()

    def now(self) -> datetime:
        return self._clock()

    def stop(self) -> None:
        self._stopped.set()

    def wait_until(self, moment: datetime) -> bool:
        """Return True once moment has come, or False as soon as stop is called."""
        while (remaining := (moment - self._clock()).total_seconds()) > 0:
            if self._stopped.wait(remaining):
                return False
        return not self._stopped.is_set()


class FeedLoop:
    """Poll one feed until the pacer stops, handing every finished poll to the listeners.

    A poll that raises is logged and the loop goes on, so one bad poll can't
    stop the feed.
    """

    def __init__(
        self,
        schedule: FeedSchedule,
        runner: PollRunner,
        pacer: Pacer,
        listeners: Sequence[PollListener] = (),
    ) -> None:
        self._schedule = schedule
        self._runner = runner
        self._pacer = pacer
        self._listeners = tuple(listeners)

    def run(self) -> None:
        interval = self._schedule.interval
        poll_time = next_poll_time(self._pacer.now(), interval)
        while self._pacer.wait_until(poll_time):
            self._poll_safely(poll_time)
            following = next_poll_time(self._pacer.now(), interval)
            self._log_skipped(poll_time, following)
            poll_time = following

    def _poll_safely(self, poll_time: datetime) -> None:
        try:
            self._poll(poll_time)
        except Exception:
            logger.exception("%s poll at %s failed", self._schedule.feed, poll_time.isoformat())

    def _poll(self, poll_time: datetime) -> None:
        requests = self._schedule.requests()
        if not requests:
            return
        retry_by = retry_deadline(poll_time, self._schedule.interval)
        outcomes = self._runner.run(Poll(poll_time, requests, retry_by))
        report = PollReport(self._schedule.feed, poll_time, outcomes)
        for listener in self._listeners:
            listener(report)

    def _log_skipped(self, poll_time: datetime, following: datetime) -> None:
        skipped = (following - poll_time) // self._schedule.interval - 1
        if skipped:
            logger.warning(
                "%s poll at %s ran past %d later poll time(s), which were skipped",
                self._schedule.feed,
                poll_time.isoformat(),
                skipped,
            )
