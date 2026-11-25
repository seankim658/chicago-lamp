import logging
from datetime import UTC, datetime, timedelta

import pytest
from lamp_collector.feeds import Feed, FeedRequest, bus_vehicles, train_positions
from lamp_collector.fetch import FetchResult
from lamp_collector.journal import CallRecord
from lamp_collector.poll import CallOutcome
from lamp_collector.raw import CallId
from lamp_collector.responses import Reading, Status
from lamp_collector.schedule import PollReport
from lamp_collector.summary import PollSummary

WINDOW_START = datetime(2026, 11, 25, 4, 30, tzinfo=UTC)
NEXT_WINDOW = WINDOW_START + timedelta(minutes=10)
OK = Reading(Status.OK)
TIMEOUT = Reading(Status.TIMEOUT, error="ReadTimeout: timed out")


@pytest.fixture(autouse=True)
def capture_info(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)


def outcome(
    request: FeedRequest, poll_time: datetime, index: int, *readings: Reading
) -> CallOutcome:
    result = FetchResult(request, poll_time, poll_time, http_status=200, body=b"{}")
    return CallOutcome(
        tuple(
            CallRecord(CallId(poll_time, index, attempt), result, reading, None)
            for attempt, reading in enumerate(readings, start=1)
        )
    )


def train_report(poll_time: datetime, *readings: Reading) -> PollReport:
    return PollReport(
        Feed.TRAIN_POSITIONS, poll_time, [outcome(train_positions(), poll_time, 0, *readings)]
    )


def test_nothing_is_written_until_the_next_window_starts(caplog: pytest.LogCaptureFixture) -> None:
    summary = PollSummary([Feed.TRAIN_POSITIONS])

    summary(train_report(WINDOW_START, OK))
    summary(train_report(WINDOW_START + timedelta(minutes=9, seconds=30), OK))

    assert caplog.messages == []


def test_a_window_is_summed_up_per_feed_when_the_next_one_starts(
    caplog: pytest.LogCaptureFixture,
) -> None:
    summary = PollSummary([Feed.TRAIN_POSITIONS, Feed.BUS_VEHICLES])
    bus_time = WINDOW_START + timedelta(seconds=30)
    summary(
        PollReport(
            Feed.BUS_VEHICLES,
            bus_time,
            [
                outcome(bus_vehicles(["1"]), bus_time, 0, OK),
                outcome(bus_vehicles(["2"]), bus_time, 1, TIMEOUT, OK),
                outcome(bus_vehicles(["3"]), bus_time, 2, TIMEOUT, TIMEOUT),
            ],
        )
    )
    summary(train_report(WINDOW_START, OK))
    summary(train_report(WINDOW_START + timedelta(seconds=30), OK))

    summary(train_report(NEXT_WINDOW, OK))

    assert caplog.messages == [
        "bus_vehicles, 04:30 to 04:40 UTC: 1 polls, 3 calls, 2 ok, 2 retries, "
        "failed after retry: timeout 1",
        "train_positions, 04:30 to 04:40 UTC: 2 polls, 2 calls, 2 ok, 0 retries",
    ]


def test_an_expected_feed_with_no_polls_in_a_window_is_warned_about(
    caplog: pytest.LogCaptureFixture,
) -> None:
    summary = PollSummary([Feed.TRAIN_POSITIONS, Feed.BUS_VEHICLES])
    summary(train_report(WINDOW_START, OK))

    summary(train_report(NEXT_WINDOW, OK))

    assert ("WARNING", "bus_vehicles, 04:30 to 04:40 UTC: no polls") in [
        (record.levelname, record.getMessage()) for record in caplog.records
    ]


def test_a_feed_not_expected_is_summed_up_only_when_it_polled(
    caplog: pytest.LogCaptureFixture,
) -> None:
    summary = PollSummary([Feed.TRAIN_POSITIONS])
    summary(train_report(WINDOW_START, OK))

    summary(train_report(NEXT_WINDOW, OK))

    assert caplog.messages == [
        "train_positions, 04:30 to 04:40 UTC: 1 polls, 1 calls, 1 ok, 0 retries"
    ]


def test_a_late_poll_from_the_window_before_joins_the_current_one(
    caplog: pytest.LogCaptureFixture,
) -> None:
    summary = PollSummary([Feed.TRAIN_POSITIONS])
    summary(train_report(NEXT_WINDOW, OK))
    summary(train_report(NEXT_WINDOW - timedelta(seconds=30), OK))

    summary(train_report(NEXT_WINDOW + timedelta(minutes=10), OK))

    assert caplog.messages == [
        "train_positions, 04:40 to 04:50 UTC: 2 polls, 2 calls, 2 ok, 0 retries"
    ]
