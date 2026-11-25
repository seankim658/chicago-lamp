"""The bus route list, which decides the calls of every bus vehicle poll.

On start the list comes from the newest saved getroutes response, so a restart
costs no call. Only when none is saved is a new one fetched, before the first
bus vehicle poll. After that, each scheduled getroutes poll replaces it.
"""

import logging
import threading
from datetime import timedelta

from lamp_storage import Store

from .feeds import MAX_IDS_PER_CALL, Feed, FeedRequest, bus_routes, bus_vehicles
from .poll import Poll, PollRunner
from .raw import read_saved
from .responses import Status
from .schedule import Pacer, PollReport, next_poll_time, retry_deadline

logger = logging.getLogger(__name__)

ROUTES_PREFIX = f"raw/{Feed.BUS_ROUTES.value}/"
STARTUP_INTERVAL = timedelta(seconds=30)


class BusRoutes:
    """The current route list, shared by the threads that read and replace it."""

    def __init__(self, store: Store) -> None:
        self._store = store
        self._lock = threading.Lock()
        self._routes: tuple[str, ...] = ()

    @property
    def loaded(self) -> bool:
        with self._lock:
            return bool(self._routes)

    def load_newest(self) -> bool:
        """Take the list from the newest saved response that holds one.

        Return False when no saved response does.
        """
        for key in reversed(list(self._store.list_keys(ROUTES_PREFIX))):
            reading = read_saved(self._store, key)
            if reading.status is Status.OK and reading.route_ids:
                self._replace(reading.route_ids, f"saved response {key}")
                return True
        return False

    def listen(self, report: PollReport) -> None:
        """Take the list from a getroutes poll that got one, and keep the old one otherwise."""
        for outcome in report.outcomes:
            reading = outcome.final.reading
            if reading.status is Status.OK and reading.route_ids:
                self._replace(reading.route_ids, f"poll at {report.poll_time_utc.isoformat()}")
                return
        logger.warning(
            "the getroutes poll at %s got no route list, so the old one is kept",
            report.poll_time_utc.isoformat(),
        )

    def requests(self) -> tuple[FeedRequest, ...]:
        """One bus vehicle call per ten routes."""
        with self._lock:
            routes = self._routes
        if not routes:
            logger.warning("no bus route list is loaded, so bus vehicles are not polled")
        batches = range(0, len(routes), MAX_IDS_PER_CALL)
        return tuple(bus_vehicles(routes[start : start + MAX_IDS_PER_CALL]) for start in batches)

    def _replace(self, routes: tuple[str, ...], source: str) -> None:
        with self._lock:
            self._routes = routes
        logger.info("loaded %d bus routes from the %s", len(routes), source)


def fetch_routes_until_loaded(routes: BusRoutes, runner: PollRunner, pacer: Pacer) -> bool:
    """Poll getroutes every 30 s until a route list arrives.

    Return False when the pacer stops first. Call this before the scheduled
    getroutes polls start, so the two can never share a poll time.
    """
    while not routes.loaded:
        poll_time = next_poll_time(pacer.now(), STARTUP_INTERVAL)
        if not pacer.wait_until(poll_time):
            return False
        retry_by = retry_deadline(poll_time, STARTUP_INTERVAL)
        try:
            outcomes = runner.run(Poll(poll_time, (bus_routes(),), retry_by))
        except Exception:
            logger.exception("the getroutes poll at %s failed", poll_time.isoformat())
            continue
        routes.listen(PollReport(Feed.BUS_ROUTES, poll_time, outcomes))
    return True
