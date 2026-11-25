from pathlib import Path

import pytest
from lamp_collector.keys import KeyKind
from lamp_collector.settings import CollectorSettings, Settings
from pydantic import ValidationError

BUS_KEY = "Bus0Key0Bus0Key0Bus0Key0B"
TRAIN_KEY = "train0key0train0key0train0key012"
OTHER_KEY = "other0key0other0key0other0key0"
GTFSRT_KEY = "gtfsrt0key0gtfsrt0key0gtfsrt0k"


@pytest.fixture(autouse=True)
def no_cta_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CTA_BUS_KEY", raising=False)
    monkeypatch.delenv("CTA_TRAIN_KEY", raising=False)
    monkeypatch.delenv("CTA_GTFSRT_KEY", raising=False)
    monkeypatch.delenv("LAMP_BUFFER_DIR", raising=False)


def env_file(tmp_path: Path, text: str) -> Path:
    path = tmp_path / ".env"
    path.write_text(text)
    return path


def test_keys_are_read_from_the_env_file(tmp_path: Path) -> None:
    path = env_file(
        tmp_path, f"CTA_BUS_KEY={BUS_KEY}\nCTA_TRAIN_KEY={TRAIN_KEY}\nAWS_DEFAULT_REGION=auto\n"
    )

    keys = Settings(_env_file=path).api_keys()

    assert keys.for_kind(KeyKind.BUS) == BUS_KEY
    assert keys.for_kind(KeyKind.TRAIN) == TRAIN_KEY


def test_environment_variables_win_over_the_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = env_file(tmp_path, f"CTA_BUS_KEY={OTHER_KEY}\nCTA_TRAIN_KEY={TRAIN_KEY}\n")
    monkeypatch.setenv("CTA_BUS_KEY", BUS_KEY)

    assert Settings(_env_file=path).api_keys().bus == BUS_KEY


def test_a_missing_env_file_is_not_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CTA_BUS_KEY", BUS_KEY)
    monkeypatch.setenv("CTA_TRAIN_KEY", TRAIN_KEY)

    assert Settings(_env_file=tmp_path / "absent.env").api_keys().train == TRAIN_KEY


def test_a_missing_key_is_named_without_showing_the_other(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CTA_BUS_KEY", BUS_KEY)

    with pytest.raises(ValidationError, match="cta_train_key") as caught:
        Settings(_env_file=None)

    assert BUS_KEY not in str(caught.value)


def test_a_malformed_key_is_refused_without_showing_either_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CTA_BUS_KEY", BUS_KEY)
    monkeypatch.setenv("CTA_TRAIN_KEY", f"{TRAIN_KEY} ")

    with pytest.raises(ValidationError, match="CTA_TRAIN_KEY must be") as caught:
        Settings(_env_file=None)

    assert BUS_KEY not in str(caught.value)
    assert TRAIN_KEY not in str(caught.value)


def test_repr_never_shows_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CTA_BUS_KEY", BUS_KEY)
    monkeypatch.setenv("CTA_TRAIN_KEY", TRAIN_KEY)

    text = repr(Settings(_env_file=None))

    assert BUS_KEY not in text
    assert TRAIN_KEY not in text


def test_gtfs_realtime_key_is_read_when_set(tmp_path: Path) -> None:
    path = env_file(
        tmp_path, f"CTA_BUS_KEY={BUS_KEY}\nCTA_TRAIN_KEY={TRAIN_KEY}\nCTA_GTFSRT_KEY={GTFSRT_KEY}\n"
    )

    assert Settings(_env_file=path).api_keys().gtfsrt == GTFSRT_KEY


def test_an_empty_gtfs_realtime_key_counts_as_unset(tmp_path: Path) -> None:
    path = env_file(
        tmp_path, f"CTA_BUS_KEY={BUS_KEY}\nCTA_TRAIN_KEY={TRAIN_KEY}\nCTA_GTFSRT_KEY=\n"
    )

    assert not Settings(_env_file=path).api_keys().has(KeyKind.GTFSRT)


def test_the_collector_reads_its_buffer_folder(tmp_path: Path) -> None:
    path = env_file(
        tmp_path,
        f"CTA_BUS_KEY={BUS_KEY}\nCTA_TRAIN_KEY={TRAIN_KEY}\nLAMP_BUFFER_DIR=/var/lib/lamp\n",
    )

    assert CollectorSettings(_env_file=path).lamp_buffer_dir == Path("/var/lib/lamp")


def test_the_collector_refuses_to_start_without_a_buffer_folder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CTA_BUS_KEY", BUS_KEY)
    monkeypatch.setenv("CTA_TRAIN_KEY", TRAIN_KEY)

    with pytest.raises(ValidationError, match="lamp_buffer_dir"):
        CollectorSettings(_env_file=None)
