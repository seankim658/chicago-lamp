import io
import logging
import os
import signal
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from lamp_collector import cli
from lamp_collector.cli import (
    EXIT_BAD_SETTINGS,
    EXIT_NOT_REPORTED,
    EXIT_UNHEALTHY,
    main,
    stop_on_signals,
)
from lamp_collector.logs import configure_logging

BUS_KEY = "Bus0Key0Bus0Key0Bus0Key0B"
TRAIN_KEY = "train0key0train0key0train0key012"
PING_URL = "https://hc-ping.example/4f1c0de0-secret-check-id"


@pytest.fixture
def no_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run from an empty folder, so there is no .env, with no settings in the environment."""
    monkeypatch.chdir(tmp_path)
    for variable in (
        "CTA_BUS_KEY",
        "CTA_TRAIN_KEY",
        "CTA_GTFSRT_KEY",
        "LAMP_BUFFER_DIR",
        "LAMP_HEALTH_PING_URL",
    ):
        monkeypatch.delenv(variable, raising=False)


@pytest.fixture
def log(monkeypatch: pytest.MonkeyPatch) -> Iterator[io.StringIO]:
    """Send the command's log to a string, then put the root logger back as it was."""
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    stream = io.StringIO()
    monkeypatch.setattr(cli, "configure_logging", lambda keys: configure_logging(keys, stream))
    yield stream
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)


@pytest.fixture
def empty_buffer(no_settings: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    buffer = tmp_path / "buffer"
    buffer.mkdir()
    monkeypatch.setenv("CTA_BUS_KEY", BUS_KEY)
    monkeypatch.setenv("CTA_TRAIN_KEY", TRAIN_KEY)
    monkeypatch.setenv("LAMP_BUFFER_DIR", str(buffer))
    return buffer


@pytest.fixture
def saved_handlers() -> Iterator[None]:
    saved = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}
    yield
    for number, handler in saved.items():
        signal.signal(number, handler)


@pytest.mark.usefixtures("no_settings")
def test_run_with_missing_settings_names_them_and_shows_no_value(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CTA_BUS_KEY", BUS_KEY)

    assert main(["run"]) == EXIT_BAD_SETTINGS

    error = capsys.readouterr().err
    assert "cta_train_key" in error
    assert "lamp_buffer_dir" in error
    assert BUS_KEY not in error


def test_a_command_is_required(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        main([])

    assert caught.value.code == 2
    assert "run" in capsys.readouterr().err


@pytest.mark.usefixtures("saved_handlers")
@pytest.mark.parametrize("signal_number", [signal.SIGTERM, signal.SIGINT])
def test_a_stop_signal_stops_the_collector(signal_number: signal.Signals) -> None:
    stops: list[None] = []
    stop_on_signals(lambda: stops.append(None))

    os.kill(os.getpid(), signal_number)

    assert stops == [None]


def test_health_on_a_buffer_with_no_polls_names_each_feed(
    empty_buffer: Path, log: io.StringIO
) -> None:
    assert main(["health"]) == EXIT_UNHEALTHY

    text = log.getvalue()
    assert "bus_vehicles: no successful poll in the last 10 minutes" in text
    assert "train_positions: no successful poll in the last 10 minutes" in text


def test_health_reports_its_problems_to_the_ping_url(
    empty_buffer: Path, log: io.StringIO, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[httpx.Request] = []

    def switch(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, text="OK")

    monkeypatch.setenv("LAMP_HEALTH_PING_URL", PING_URL)
    monkeypatch.setattr(
        cli, "heartbeat_client", lambda: httpx.Client(transport=httpx.MockTransport(switch))
    )

    assert main(["health"]) == EXIT_UNHEALTHY

    [request] = sent
    assert str(request.url) == f"{PING_URL}/fail"
    assert b"bus_vehicles" in request.content


def test_health_that_cannot_report_says_so_without_the_url(
    empty_buffer: Path, log: io.StringIO, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    monkeypatch.setenv("LAMP_HEALTH_PING_URL", PING_URL)
    monkeypatch.setattr(
        cli, "heartbeat_client", lambda: httpx.Client(transport=httpx.MockTransport(refuse))
    )

    assert main(["health"]) == EXIT_NOT_REPORTED

    text = log.getvalue()
    assert "the result was not reported" in text
    assert "secret-check-id" not in text


@pytest.mark.usefixtures("no_settings")
def test_health_with_missing_settings_exits_as_bad_settings() -> None:
    assert main(["health"]) == EXIT_BAD_SETTINGS
