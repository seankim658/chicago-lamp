import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from lamp_collector.feeds import Feed, FeedRequest, bus_patterns, bus_vehicles
from lamp_collector.fetch import FetchResult
from lamp_collector.journal import CallRecord
from lamp_collector.patterns import PatternTracker
from lamp_collector.poll import CallOutcome
from lamp_collector.raw import CallId, RawWriter
from lamp_collector.responses import Reading, Status, read_response
from lamp_collector.schedule import PollReport
from lamp_storage import LocalStore

POLL_TIME = datetime(2026, 11, 25, 4, 30, tzinfo=UTC)


@pytest.fixture
def store(tmp_path: Path) -> LocalStore:
    return LocalStore(tmp_path)


def outcome(request: FeedRequest, reading: Reading, index: int = 0) -> CallOutcome:
    result = FetchResult(request, POLL_TIME, POLL_TIME, http_status=200, body=b"{}")
    return CallOutcome((CallRecord(CallId(POLL_TIME, index, 1), result, reading, None),))


def vehicles_seen(*patterns: str, status: Status = Status.OK) -> PollReport:
    reading = Reading(status, vehicle_count=len(patterns), pattern_ids=patterns)
    return PollReport(Feed.BUS_VEHICLES, POLL_TIME, [outcome(bus_vehicles(["22"]), reading)])


def patterns_answered(tracker: PatternTracker, reading_for: dict[int, Reading]) -> None:
    """Answer the tracker's current calls, call index to reading."""
    requests = tracker.requests()
    outcomes = [outcome(requests[index], reading) for index, reading in reading_for.items()]
    tracker.listen_patterns(PollReport(Feed.BUS_PATTERNS, POLL_TIME, outcomes))


def asked(tracker: PatternTracker) -> list[str]:
    return [dict(request.params)["pid"] for request in tracker.requests()]


def test_a_pattern_seen_on_a_bus_is_asked_for(store: LocalStore) -> None:
    tracker = PatternTracker(store)

    tracker.listen_vehicles(vehicles_seen("1", "2"))

    assert tracker.requests() == (bus_patterns(["1", "2"]),)


def test_a_pattern_defined_in_a_saved_response_is_not_asked_for(store: LocalStore) -> None:
    body = b'{"bustime-response": {"ptr": [{"pid": 1, "pt": []}]}}'
    result = FetchResult(bus_patterns(["1"]), POLL_TIME, POLL_TIME, http_status=200, body=body)
    RawWriter(store).write(CallId(POLL_TIME, 0, 1), result)
    tracker = PatternTracker(store)

    assert tracker.load_known() == 1
    tracker.listen_vehicles(vehicles_seen("1", "2"))

    assert asked(tracker) == ["2"]


def test_vehicles_from_a_failed_poll_are_ignored(store: LocalStore) -> None:
    tracker = PatternTracker(store)

    tracker.listen_vehicles(vehicles_seen("1", status=Status.PARSE_ERROR))

    assert tracker.requests() == ()


def test_wanted_patterns_go_ten_to_a_call_and_ten_calls_to_a_poll(store: LocalStore) -> None:
    tracker = PatternTracker(store)
    tracker.listen_vehicles(vehicles_seen(*(f"{n:03d}" for n in range(105))))

    requests = tracker.requests()

    assert len(requests) == 10
    assert all(len(dict(request.params)["pid"].split(",")) == 10 for request in requests)


def test_an_answered_pattern_is_not_asked_for_again(store: LocalStore) -> None:
    tracker = PatternTracker(store)
    tracker.listen_vehicles(vehicles_seen("1"))

    patterns_answered(tracker, {0: Reading(Status.OK, pattern_ids=("1",))})
    tracker.listen_vehicles(vehicles_seen("1"))

    assert tracker.requests() == ()


def test_a_pattern_cta_has_no_definition_for_is_not_asked_for_again(
    store: LocalStore, caplog: pytest.LogCaptureFixture
) -> None:
    tracker = PatternTracker(store)
    tracker.listen_vehicles(vehicles_seen("1", "2"))

    patterns_answered(tracker, {0: Reading(Status.OK, pattern_ids=("1",))})

    assert tracker.requests() == ()
    assert "CTA has no definition for bus pattern 2" in caplog.text


def test_a_failing_pattern_is_asked_for_three_times_then_given_up(
    store: LocalStore, caplog: pytest.LogCaptureFixture
) -> None:
    tracker = PatternTracker(store)
    tracker.listen_vehicles(vehicles_seen("1"))
    failed = Reading(Status.API_ERROR, error="Internal server error")

    for _ in range(3):
        assert asked(tracker) == ["1"]
        patterns_answered(tracker, {0: failed})
    tracker.listen_vehicles(vehicles_seen("1"))

    assert tracker.requests() == ()
    assert "gave up on bus pattern 1 after 3 failed polls" in caplog.text


def test_every_pattern_on_recorded_buses_is_defined_by_the_recorded_patterns(
    cta_fixtures: Path, store: LocalStore
) -> None:
    entries = [json.loads(line) for line in (cta_fixtures / "calls.jsonl").read_text().splitlines()]
    results = {
        feed: [
            FetchResult(
                FeedRequest(feed, entry["url"]),
                POLL_TIME,
                POLL_TIME,
                http_status=entry["http_status"],
                body=(cta_fixtures / entry["file"]).read_bytes(),
            )
            for entry in entries
            if entry["feed"] == feed.value and entry["file"] is not None
        ]
        for feed in (Feed.BUS_PATTERNS, Feed.BUS_VEHICLES)
    }
    writer = RawWriter(store)
    for index, result in enumerate(results[Feed.BUS_PATTERNS]):
        writer.write(CallId(POLL_TIME, index, 1), result)
    tracker = PatternTracker(store)
    tracker.load_known()

    for result in results[Feed.BUS_VEHICLES]:
        report = PollReport(Feed.BUS_VEHICLES, POLL_TIME, [outcome_of(result)])
        tracker.listen_vehicles(report)

    assert tracker.requests() == ()


def outcome_of(result: FetchResult) -> CallOutcome:
    record = CallRecord(CallId(POLL_TIME, 0, 1), result, read_response(result), None)
    return CallOutcome((record,))
