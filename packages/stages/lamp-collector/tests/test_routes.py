import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from lamp_collector.feeds import Feed, FeedRequest, bus_routes, bus_vehicles
from lamp_collector.fetch import Fetcher, FetchResult
from lamp_collector.journal import CallRecord, JournalWriter
from lamp_collector.keys import ApiKeys
from lamp_collector.poll import CallOutcome, PollRunner, Recorder
from lamp_collector.raw import CallId, RawWriter
from lamp_collector.responses import Reading, Status
from lamp_collector.routes import BusRoutes, fetch_routes_until_loaded
from lamp_collector.schedule import Pacer, PollReport
from lamp_storage import LocalStore

KEYS = ApiKeys(bus="Bus0Key0Bus0Key0Bus0Key0B", train="train0key0train0key0train0key012")
POLL_TIME = datetime(2026, 11, 25, 4, 30, tzinfo=UTC)
START = datetime(2026, 11, 25, 4, 30, 7, tzinfo=UTC)
ROUTE_IDS = tuple(str(route) for route in range(1, 24))


def routes_body(route_ids: tuple[str, ...] = ROUTE_IDS) -> bytes:
    routes = [{"rt": route, "rtnm": f"Route {route}"} for route in route_ids]
    return json.dumps({"bustime-response": {"routes": routes}}).encode()


ERROR_BODY = b'{"bustime-response": {"error": [{"msg": "Internal server error"}]}}'


class FakePacer(Pacer):
    """A clock that jumps straight to each moment waited for, and stops after some waits."""

    def __init__(self, start: datetime, waits: int) -> None:
        self.time = start
        self.waited: list[datetime] = []
        self._waits_left = waits
        super().__init__(clock=lambda: self.time)

    def wait_until(self, moment: datetime) -> bool:
        if self._waits_left == 0:
            return False
        self._waits_left -= 1
        self.waited.append(moment)
        self.time = max(self.time, moment) + timedelta(seconds=1)
        return True


@pytest.fixture
def store(tmp_path: Path) -> LocalStore:
    return LocalStore(tmp_path)


def save(store: LocalStore, body: bytes, poll_time: datetime) -> None:
    result = FetchResult(bus_routes(), poll_time, poll_time, http_status=200, body=body)
    RawWriter(store).write(CallId(poll_time, 0, 1), result)


def routes_report(reading: Reading) -> PollReport:
    result = FetchResult(bus_routes(), POLL_TIME, POLL_TIME, http_status=200, body=b"{}")
    record = CallRecord(CallId(POLL_TIME, 0, 1), result, reading, None)
    return PollReport(Feed.BUS_ROUTES, POLL_TIME, [CallOutcome((record,))])


def route_lists(requests: tuple[FeedRequest, ...]) -> list[str]:
    return [dict(request.params)["rt"] for request in requests]


def test_the_newest_saved_list_is_loaded_and_split_ten_routes_to_a_call(
    store: LocalStore,
) -> None:
    save(store, routes_body(("old",)), POLL_TIME - timedelta(hours=6))
    save(store, routes_body(), POLL_TIME)
    routes = BusRoutes(store)

    assert routes.load_newest() is True
    assert routes.requests() == (
        bus_vehicles(ROUTE_IDS[0:10]),
        bus_vehicles(ROUTE_IDS[10:20]),
        bus_vehicles(ROUTE_IDS[20:23]),
    )


def test_a_newer_saved_error_is_passed_over_for_the_last_good_list(store: LocalStore) -> None:
    save(store, routes_body(("9", "X9")), POLL_TIME)
    save(store, ERROR_BODY, POLL_TIME + timedelta(hours=6))
    routes = BusRoutes(store)

    assert routes.load_newest() is True
    assert route_lists(routes.requests()) == ["9,X9"]


def test_with_nothing_saved_no_list_loads_and_bus_polls_warn(
    store: LocalStore, caplog: pytest.LogCaptureFixture
) -> None:
    routes = BusRoutes(store)

    assert routes.load_newest() is False
    assert routes.loaded is False
    assert routes.requests() == ()
    assert "no bus route list is loaded" in caplog.text


def test_a_getroutes_poll_replaces_the_list(store: LocalStore) -> None:
    routes = BusRoutes(store)

    routes.listen(routes_report(Reading(Status.OK, route_ids=("4", "49"))))

    assert route_lists(routes.requests()) == ["4,49"]


def test_a_failed_getroutes_poll_keeps_the_old_list(
    store: LocalStore, caplog: pytest.LogCaptureFixture
) -> None:
    routes = BusRoutes(store)
    routes.listen(routes_report(Reading(Status.OK, route_ids=("4",))))

    routes.listen(routes_report(Reading(Status.TIMEOUT, error="ReadTimeout: timed out")))

    assert route_lists(routes.requests()) == ["4"]
    assert "got no route list, so the old one is kept" in caplog.text


def test_startup_polls_getroutes_every_30_seconds_until_a_list_arrives(
    store: LocalStore,
) -> None:
    answers = [httpx.Response(503, content=b"busy")] * 2 + [
        httpx.Response(200, content=routes_body())
    ]
    client = httpx.Client(transport=httpx.MockTransport(lambda request: answers.pop(0)))
    recorder = Recorder(RawWriter(store), JournalWriter(store))
    pacer = FakePacer(START, waits=5)
    runner = PollRunner(Fetcher(client, KEYS, clock=pacer.now), recorder, clock=pacer.now)
    routes = BusRoutes(store)

    assert fetch_routes_until_loaded(routes, runner, pacer) is True

    assert pacer.waited == [
        datetime(2026, 11, 25, 4, 30, 30, tzinfo=UTC),
        datetime(2026, 11, 25, 4, 31, 0, tzinfo=UTC),
    ]
    assert len(route_lists(routes.requests())) == 3
    assert len(list(store.list_keys("raw/bus_routes/"))) == 3


def test_startup_gives_up_when_the_collector_stops(store: LocalStore) -> None:
    runner = PollRunner(
        Fetcher(httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503))), KEYS),
        Recorder(RawWriter(store), JournalWriter(store)),
    )

    assert fetch_routes_until_loaded(BusRoutes(store), runner, FakePacer(START, 0)) is False


def test_the_recorded_route_list_loads(cta_fixtures: Path, store: LocalStore) -> None:
    entries = [json.loads(line) for line in (cta_fixtures / "calls.jsonl").read_text().splitlines()]
    [entry] = [entry for entry in entries if entry["feed"] == "bus_routes"]
    save(store, (cta_fixtures / entry["file"]).read_bytes(), POLL_TIME)
    routes = BusRoutes(store)

    assert routes.load_newest() is True
    assert len(routes.requests()) > 10
