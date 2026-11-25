import json
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from lamp_collector.feeds import FeedRequest, bus_vehicles, train_positions
from lamp_collector.fetch import Fetcher
from lamp_collector.journal import JournalWriter
from lamp_collector.keys import ApiKeys
from lamp_collector.poll import CallOutcome, Poll, PollRunner, Recorder
from lamp_collector.raw import RawWriter
from lamp_collector.responses import Status
from lamp_storage import LocalStore, StorageError

KEYS = ApiKeys(bus="Bus0Key0Bus0Key0Bus0Key0B", train="train0key0train0key0train0key012")
POLL_TIME = datetime(2026, 11, 25, 4, 30, 0, tzinfo=UTC)
NOW = POLL_TIME + timedelta(seconds=1)
LATER = NOW + timedelta(seconds=20)

BUS_OK = b'{"bustime-response": {"vehicle": [{"vid": "1", "pid": 5}]}}'
BUS_ERROR = b'{"bustime-response": {"error": [{"msg": "Internal server error"}]}}'
TRAIN_OK = b'{"ctatt": {"errCd": "0", "route": [{"@name": "red", "train": []}]}}'

Handler = Callable[[httpx.Request], httpx.Response]


def runner(store: LocalStore, handler: Handler) -> PollRunner:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    recorder = Recorder(RawWriter(store), JournalWriter(store))
    return PollRunner(Fetcher(client, KEYS, clock=lambda: NOW), recorder, clock=lambda: NOW)


def poll(*requests: FeedRequest, retry_by: datetime = LATER) -> Poll:
    return Poll(POLL_TIME, requests, retry_by)


def answers(*responses: httpx.Response) -> Handler:
    """Answer each request with the next response, the last one from then on."""
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        return queue.pop(0) if len(queue) > 1 else queue[0]

    return handler


def statuses(outcome: CallOutcome) -> list[Status]:
    return [attempt.reading.status for attempt in outcome.attempts]


def journal(store: LocalStore) -> list[dict[str, object]]:
    keys = store.list_keys("coverage/journal/")
    return [json.loads(line) for key in keys for line in store.get(key).splitlines()]


@pytest.fixture
def store(tmp_path: Path) -> LocalStore:
    return LocalStore(tmp_path)


def test_every_call_is_made_saved_and_journaled_in_request_order(store: LocalStore) -> None:
    requests = (bus_vehicles(["1"]), bus_vehicles(["2"]), bus_vehicles(["3"]))

    outcomes = runner(store, answers(httpx.Response(200, content=BUS_OK))).run(poll(*requests))

    assert [outcome.final.result.request for outcome in outcomes] == list(requests)
    assert [outcome.final.call.call_index for outcome in outcomes] == [0, 1, 2]
    assert all(statuses(outcome) == [Status.OK] for outcome in outcomes)
    assert all(store.exists(str(outcome.final.raw_path)) for outcome in outcomes)
    assert sorted(str(row["call_index"]) for row in journal(store)) == ["0", "1", "2"]


def test_a_failed_call_is_tried_once_more(store: LocalStore) -> None:
    handler = answers(httpx.Response(503, content=b"busy"), httpx.Response(200, content=TRAIN_OK))

    [outcome] = runner(store, handler).run(poll(train_positions()))

    assert statuses(outcome) == [Status.HTTP_ERROR, Status.OK]
    assert [attempt.call.attempt for attempt in outcome.attempts] == [1, 2]
    assert [row["attempt"] for row in journal(store)] == [1, 2]
    assert len(list(store.list_keys("raw/"))) == 2


def test_a_call_that_fails_twice_is_not_tried_a_third_time(store: LocalStore) -> None:
    handler = answers(httpx.Response(200, content=BUS_ERROR))

    [outcome] = runner(store, handler).run(poll(bus_vehicles(["1"])))

    assert statuses(outcome) == [Status.API_ERROR, Status.API_ERROR]


def test_a_call_that_got_no_response_is_retried_and_saves_no_raw_file(store: LocalStore) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    [outcome] = runner(store, handler).run(poll(train_positions()))

    assert statuses(outcome) == [Status.CONNECTION_ERROR, Status.CONNECTION_ERROR]
    assert [attempt.raw_path for attempt in outcome.attempts] == [None, None]
    assert len(journal(store)) == 2


def test_no_retry_once_retry_by_has_passed(store: LocalStore) -> None:
    handler = answers(httpx.Response(503, content=b"busy"))

    [outcome] = runner(store, handler).run(poll(train_positions(), retry_by=NOW))

    assert statuses(outcome) == [Status.HTTP_ERROR]


def test_calls_of_a_poll_run_at_the_same_time(store: LocalStore) -> None:
    requests = tuple(bus_vehicles([str(route)]) for route in range(5))
    all_waiting = threading.Barrier(len(requests), timeout=5)

    def handler(request: httpx.Request) -> httpx.Response:
        all_waiting.wait()
        return httpx.Response(200, content=BUS_OK)

    outcomes = runner(store, handler).run(poll(*requests))

    assert all(statuses(outcome) == [Status.OK] for outcome in outcomes)


def test_a_poll_with_no_calls_does_nothing(store: LocalStore) -> None:
    assert runner(store, answers(httpx.Response(200))).run(poll()) == []
    assert list(store.list_keys()) == []


def test_a_storage_failure_is_raised_after_the_other_calls_are_saved(
    store: LocalStore, tmp_path: Path
) -> None:
    (tmp_path / "raw").mkdir()
    (tmp_path / "raw" / "train_positions").write_bytes(b"a file where a folder belongs")

    def handler(request: httpx.Request) -> httpx.Response:
        body = BUS_OK if "getvehicles" in request.url.path else TRAIN_OK
        return httpx.Response(200, content=body)

    with pytest.raises(StorageError, match="parent of it is a file"):
        runner(store, handler).run(poll(bus_vehicles(["1"]), train_positions()))
    assert [row["feed"] for row in journal(store)] == ["bus_vehicles"]
