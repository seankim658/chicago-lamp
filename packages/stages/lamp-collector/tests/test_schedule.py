import logging
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
from lamp_collector.feeds import Feed, FeedRequest, train_positions
from lamp_collector.poll import CallOutcome, Poll, PollRunner
from lamp_collector.schedule import (
    FeedLoop,
    FeedSchedule,
    InvalidScheduleError,
    Pacer,
    PollReport,
    next_poll_time,
)

START = datetime(2026, 11, 25, 4, 30, 7, tzinfo=UTC)
FIRST_POLL = datetime(2026, 11, 25, 4, 30, 30, tzinfo=UTC)
THIRTY_SECONDS = timedelta(seconds=30)


class FakePacer(Pacer):
    """A clock that jumps straight to each moment waited for, and stops after some polls."""

    def __init__(self, start: datetime, polls: int) -> None:
        self.time = start
        self._polls_left = polls
        super().__init__(clock=lambda: self.time)

    def wait_until(self, moment: datetime) -> bool:
        if self._polls_left == 0:
            return False
        self._polls_left -= 1
        self.time = max(self.time, moment)
        return True


class FakeRunner(PollRunner):
    """Record each poll and take the given time to run it."""

    def __init__(self, pacer: FakePacer, takes: timedelta = timedelta(seconds=1)) -> None:
        self.polls: list[Poll] = []
        self._pacer = pacer
        self._takes = takes

    def run(self, poll: Poll) -> list[CallOutcome]:
        self.polls.append(poll)
        self._pacer.time += self._takes
        return []


def train_schedule(interval: timedelta = THIRTY_SECONDS) -> FeedSchedule:
    return FeedSchedule(Feed.TRAIN_POSITIONS, interval, lambda: (train_positions(),))


@pytest.mark.parametrize(
    ("now", "interval", "expected"),
    [
        (START, THIRTY_SECONDS, FIRST_POLL),
        (FIRST_POLL, THIRTY_SECONDS, FIRST_POLL + THIRTY_SECONDS),
        (START, timedelta(minutes=1), datetime(2026, 11, 25, 4, 31, tzinfo=UTC)),
        (START, timedelta(minutes=5), datetime(2026, 11, 25, 4, 35, tzinfo=UTC)),
        (START, timedelta(hours=6), datetime(2026, 11, 25, 6, 0, tzinfo=UTC)),
    ],
)
def test_the_next_poll_is_the_first_whole_interval_after_now(
    now: datetime, interval: timedelta, expected: datetime
) -> None:
    assert next_poll_time(now, interval) == expected


@pytest.mark.parametrize("seconds", [7, 10, 0])
def test_an_interval_the_clock_cant_line_up_with_is_refused(seconds: int) -> None:
    with pytest.raises(InvalidScheduleError, match="must divide a day evenly"):
        train_schedule(timedelta(seconds=seconds))


def test_polls_run_on_the_wall_clock_with_a_retry_deadline_before_the_next_poll() -> None:
    pacer = FakePacer(START, polls=3)
    runner = FakeRunner(pacer)

    FeedLoop(train_schedule(), runner, pacer).run()

    assert [poll.poll_time_utc for poll in runner.polls] == [
        FIRST_POLL,
        FIRST_POLL + THIRTY_SECONDS,
        FIRST_POLL + 2 * THIRTY_SECONDS,
    ]
    assert runner.polls[0].retry_by == FIRST_POLL + timedelta(seconds=20)
    assert runner.polls[0].requests == (train_positions(),)


def test_a_poll_that_runs_long_skips_the_times_it_overran(
    caplog: pytest.LogCaptureFixture,
) -> None:
    pacer = FakePacer(START, polls=2)
    runner = FakeRunner(pacer, takes=timedelta(seconds=70))

    FeedLoop(train_schedule(), runner, pacer).run()

    assert [poll.poll_time_utc for poll in runner.polls] == [
        FIRST_POLL,
        FIRST_POLL + 3 * THIRTY_SECONDS,
    ]
    assert "ran past 2 later poll time(s)" in caplog.text


def test_every_listener_gets_every_finished_poll() -> None:
    pacer = FakePacer(START, polls=2)
    first: list[PollReport] = []
    second: list[PollReport] = []

    FeedLoop(train_schedule(), FakeRunner(pacer), pacer, [first.append, second.append]).run()

    assert [report.poll_time_utc for report in first] == [
        FIRST_POLL,
        FIRST_POLL + THIRTY_SECONDS,
    ]
    assert first == second
    assert all(report.feed is Feed.TRAIN_POSITIONS for report in first)


def test_a_poll_that_raises_is_logged_and_the_next_poll_still_runs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    pacer = FakePacer(START, polls=2)
    runner = FakeRunner(pacer)
    reports: list[PollReport] = []

    def fail_once(report: PollReport) -> None:
        if not reports:
            reports.append(report)
            raise RuntimeError("listener broke")
        reports.append(report)

    with caplog.at_level(logging.ERROR):
        FeedLoop(train_schedule(), runner, pacer, [fail_once]).run()

    assert len(runner.polls) == 2
    assert "train_positions poll at 2026-11-25T04:30:30+00:00 failed" in caplog.text
    assert "listener broke" in caplog.text


def test_a_poll_with_no_calls_is_skipped_with_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    pacer = FakePacer(START, polls=1)
    runner = FakeRunner(pacer)
    no_calls: tuple[FeedRequest, ...] = ()
    schedule = FeedSchedule(Feed.BUS_VEHICLES, THIRTY_SECONDS, lambda: no_calls)

    FeedLoop(schedule, runner, pacer).run()

    assert runner.polls == []
    assert "bus_vehicles poll at 2026-11-25T04:30:30+00:00 has no calls to make" in caplog.text


def test_waiting_for_a_moment_already_past_returns_at_once() -> None:
    pacer = Pacer()

    assert pacer.wait_until(datetime.now(UTC) - timedelta(seconds=1)) is True


def test_stopping_the_pacer_ends_a_wait_early() -> None:
    pacer = Pacer()
    threading.Timer(0.05, pacer.stop).start()
    started = time.monotonic()

    assert pacer.wait_until(datetime.now(UTC) + timedelta(seconds=30)) is False
    assert time.monotonic() - started < 5
