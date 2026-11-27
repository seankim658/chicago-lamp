import json
import logging
import threading
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from google.transit import gtfs_realtime_pb2
from lamp_collector.collector import Collector, Services, polled_feeds, steady_feeds
from lamp_collector.feeds import Feed
from lamp_collector.fetch import Fetcher
from lamp_collector.journal import JournalWriter
from lamp_collector.keys import ApiKeys
from lamp_collector.poll import PollRunner, Recorder
from lamp_collector.raw import RawWriter
from lamp_collector.schedule import Pacer
from lamp_storage import LocalStore

KEYS = ApiKeys(bus="Bus0Key0Bus0Key0Bus0Key0B", train="train0key0train0key0train0key012")
KEYS_WITH_GTFSRT = ApiKeys(KEYS.bus, KEYS.train, gtfsrt="Gtfs0Key0Gtfs0Key0Gtfs0")
START = datetime(2026, 11, 25, 4, 30, 7, tzinfo=UTC)
POLLS_PER_THREAD = 3

BUS_FEEDS = {"bus_routes", "bus_vehicles"}
GTFSRT_FEEDS = {"gtfsrt_vehicle_positions", "gtfsrt_trip_updates", "gtfsrt_service_alerts"}


class SteppingPacer(Pacer):
    """A shared clock that jumps to each moment waited for.

    Each thread gets a few waits, then waits for the real stop, so every feed
    polls a few times however the threads interleave.
    """

    def __init__(self, fail_in: str | None = None) -> None:
        self._lock = threading.Lock()
        self._time = START
        self._waits: defaultdict[str, int] = defaultdict(int)
        self._fail_in = fail_in
        super().__init__(clock=self._now)

    def _now(self) -> datetime:
        with self._lock:
            return self._time

    def wait_until(self, moment: datetime) -> bool:
        name = threading.current_thread().name
        if name == self._fail_in:
            raise RuntimeError("clock broke")
        with self._lock:
            self._waits[name] += 1
            if self._waits[name] <= POLLS_PER_THREAD:
                self._time = max(self._time, moment)
                return True
        self.wait_stopped(30)
        return False


def cta(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    body: object
    if path.endswith("/getroutes"):
        body = {"bustime-response": {"routes": [{"rt": "22"}, {"rt": "X9"}]}}
    elif path.endswith("/getvehicles"):
        body = {"bustime-response": {"vehicle": [{"vid": "1", "pid": 7}]}}
    elif path.endswith("/getpatterns"):
        body = {"bustime-response": {"ptr": [{"pid": 7, "pt": []}]}}
    elif path.endswith("/ttpositions.aspx"):
        body = {"ctatt": {"errCd": "0", "route": [{"@name": "red"}]}}
    else:
        message = gtfs_realtime_pb2.FeedMessage()
        message.header.gtfs_realtime_version = "2.0"
        return httpx.Response(200, content=message.SerializeToString())
    return httpx.Response(200, content=json.dumps(body).encode())


def collector(store: LocalStore, keys: ApiKeys, pacer: Pacer) -> Collector:
    client = httpx.Client(transport=httpx.MockTransport(cta))
    recorder = Recorder(RawWriter(store), JournalWriter(store))
    runner = PollRunner(Fetcher(client, keys, clock=pacer.now), recorder, clock=pacer.now)
    return Collector(Services(store, runner, pacer), keys)


def journaled_feeds(store: LocalStore) -> set[str]:
    feeds = set()
    for key in store.list_keys("coverage/journal/"):
        for line in store.get(key).splitlines():
            row = json.loads(line)
            assert row["status"] == "ok", row
            feeds.add(row["feed"])
    return feeds


def run_until(target: Collector, done: threading.Event) -> list[bool]:
    """Run the collector in a thread until done is set or it stops itself, and return its result."""
    result: list[bool] = []
    thread = threading.Thread(target=lambda: result.append(target.run()))
    thread.start()
    deadline = time.monotonic() + 10
    while thread.is_alive() and not done.is_set() and time.monotonic() < deadline:
        done.wait(0.02)
    target.stop()
    thread.join(10)
    assert not thread.is_alive()
    return result


def wait_for_feeds(store: LocalStore, feeds: set[str]) -> threading.Event:
    done = threading.Event()

    def watch() -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not feeds <= journaled_feeds(store):
            time.sleep(0.02)
        done.set()

    threading.Thread(target=watch, daemon=True).start()
    return done


@pytest.fixture
def store(tmp_path: Path) -> LocalStore:
    return LocalStore(tmp_path)


def test_every_feed_polls_and_journals_until_stopped(store: LocalStore) -> None:
    feeds = {"train_positions"} | BUS_FEEDS | GTFSRT_FEEDS
    target = collector(store, KEYS_WITH_GTFSRT, SteppingPacer())

    assert run_until(target, wait_for_feeds(store, feeds)) == [True]
    assert feeds <= journaled_feeds(store)


def test_without_a_gtfs_realtime_key_those_feeds_are_skipped(
    store: LocalStore, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    feeds = {"train_positions"} | BUS_FEEDS
    target = collector(store, KEYS, SteppingPacer())

    assert run_until(target, wait_for_feeds(store, feeds)) == [True]
    assert not journaled_feeds(store) & GTFSRT_FEEDS
    assert "GTFS-Realtime feeds are skipped, since CTA_GTFSRT_KEY is not set" in caplog.text


def test_a_saved_route_list_is_used_without_calling_getroutes_first(
    store: LocalStore, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    first = collector(store, KEYS, SteppingPacer())
    run_until(first, wait_for_feeds(store, {"bus_routes", "bus_vehicles"}))
    caplog.clear()

    second = collector(store, KEYS, SteppingPacer())
    run_until(second, wait_for_feeds(store, set()))

    assert "loaded 2 bus routes from the saved response" in caplog.text
    assert "getroutes is polled until one arrives" not in caplog.text


def test_a_feed_thread_that_dies_stops_the_collector_with_an_error(
    store: LocalStore, caplog: pytest.LogCaptureFixture
) -> None:
    target = collector(store, KEYS, SteppingPacer(fail_in="train_positions"))
    never = threading.Event()

    assert run_until(target, never) == [False]
    assert "the train_positions thread died, so the collector is stopping" in caplog.text


def test_gtfs_realtime_feeds_are_polled_only_with_their_key() -> None:
    assert {feed.value for feed in polled_feeds(KEYS)} == {
        "bus_vehicles",
        "bus_routes",
        "bus_patterns",
        "train_positions",
    }
    assert {feed.value for feed in polled_feeds(KEYS_WITH_GTFSRT)} >= GTFSRT_FEEDS


def test_route_lists_and_pattern_definitions_are_not_steady_feeds() -> None:
    assert steady_feeds(KEYS) == {Feed.BUS_VEHICLES, Feed.TRAIN_POSITIONS}
    assert steady_feeds(KEYS_WITH_GTFSRT) == {
        Feed.BUS_VEHICLES,
        Feed.TRAIN_POSITIONS,
        Feed.GTFSRT_VEHICLE_POSITIONS,
        Feed.GTFSRT_TRIP_UPDATES,
        Feed.GTFSRT_SERVICE_ALERTS,
    }
