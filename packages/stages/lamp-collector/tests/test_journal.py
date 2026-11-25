import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from lamp_collector.feeds import Feed, FeedRequest, bus_vehicles, train_positions
from lamp_collector.fetch import Failure, FetchResult
from lamp_collector.journal import CallRecord, JournalWriter, journal_key, journal_row
from lamp_collector.raw import CallId, RawWriter
from lamp_collector.responses import Reading, Status, read_response
from lamp_storage import LocalStore

# 10:30 pm on 24 November in Chicago.
POLL = datetime(2026, 11, 25, 4, 30, 0, tzinfo=UTC)
SENT = POLL + timedelta(microseconds=1500)
ANSWERED = POLL + timedelta(seconds=1)
CALL = CallId(POLL, call_index=2, attempt=1)
RAW_PATH = "raw/bus_vehicles/2026-11-24/20261125T043000Z-02-1.json.gz"


@pytest.fixture
def store(tmp_path: Path) -> LocalStore:
    return LocalStore(tmp_path)


def answered(request: FeedRequest, status: int = 200) -> FetchResult:
    return FetchResult(request, SENT, ANSWERED, http_status=status, body=b"{}")


def rows(store: LocalStore, key: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in store.get(key).splitlines()]


@pytest.mark.parametrize(
    ("poll_time", "key"),
    [
        (POLL, "coverage/journal/2026-11-24/20261125T04Z.jsonl"),
        (
            datetime(2026, 11, 25, 5, 59, 59, tzinfo=UTC),
            "coverage/journal/2026-11-24/20261125T05Z.jsonl",
        ),
        (
            datetime(2026, 11, 25, 6, 0, 0, tzinfo=UTC),
            "coverage/journal/2026-11-25/20261125T06Z.jsonl",
        ),
        (
            datetime(2026, 11, 1, 6, 30, tzinfo=UTC),
            "coverage/journal/2026-11-01/20261101T06Z.jsonl",
        ),
        (
            datetime(2026, 11, 1, 7, 30, tzinfo=UTC),
            "coverage/journal/2026-11-01/20261101T07Z.jsonl",
        ),
    ],
)
def test_the_file_is_the_polls_utc_hour_in_its_chicago_date(poll_time: datetime, key: str) -> None:
    assert journal_key(poll_time) == key


def test_a_row_for_an_answered_call_holds_every_column_in_order() -> None:
    reading = Reading(Status.OK, vehicle_count=42, pattern_ids=("1",))
    record = CallRecord(CALL, answered(bus_vehicles(["22"])), reading, RAW_PATH)

    assert journal_row(record) == {
        "feed": "bus_vehicles",
        "poll_time_utc": "2026-11-25T04:30:00.000000+00:00",
        "call_index": 2,
        "attempt": 1,
        "request_url": bus_vehicles(["22"]).public_url,
        "request_time_utc": "2026-11-25T04:30:00.001500+00:00",
        "response_time_utc": "2026-11-25T04:30:01.000000+00:00",
        "status": "ok",
        "error": None,
        "http_status": 200,
        "vehicle_count": 42,
        "raw_path": RAW_PATH,
    }


def test_a_row_for_a_call_with_no_response_has_no_response_fields() -> None:
    error = "ReadTimeout: timed out"
    result = FetchResult(train_positions(), SENT, failure=Failure.TIMEOUT, error=error)
    record = CallRecord(CALL, result, read_response(result), raw_path=None)

    row = journal_row(record)

    assert row["status"] == "timeout"
    assert row["error"] == error
    assert row["response_time_utc"] is None
    assert row["http_status"] is None
    assert row["vehicle_count"] is None
    assert row["raw_path"] is None


def test_a_row_for_a_cta_error_keeps_its_message_and_raw_path() -> None:
    reading = Reading(Status.API_ERROR, error="errCd 102: Invalid API key")
    record = CallRecord(CALL, answered(train_positions()), reading, RAW_PATH)

    row = journal_row(record)

    assert row["status"] == "api_error"
    assert row["error"] == "errCd 102: Invalid API key"
    assert row["raw_path"] == RAW_PATH


def test_rows_of_one_hour_go_to_one_file_in_order(store: LocalStore) -> None:
    writer = JournalWriter(store)
    retry = CallId(POLL, call_index=2, attempt=2)
    for call in (CALL, retry):
        writer.record(CallRecord(call, answered(train_positions()), Reading(Status.OK), None))

    written = rows(store, journal_key(POLL))
    assert [row["attempt"] for row in written] == [1, 2]


def test_repair_drops_a_torn_row_from_the_newest_file_only(
    store: LocalStore, tmp_path: Path
) -> None:
    writer = JournalWriter(store)
    older, newest = journal_key(POLL), journal_key(POLL + timedelta(hours=1))
    for key in (older, newest):
        store.append_line(key, b'{"whole": 1}')
        with (tmp_path / key).open("ab") as file:
            file.write(b'{"torn')

    assert writer.repair_newest() == newest
    assert store.get(newest) == b'{"whole": 1}\n'
    assert store.get(older).endswith(b'{"torn')
    assert writer.repair_newest() is None


def test_repair_with_no_journal_does_nothing(store: LocalStore) -> None:
    assert JournalWriter(store).repair_newest() is None


def test_every_recorded_call_gets_a_row_pointing_at_its_raw_file(
    cta_fixtures: Path, store: LocalStore
) -> None:
    raw, journal = RawWriter(store), JournalWriter(store)
    entries = [json.loads(line) for line in (cta_fixtures / "calls.jsonl").read_text().splitlines()]
    recorded = [entry for entry in entries if entry["file"] is not None]

    for number, entry in enumerate(recorded):
        body = (cta_fixtures / entry["file"]).read_bytes()
        call = CallId(POLL + timedelta(seconds=number), 0, 1)
        request = FeedRequest(Feed(entry["feed"]), entry["url"])
        result = FetchResult(request, SENT, ANSWERED, http_status=entry["http_status"], body=body)
        journal.record(CallRecord(call, result, read_response(result), raw.write(call, result)))

    written = [row for key in store.list_keys("coverage/") for row in rows(store, key)]
    assert len(written) == len(recorded)
    assert all(store.exists(str(row["raw_path"])) for row in written)
    assert {row["status"] for row in written} <= {"ok", "api_error"}
