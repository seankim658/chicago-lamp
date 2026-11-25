import gzip
import json
import re
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from lamp_collector.feeds import Feed, FeedRequest, bus_vehicles, gtfs_realtime, train_positions
from lamp_collector.fetch import Failure, FetchResult
from lamp_collector.raw import CallId, InvalidCallError, RawWriter, collection_date, raw_key
from lamp_storage import LocalStore, ObjectConflictError

# 10:30 pm on 24 November in Chicago.
POLL = datetime(2026, 11, 25, 4, 30, 0, tzinfo=UTC)
CALL = CallId(POLL, call_index=3, attempt=1)
BODY = b'{"bustime-response": {"vehicle": []}}'


def response(request: FeedRequest, body: bytes | None, status: int | None = 200) -> FetchResult:
    return FetchResult(request, POLL, POLL, http_status=status, body=body)


@pytest.fixture
def store(tmp_path: Path) -> LocalStore:
    return LocalStore(tmp_path)


def stored(store: LocalStore, key: str) -> bytes:
    return gzip.decompress(store.get(key))


def test_key_puts_the_chicago_date_in_the_folder_and_the_utc_time_in_the_name() -> None:
    assert raw_key(Feed.BUS_VEHICLES, CALL) == (
        "raw/bus_vehicles/2026-11-24/20261125T043000Z-03-1.json.gz"
    )


def test_gtfs_realtime_keys_end_in_pb() -> None:
    key = raw_key(Feed.GTFSRT_SERVICE_ALERTS, CallId(POLL, 0, 2))

    assert key == "raw/gtfsrt_service_alerts/2026-11-24/20261125T043000Z-00-2.pb.gz"


def test_the_repeated_hour_when_daylight_saving_ends_gets_distinct_names() -> None:
    first = CallId(datetime(2026, 11, 1, 6, 30, tzinfo=UTC), 0, 1)
    second = CallId(datetime(2026, 11, 1, 7, 30, tzinfo=UTC), 0, 1)

    assert collection_date(first.poll_time_utc) == date(2026, 11, 1)
    assert collection_date(second.poll_time_utc) == date(2026, 11, 1)
    assert raw_key(Feed.TRAIN_POSITIONS, first) != raw_key(Feed.TRAIN_POSITIONS, second)


@pytest.mark.parametrize(
    ("poll_time", "call_index", "attempt", "message"),
    [
        (datetime(2026, 11, 25, 4, 30), 0, 1, "must be in UTC"),
        (datetime(2026, 11, 25, 4, 30, tzinfo=timezone(timedelta(hours=-6))), 0, 1, "in UTC"),
        (POLL.replace(microsecond=500), 0, 1, "whole second"),
        (POLL, -1, 1, "call index must be 0 to 99, got -1"),
        (POLL, 100, 1, "call index must be 0 to 99, got 100"),
        (POLL, 0, 3, "attempt must be one of (1, 2), got 3"),
    ],
)
def test_a_call_no_file_name_can_hold_is_refused(
    poll_time: datetime, call_index: int, attempt: int, message: str
) -> None:
    with pytest.raises(InvalidCallError, match=re.escape(message)):
        CallId(poll_time, call_index, attempt)


def test_writing_a_response_stores_its_body_gzipped(store: LocalStore) -> None:
    key = RawWriter(store).write(CALL, response(bus_vehicles(["22"]), BODY))

    assert key == raw_key(Feed.BUS_VEHICLES, CALL)
    assert stored(store, key) == BODY


def test_a_response_with_an_error_status_is_still_stored(store: LocalStore) -> None:
    key = RawWriter(store).write(CALL, response(train_positions(), b"busy", status=503))

    assert key is not None
    assert stored(store, key) == b"busy"


def test_an_xml_error_from_gtfs_realtime_is_stored_under_pb(store: LocalStore) -> None:
    body = b"<error><errCd>102</errCd></error>"
    request = gtfs_realtime(Feed.GTFSRT_TRIP_UPDATES)

    key = RawWriter(store).write(CALL, response(request, body))

    assert key is not None
    assert key.endswith(".pb.gz")
    assert stored(store, key) == body


def test_a_call_with_no_response_stores_nothing(store: LocalStore) -> None:
    failed = FetchResult(
        train_positions(), POLL, failure=Failure.TIMEOUT, error="ReadTimeout: timed out"
    )

    assert RawWriter(store).write(CALL, failed) is None
    assert list(store.list_keys()) == []


def test_writing_the_same_response_twice_is_safe(store: LocalStore) -> None:
    writer = RawWriter(store)
    result = response(bus_vehicles(["22"]), BODY)

    assert writer.write(CALL, result) == writer.write(CALL, result)
    assert len(list(store.list_keys())) == 1


def test_a_different_body_for_a_call_already_stored_is_refused(store: LocalStore) -> None:
    writer = RawWriter(store)
    writer.write(CALL, response(bus_vehicles(["22"]), BODY))

    with pytest.raises(ObjectConflictError):
        writer.write(CALL, response(bus_vehicles(["22"]), b"{}"))
    assert stored(store, raw_key(Feed.BUS_VEHICLES, CALL)) == BODY


def test_every_recorded_response_is_stored_unchanged(cta_fixtures: Path, store: LocalStore) -> None:
    writer = RawWriter(store)
    entries = [json.loads(line) for line in (cta_fixtures / "calls.jsonl").read_text().splitlines()]
    recorded = [entry for entry in entries if entry["file"] is not None]

    for number, entry in enumerate(recorded):
        body = (cta_fixtures / entry["file"]).read_bytes()
        call = CallId(POLL + timedelta(seconds=number), 0, 1)
        request = FeedRequest(Feed(entry["feed"]), entry["url"])

        key = writer.write(call, response(request, body, entry["http_status"]))

        assert key is not None
        assert stored(store, key) == body
