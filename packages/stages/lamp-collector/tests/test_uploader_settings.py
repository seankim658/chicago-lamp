from pathlib import Path

import pytest
from lamp_collector.settings import UploaderSettings
from pydantic import ValidationError

ACCESS_KEY_ID = "uploader-access-key-id"
STORAGE_KEY = "uploader-secret-access-key-0123456789"
ENDPOINT = "https://0123abcd.r2.cloudflarestorage.com"
VARIABLES = (
    "LAMP_BUFFER_DIR",
    "LAMP_S3_ENDPOINT_URL",
    "LAMP_PRIVATE_BUCKET",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_DEFAULT_REGION",
    "CTA_BUS_KEY",
    "CTA_TRAIN_KEY",
)


@pytest.fixture
def settings_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every uploader setting in the environment, run from a folder with no .env."""
    monkeypatch.chdir(tmp_path)
    for variable in VARIABLES:
        monkeypatch.delenv(variable, raising=False)
    buffer = tmp_path / "buffer"
    buffer.mkdir()
    monkeypatch.setenv("LAMP_BUFFER_DIR", str(buffer))
    monkeypatch.setenv("LAMP_S3_ENDPOINT_URL", ENDPOINT)
    monkeypatch.setenv("LAMP_PRIVATE_BUCKET", "chicago-lamp-private")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", ACCESS_KEY_ID)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", STORAGE_KEY)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "auto")
    return buffer


def test_the_uploader_needs_no_cta_keys(settings_env: Path) -> None:
    settings = UploaderSettings()

    assert settings.lamp_buffer_dir == settings_env
    assert settings.lamp_private_bucket == "chicago-lamp-private"
    assert settings.credentials().secret_access_key == STORAGE_KEY


def test_the_endpoint_may_be_left_out_for_aws(
    settings_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LAMP_S3_ENDPOINT_URL")

    assert UploaderSettings().lamp_s3_endpoint_url is None


@pytest.mark.usefixtures("settings_env")
def test_an_endpoint_that_is_not_https_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAMP_S3_ENDPOINT_URL", "http://0123abcd.r2.cloudflarestorage.com")

    with pytest.raises(ValidationError, match="LAMP_S3_ENDPOINT_URL must be an https URL"):
        UploaderSettings()


@pytest.mark.usefixtures("settings_env")
def test_a_buffer_folder_that_does_not_exist_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LAMP_BUFFER_DIR", str(tmp_path / "absent"))

    with pytest.raises(ValidationError, match="lamp_buffer_dir"):
        UploaderSettings()


@pytest.mark.usefixtures("settings_env")
def test_a_missing_secret_key_is_named_without_showing_the_other_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY")

    with pytest.raises(ValidationError) as caught:
        UploaderSettings()

    assert "aws_secret_access_key" in str(caught.value)
    assert ACCESS_KEY_ID not in str(caught.value)


@pytest.mark.usefixtures("settings_env")
def test_the_secrets_to_remove_from_logs_are_the_key_pair() -> None:
    assert set(UploaderSettings().secrets()) == {ACCESS_KEY_ID, STORAGE_KEY}
