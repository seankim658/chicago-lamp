"""Bus pattern definitions, fetched for every pattern a bus is seen on that has none saved.

Bus vehicle polls report which patterns buses are on. A pattern with no saved
definition is asked for on the next pattern poll, ten to a call. A pattern CTA
answers for, with or without a definition, isn't asked for again, and one whose
calls keep failing is given up after a few polls, so a bad pattern can't cost
calls forever. A pattern given up on is asked for again only after a restart.
"""

import logging
import threading

from lamp_storage import Store

from .feeds import MAX_IDS_PER_CALL, Feed, FeedRequest, bus_patterns
from .raw import read_saved
from .responses import Status
from .schedule import PollReport

logger = logging.getLogger(__name__)

PATTERNS_PREFIX = f"raw/{Feed.BUS_PATTERNS.value}/"
MAX_CALLS_PER_POLL = 10
MAX_FAILED_POLLS = 3


class PatternTracker:
    """Which patterns are known, and which are wanted, shared by the bus and pattern threads."""

    def __init__(self, store: Store) -> None:
        self._store = store
        self._lock = threading.Lock()
        self._known: set[str] = set()
        self._failed_polls: dict[str, int] = {}
        self._asked: dict[FeedRequest, tuple[str, ...]] = {}

    def load_known(self) -> int:
        """Mark every pattern defined in a saved response as known, and return how many."""
        defined: set[str] = set()
        for key in self._store.list_keys(PATTERNS_PREFIX):
            reading = read_saved(self._store, key)
            if reading.status is Status.OK:
                defined.update(reading.pattern_ids)
        with self._lock:
            self._known.update(defined)
        return len(defined)

    def listen_vehicles(self, report: PollReport) -> None:
        seen = {
            pattern
            for outcome in report.outcomes
            if outcome.final.reading.status is Status.OK
            for pattern in outcome.final.reading.pattern_ids
        }
        with self._lock:
            for pattern in seen - self._known:
                self._failed_polls.setdefault(pattern, 0)

    def requests(self) -> tuple[FeedRequest, ...]:
        """Calls for the wanted patterns, as many as one poll takes."""
        with self._lock:
            wanted = sorted(self._failed_polls)[: MAX_CALLS_PER_POLL * MAX_IDS_PER_CALL]
            batches = [
                tuple(wanted[start : start + MAX_IDS_PER_CALL])
                for start in range(0, len(wanted), MAX_IDS_PER_CALL)
            ]
            self._asked = {bus_patterns(batch): batch for batch in batches}
            return tuple(self._asked)

    def listen_patterns(self, report: PollReport) -> None:
        with self._lock:
            for outcome in report.outcomes:
                final = outcome.final
                asked = self._asked.pop(final.result.request, ())
                if final.reading.status is Status.OK:
                    self._answered(asked, final.reading.pattern_ids)
                else:
                    self._failed(asked)

    def _answered(self, asked: tuple[str, ...], defined: tuple[str, ...]) -> None:
        for pattern in asked:
            self._failed_polls.pop(pattern, None)
            self._known.add(pattern)
            if pattern not in defined:
                logger.warning("CTA has no definition for bus pattern %s", pattern)
        self._known.update(defined)

    def _failed(self, asked: tuple[str, ...]) -> None:
        for pattern in asked:
            self._failed_polls[pattern] += 1
            if self._failed_polls[pattern] >= MAX_FAILED_POLLS:
                del self._failed_polls[pattern]
                self._known.add(pattern)
                logger.warning(
                    "gave up on bus pattern %s after %d failed polls", pattern, MAX_FAILED_POLLS
                )
