from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from lamp_collector.feeds import Feed, FeedRequest, bus_vehicles, train_positions
from lamp_collector.fetch import FetchResult
from lamp_collector.health import HealthCheck
from lamp_collector.journal import CallRecord, JournalWriter, journal_key
from lamp_collector.raw import CallId
from lamp_collector.responses import Reading, Status
from lamp_storage import LocalStore

NOW = datetime(2026, 11, 27, 6, 3, 15, tzinfo=UTC)
FEEDS = frozenset({Feed.BUS_VEHICLES, Feed.TRAIN_POSITIONS})
BUS = bus_vehicles(["22"])
TRAIN = train_positions()


@pytest.fixture
def store(tmp_path: Path) -> LocalStore:
    return LocalStore(tmp_path)


def check(store: LocalStore, tmp_path: Path, min_free_bytes: int = 0) -> list[str]:
    return HealthCheck(store, tmp_path, FEEDS, min_free_bytes=min_free_bytes).problems(NOW)


def record(
    store: LocalStore,
    request: FeedRequest,
    ago: timedelta,
    status: Status = Status.OK,
    call_index: int = 0,
    attempt: int = 1,
) -> None:
    poll = NOW - ago
    poll = poll.replace(microsecond=0)
    result = FetchResult(request, poll, poll, http_status=200, body=b"{}")
    call = CallId(poll, call_index=call_index, attempt=attempt)
    JournalWriter(store).record(CallRecord(call, result, Reading(status), raw_path=None))


def both_feeds_ok(store: LocalStore, ago: timedelta) -> None:
    record(store, BUS, ago)
    record(store, TRAIN, ago)


def test_feeds_with_a_recent_successful_poll_are_healthy(store: LocalStore, tmp_path: Path) -> None:
    both_feeds_ok(store, timedelta(seconds=45))

    assert check(store, tmp_path) == []


def test_a_feed_with_no_rows_is_reported(store: LocalStore, tmp_path: Path) -> None:
    record(store, BUS, timedelta(seconds=45))

    assert check(store, tmp_path) == ["train_positions: no successful poll in the last 10 minutes"]


def test_failed_polls_after_an_old_success_are_reported_with_its_time(
    store: LocalStore, tmp_path: Path
) -> None:
    record(store, BUS, timedelta(seconds=45))
    record(store, TRAIN, timedelta(minutes=10, seconds=15))
    record(store, TRAIN, timedelta(minutes=5), Status.TIMEOUT)

    assert check(store, tmp_path) == ["train_positions: no successful poll since 05:53:00 UTC"]


def test_a_poll_with_one_failed_call_is_not_a_success(store: LocalStore, tmp_path: Path) -> None:
    record(store, TRAIN, timedelta(seconds=45))
    record(store, BUS, timedelta(seconds=45), call_index=0)
    record(store, BUS, timedelta(seconds=45), Status.HTTP_ERROR, call_index=1)
    record(store, BUS, timedelta(seconds=45), Status.HTTP_ERROR, call_index=1, attempt=2)

    assert check(store, tmp_path) == ["bus_vehicles: no successful poll in the last 10 minutes"]


def test_a_call_that_succeeds_on_its_retry_counts(store: LocalStore, tmp_path: Path) -> None:
    record(store, TRAIN, timedelta(seconds=45))
    record(store, BUS, timedelta(seconds=45), Status.CONNECTION_ERROR)
    record(store, BUS, timedelta(seconds=45), attempt=2)

    assert check(store, tmp_path) == []


def test_a_success_in_the_previous_hours_file_counts(store: LocalStore, tmp_path: Path) -> None:
    both_feeds_ok(store, timedelta(minutes=6))

    assert journal_key(NOW - timedelta(minutes=6)) != journal_key(NOW)
    assert check(store, tmp_path) == []


def test_a_row_still_being_written_is_ignored(store: LocalStore, tmp_path: Path) -> None:
    both_feeds_ok(store, timedelta(seconds=45))
    path = tmp_path / journal_key(NOW)
    with path.open("ab") as file:
        file.write(b'{"feed":"train_po')

    assert check(store, tmp_path) == []


def test_feeds_outside_the_expected_set_are_not_checked(store: LocalStore, tmp_path: Path) -> None:
    both_feeds_ok(store, timedelta(seconds=45))

    problems = HealthCheck(store, tmp_path, {Feed.BUS_VEHICLES}, min_free_bytes=0).problems(NOW)

    assert problems == []


def test_low_free_disk_space_is_reported(store: LocalStore, tmp_path: Path) -> None:
    both_feeds_ok(store, timedelta(seconds=45))

    problems = check(store, tmp_path, min_free_bytes=10**18)

    assert len(problems) == 1
    assert problems[0].startswith("disk: ")
    assert " GB free under " in problems[0]


def test_a_missing_buffer_folder_is_reported(tmp_path: Path) -> None:
    missing = tmp_path / "buffer"

    problems = HealthCheck(LocalStore(missing), missing, FEEDS, min_free_bytes=0).problems(NOW)

    assert problems == [f"buffer: {missing} does not exist"]
