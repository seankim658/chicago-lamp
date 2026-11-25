from datetime import UTC, datetime, timedelta

import pytest
from lamp_collector.budget import CallBudget
from lamp_collector.feeds import Feed, FeedRequest, bus_vehicles, gtfs_realtime, train_positions
from lamp_collector.fetch import FetchResult
from lamp_collector.journal import CallRecord
from lamp_collector.keys import KeyKind
from lamp_collector.poll import CallOutcome
from lamp_collector.raw import CallId
from lamp_collector.responses import Reading, Status
from lamp_collector.schedule import PollReport

# 10:30 pm on 24 November in Chicago.
POLL_TIME = datetime(2026, 11, 25, 4, 30, tzinfo=UTC)
# Midnight starting 25 November in Chicago.
NEXT_DAY = datetime(2026, 11, 25, 6, 0, tzinfo=UTC)


def report(
    request: FeedRequest, attempts: list[int], poll_time: datetime = POLL_TIME
) -> PollReport:
    """A poll with one call per entry, each making that many attempts."""
    outcomes = [
        CallOutcome(tuple(record(request, poll_time, index, n + 1) for n in range(count)))
        for index, count in enumerate(attempts)
    ]
    return PollReport(request.feed, poll_time, outcomes)


def record(request: FeedRequest, poll_time: datetime, index: int, attempt: int) -> CallRecord:
    result = FetchResult(request, poll_time, poll_time, http_status=200, body=b"{}")
    return CallRecord(CallId(poll_time, index, attempt), result, Reading(Status.OK), None)


def test_every_attempt_counts_against_its_own_key() -> None:
    budget = CallBudget()

    budget(report(bus_vehicles(["22"]), [1, 2, 1]))
    budget(report(train_positions(), [2]))

    assert budget.calls_today(KeyKind.BUS) == 4
    assert budget.calls_today(KeyKind.TRAIN) == 2


def test_a_key_past_80_percent_warns_once(caplog: pytest.LogCaptureFixture) -> None:
    budget = CallBudget({KeyKind.BUS: 10})

    budget(report(bus_vehicles(["22"]), [1] * 8))
    assert caplog.text == ""

    budget(report(bus_vehicles(["22"]), [1]))
    budget(report(bus_vehicles(["22"]), [1]))

    assert caplog.text.count("the bus key has made 9 calls today, over 80% of") == 1


def test_a_new_chicago_day_starts_the_count_and_the_warning_again(
    caplog: pytest.LogCaptureFixture,
) -> None:
    budget = CallBudget({KeyKind.BUS: 10})
    budget(report(bus_vehicles(["22"]), [1] * 9))

    budget(report(bus_vehicles(["22"]), [1] * 9, poll_time=NEXT_DAY))

    assert budget.calls_today(KeyKind.BUS) == 9
    assert caplog.text.count("over 80% of") == 2


def test_a_late_report_from_the_day_before_counts_toward_today() -> None:
    budget = CallBudget()
    budget(report(train_positions(), [1], poll_time=NEXT_DAY))

    budget(report(train_positions(), [1], poll_time=NEXT_DAY - timedelta(seconds=30)))

    assert budget.calls_today(KeyKind.TRAIN) == 2


def test_a_key_with_no_known_limit_is_counted_but_never_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    budget = CallBudget()

    for _ in range(20):
        budget(report(gtfs_realtime(Feed.GTFSRT_TRIP_UPDATES), [1] * 50))

    assert budget.calls_today(KeyKind.GTFSRT) == 1000
    assert caplog.text == ""
