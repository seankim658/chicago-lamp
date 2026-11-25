"""A log line per feed every 10 minutes, saying how its polls went.

Windows follow poll times, so a line covers whole polls. A window's lines are
written when the first poll of the next window finishes. A feed expected to
poll every few seconds that had no poll in a window gets a warning instead.
"""

import logging
import threading
from collections import Counter
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .feeds import Feed
from .responses import Status
from .schedule import PollReport, next_poll_time

logger = logging.getLogger(__name__)

WINDOW = timedelta(minutes=10)


@dataclass
class _Tally:
    polls: int = 0
    calls: int = 0
    attempts: int = 0
    failed: Counter[Status] = field(default_factory=Counter)

    def add(self, report: PollReport) -> None:
        self.polls += 1
        for outcome in report.outcomes:
            self.calls += 1
            self.attempts += len(outcome.attempts)
            if outcome.final.reading.status is not Status.OK:
                self.failed[outcome.final.reading.status] += 1

    def describe(self) -> str:
        ok = self.calls - self.failed.total()
        text = (
            f"{self.polls} polls, {self.calls} calls, {ok} ok, {self.attempts - self.calls} retries"
        )
        if self.failed:
            failures = ", ".join(
                f"{status} {count}" for status, count in sorted(self.failed.items())
            )
            text += f", failed after retry: {failures}"
        return text


class PollSummary:
    """Pass it to each feed loop as a listener."""

    def __init__(self, expected: Collection[Feed]) -> None:
        self._expected = frozenset(expected)
        self._lock = threading.Lock()
        self._window: datetime | None = None
        self._tallies: dict[Feed, _Tally] = {}

    def __call__(self, report: PollReport) -> None:
        window = _window_start(report.poll_time_utc)
        with self._lock:
            if self._window is None:
                self._window = window
            elif window > self._window:
                self._write(self._window)
                self._window, self._tallies = window, {}
            self._tallies.setdefault(report.feed, _Tally()).add(report)

    def _write(self, start: datetime) -> None:
        period = f"{start:%H:%M} to {start + WINDOW:%H:%M} UTC"
        for feed in sorted(self._expected | self._tallies.keys()):
            tally = self._tallies.get(feed)
            if tally is None:
                logger.warning("%s, %s: no polls", feed, period)
            else:
                logger.info("%s, %s: %s", feed, period, tally.describe())


def _window_start(poll_time: datetime) -> datetime:
    return next_poll_time(poll_time, WINDOW) - WINDOW
