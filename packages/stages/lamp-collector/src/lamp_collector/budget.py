"""A count of each key's calls per day, against CTA's daily limit.

A day here is a Chicago date. CTA doesn't say when its count resets, so a
warning near midnight may be early or late by the difference.
"""

import logging
import threading
from collections import Counter
from collections.abc import Mapping
from datetime import date

from .keys import KeyKind
from .raw import collection_date
from .schedule import PollReport

logger = logging.getLogger(__name__)

# CTA's default limits. GTFS-Realtime publishes none.
DAILY_LIMITS: Mapping[KeyKind, int] = {KeyKind.BUS: 100_000, KeyKind.TRAIN: 50_000}
WARNING_SHARE = 0.8


class CallBudget:
    """Count every call attempt, retries included, and warn once a day per key past 80 percent.

    Pass it to each feed loop as a listener.
    """

    def __init__(self, limits: Mapping[KeyKind, int] = DAILY_LIMITS) -> None:
        self._limits = dict(limits)
        self._lock = threading.Lock()
        self._day: date | None = None
        self._calls: Counter[KeyKind] = Counter()
        self._warned: set[KeyKind] = set()

    def __call__(self, report: PollReport) -> None:
        kind = report.feed.key_kind
        calls = sum(len(outcome.attempts) for outcome in report.outcomes)
        with self._lock:
            self._start_day(collection_date(report.poll_time_utc))
            self._calls[kind] += calls
            if self._passed_warning(kind):
                self._warned.add(kind)
                logger.warning(
                    "the %s key has made %d calls today, over %d%% of its daily limit of %d",
                    kind,
                    self._calls[kind],
                    WARNING_SHARE * 100,
                    self._limits[kind],
                )

    def calls_today(self, kind: KeyKind) -> int:
        with self._lock:
            return self._calls[kind]

    def _start_day(self, day: date) -> None:
        if self._day is not None and day <= self._day:
            return
        self._day = day
        self._calls.clear()
        self._warned.clear()

    def _passed_warning(self, kind: KeyKind) -> bool:
        limit = self._limits.get(kind)
        if limit is None or kind in self._warned:
            return False
        return self._calls[kind] > limit * WARNING_SHARE
