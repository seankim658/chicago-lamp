from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import boto3
import pytest
from lamp_collector.upload import Archive, Uploader, UploadReport
from lamp_collector.upload_log import UploadLog, upload_entry
from lamp_storage import LocalStore, S3Store, StorageError, s3_client
from moto import mock_aws

BUCKET = "lamp-test"
RAW_KEY = "raw/bus_vehicles/2026-11-27/20261127T150000Z-00-1.json.gz"
OTHER_RAW_KEY = "raw/train_positions/2026-11-27/20261127T150000Z-00-1.json.gz"
JOURNAL_KEY = "coverage/journal/2026-11-27/20261127T15Z.jsonl"
# The journal file above may be uploaded from 16:05 UTC.
JOURNAL_DUE = datetime(2026, 11, 27, 16, 5, tzinfo=UTC)
ROW = b'{"feed":"bus_vehicles"}\n'


@pytest.fixture
def archive(monkeypatch: pytest.MonkeyPatch) -> Iterator[S3Store]:
    """A bucket in moto, with dummy credentials so no test can reach a real account."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    with mock_aws():
        boto3.client("s3").create_bucket(Bucket=BUCKET)
        yield S3Store(s3_client(endpoint_url=None), BUCKET)


@pytest.fixture
def buffer(tmp_path: Path) -> LocalStore:
    return LocalStore(tmp_path)


class UnreachableStore:
    """An archive whose every write fails, as when object storage is down."""

    def __init__(self) -> None:
        self.writes = 0

    def put_new(self, key: str, data: bytes) -> bool:
        self.writes += 1
        raise StorageError(f"could not write {key!r}: connection refused")


def uploader(buffer: LocalStore, archive: Archive) -> Uploader:
    return Uploader(buffer, archive, UploadLog(buffer))


def test_raw_files_are_uploaded_as_soon_as_they_are_seen(
    buffer: LocalStore, archive: S3Store
) -> None:
    buffer.put(RAW_KEY, b"raw body")

    report = uploader(buffer, archive).run_pass(datetime(2026, 11, 27, 15, 0, 1, tzinfo=UTC))

    assert report == UploadReport(uploaded=1)
    assert archive.get(RAW_KEY) == b"raw body"
    assert UploadLog(buffer).entries() == {RAW_KEY: upload_entry(RAW_KEY, b"raw body")}


def test_a_journal_file_waits_until_5_minutes_after_its_hour(
    buffer: LocalStore, archive: S3Store
) -> None:
    buffer.put(JOURNAL_KEY, ROW)
    early = JOURNAL_DUE.replace(minute=4, second=59)

    assert uploader(buffer, archive).run_pass(early) == UploadReport(waiting=1)
    assert not archive.exists(JOURNAL_KEY)
    assert uploader(buffer, archive).run_pass(JOURNAL_DUE) == UploadReport(uploaded=1)
    assert archive.get(JOURNAL_KEY) == ROW


def test_a_journal_file_ending_in_half_a_row_waits_for_the_collector_to_repair_it(
    buffer: LocalStore, archive: S3Store
) -> None:
    buffer.put(JOURNAL_KEY, ROW + b'{"feed":"bus_')

    assert uploader(buffer, archive).run_pass(JOURNAL_DUE) == UploadReport(waiting=1)
    assert not archive.exists(JOURNAL_KEY)


def test_a_second_pass_uploads_nothing(buffer: LocalStore, archive: S3Store) -> None:
    buffer.put(RAW_KEY, b"raw body")
    buffer.put(JOURNAL_KEY, ROW)
    uploader(buffer, archive).run_pass(JOURNAL_DUE)

    assert uploader(buffer, archive).run_pass(JOURNAL_DUE) == UploadReport()
    assert len(UploadLog(buffer).entries()) == 2


def test_an_identical_object_already_in_storage_counts_as_uploaded(
    buffer: LocalStore, archive: S3Store
) -> None:
    buffer.put(RAW_KEY, b"raw body")
    archive.put(RAW_KEY, b"raw body")

    report = uploader(buffer, archive).run_pass(JOURNAL_DUE)

    assert report == UploadReport(already_there=1)
    assert RAW_KEY in UploadLog(buffer).entries()


def test_a_different_object_in_storage_is_reported_and_left_alone(
    buffer: LocalStore, archive: S3Store, caplog: pytest.LogCaptureFixture
) -> None:
    buffer.put(RAW_KEY, b"buffered body")
    buffer.put(OTHER_RAW_KEY, b"other body")
    archive.put(RAW_KEY, b"archived body")

    report = uploader(buffer, archive).run_pass(JOURNAL_DUE)

    assert report == UploadReport(uploaded=1, failed=1)
    assert archive.get(RAW_KEY) == b"archived body"
    assert buffer.get(RAW_KEY) == b"buffered body"
    assert list(UploadLog(buffer).entries()) == [OTHER_RAW_KEY]
    assert "a different object already exists" in caplog.text


def test_unreachable_storage_stops_the_pass_and_leaves_every_file(buffer: LocalStore) -> None:
    for second in range(5):
        buffer.put(f"raw/bus_vehicles/2026-11-27/20261127T15000{second}Z-00-1.json.gz", b"x")
    archive = UnreachableStore()

    report = uploader(buffer, archive).run_pass(JOURNAL_DUE)

    assert report == UploadReport(failed=3, stopped_early=True)
    assert archive.writes == 3
    assert UploadLog(buffer).entries() == {}
    assert len(list(buffer.list_keys("raw/"))) == 5


def test_only_raw_files_and_journal_files_are_uploaded(
    buffer: LocalStore, archive: S3Store
) -> None:
    buffer.put(RAW_KEY, b"raw body")
    buffer.put("notes/readme.txt", b"not collected data")
    UploadLog(buffer).record(upload_entry(OTHER_RAW_KEY, b"other body"))

    uploader(buffer, archive).run_pass(JOURNAL_DUE)

    assert list(archive.list_keys()) == [RAW_KEY]


def test_a_journal_file_with_an_unexpected_name_is_reported(
    buffer: LocalStore, archive: S3Store
) -> None:
    buffer.put("coverage/journal/2026-11-27/latest.jsonl", ROW)

    assert uploader(buffer, archive).run_pass(JOURNAL_DUE) == UploadReport(failed=1)
    assert list(archive.list_keys()) == []


def test_a_report_with_a_failure_is_not_ok() -> None:
    assert UploadReport(uploaded=3).ok
    assert not UploadReport(uploaded=3, failed=1).ok
