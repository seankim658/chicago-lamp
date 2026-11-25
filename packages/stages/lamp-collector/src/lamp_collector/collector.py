"""Every feed polling at once, each in its own thread, until stopped.

Before polling starts, a journal row torn by a crash is dropped and the saved
route list and pattern definitions are loaded. Bus vehicle polls wait for a
route list, and the other feeds don't wait for them. If a feed's thread dies,
the whole collector stops with an error, so the service manager restarts it
instead of leaving that feed silent.
"""

import logging
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta

from lamp_storage import LocalStore

from .budget import CallBudget
from .feeds import Feed, FeedRequest, bus_routes, gtfs_realtime, train_positions
from .journal import JournalWriter
from .keys import GTFSRT_KEY_VARIABLE, ApiKeys, KeyKind
from .patterns import PatternTracker
from .poll import PollRunner
from .routes import BusRoutes, fetch_routes_until_loaded
from .schedule import FeedLoop, FeedSchedule, Pacer, PollListener
from .summary import PollSummary

logger = logging.getLogger(__name__)

Requests = Callable[[], tuple[FeedRequest, ...]]

INTERVALS: Mapping[Feed, timedelta] = {
    Feed.BUS_VEHICLES: timedelta(seconds=30),
    Feed.TRAIN_POSITIONS: timedelta(seconds=30),
    Feed.GTFSRT_VEHICLE_POSITIONS: timedelta(seconds=60),
    Feed.GTFSRT_TRIP_UPDATES: timedelta(seconds=60),
    Feed.GTFSRT_SERVICE_ALERTS: timedelta(minutes=5),
    Feed.BUS_ROUTES: timedelta(hours=6),
    Feed.BUS_PATTERNS: timedelta(seconds=30),
}
GTFS_REALTIME_FEEDS = tuple(feed for feed in Feed if feed.key_kind is KeyKind.GTFSRT)
# Feeds polled only now and then, which a 10 minute window often misses.
_OCCASIONAL_FEEDS = frozenset({Feed.BUS_ROUTES, Feed.BUS_PATTERNS})
# How often the main thread looks up from waiting, so it can handle signals.
_WAKE_SECONDS = 1.0


@dataclass(frozen=True)
class Services:
    """What every feed shares: the buffer, the way polls are run, and the clock."""

    store: LocalStore
    runner: PollRunner
    pacer: Pacer


class Collector:
    def __init__(self, services: Services, keys: ApiKeys) -> None:
        self._services = services
        self._feeds = tuple(feed for feed in INTERVALS if keys.has(feed.key_kind))
        self._routes = BusRoutes(services.store)
        self._patterns = PatternTracker(services.store)
        self._listeners: tuple[PollListener, ...] = (
            CallBudget(),
            PollSummary(set(self._feeds) - _OCCASIONAL_FEEDS),
        )
        self._threads: list[threading.Thread] = []
        self._threads_lock = threading.Lock()
        self._failed = threading.Event()

    def run(self) -> bool:
        """Poll until stop is called. Return False when a feed's thread died."""
        self._prepare()
        for feed in self._feeds:
            if feed.key_kind is not KeyKind.BUS:
                self._start(feed.value, self._loop(feed).run)
        self._start("bus", self._run_bus)
        while not self._services.pacer.wait_stopped(_WAKE_SECONDS):
            pass
        self._join_all()
        return not self._failed.is_set()

    def stop(self) -> None:
        self._services.pacer.stop()

    def _prepare(self) -> None:
        repaired = JournalWriter(self._services.store).repair_newest()
        if repaired is not None:
            logger.warning("dropped a row left half-written by a crash from %s", repaired)
        self._routes.load_newest()
        logger.info("%d bus pattern definitions are already saved", self._patterns.load_known())
        for feed in self._feeds:
            logger.info("polling %s every %s", feed, INTERVALS[feed])
        if not set(GTFS_REALTIME_FEEDS) & set(self._feeds):
            logger.info("GTFS-Realtime feeds are skipped, since %s is not set", GTFSRT_KEY_VARIABLE)

    def _run_bus(self) -> None:
        if not self._routes.loaded:
            logger.info("no saved bus route list, so getroutes is polled until one arrives")
            if not fetch_routes_until_loaded(
                self._routes, self._services.runner, self._services.pacer
            ):
                return
        for feed in (Feed.BUS_ROUTES, Feed.BUS_VEHICLES, Feed.BUS_PATTERNS):
            self._start(feed.value, self._loop(feed).run)

    def _loop(self, feed: Feed) -> FeedLoop:
        requests, listener = self._feed_parts(feed)
        schedule = FeedSchedule(feed, INTERVALS[feed], requests)
        listeners = self._listeners if listener is None else (*self._listeners, listener)
        return FeedLoop(schedule, self._services.runner, self._services.pacer, listeners)

    def _feed_parts(self, feed: Feed) -> tuple[Requests, PollListener | None]:
        if feed is Feed.BUS_VEHICLES:
            return self._routes.requests, self._patterns.listen_vehicles
        if feed is Feed.BUS_ROUTES:
            return lambda: (bus_routes(),), self._routes.listen
        if feed is Feed.BUS_PATTERNS:
            return self._patterns.requests, self._patterns.listen_patterns
        if feed is Feed.TRAIN_POSITIONS:
            return lambda: (train_positions(),), None
        return lambda: (gtfs_realtime(feed),), None

    def _start(self, name: str, target: Callable[[], None]) -> None:
        thread = threading.Thread(target=self._guarded(name, target), name=name)
        with self._threads_lock:
            self._threads.append(thread)
        thread.start()

    def _guarded(self, name: str, target: Callable[[], None]) -> Callable[[], None]:
        def run() -> None:
            try:
                target()
            except BaseException:
                logger.exception("the %s thread died, so the collector is stopping", name)
                self._failed.set()
                self.stop()

        return run

    def _join_all(self) -> None:
        """Wait for every thread, including any a thread started while stopping."""
        while alive := self._alive_threads():
            for thread in alive:
                thread.join()

    def _alive_threads(self) -> Sequence[threading.Thread]:
        with self._threads_lock:
            return [thread for thread in self._threads if thread.is_alive()]
