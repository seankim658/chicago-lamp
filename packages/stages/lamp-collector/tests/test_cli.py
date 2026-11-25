import os
import signal
from collections.abc import Iterator
from pathlib import Path

import pytest
from lamp_collector.cli import EXIT_BAD_SETTINGS, main, stop_on_signals

BUS_KEY = "Bus0Key0Bus0Key0Bus0Key0B"


@pytest.fixture
def no_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run from an empty folder, so there is no .env, with no settings in the environment."""
    monkeypatch.chdir(tmp_path)
    for variable in ("CTA_BUS_KEY", "CTA_TRAIN_KEY", "CTA_GTFSRT_KEY", "LAMP_BUFFER_DIR"):
        monkeypatch.delenv(variable, raising=False)


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
