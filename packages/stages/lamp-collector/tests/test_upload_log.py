from pathlib import Path

import pytest
from lamp_collector.upload_log import (
    InvalidUploadLogError,
    UploadEntry,
    UploadLog,
    upload_entry,
    upload_log_key,
)
from lamp_storage import LocalStore

RAW_KEY = "raw/bus_vehicles/2026-11-27/20261127T150000Z-00-1.json.gz"
JOURNAL_KEY = "coverage/journal/2026-11-27/20261127T15Z.jsonl"


def test_a_file_is_logged_under_its_collection_date() -> None:
    assert upload_log_key(RAW_KEY) == "uploads/2026-11-27.jsonl"
    assert upload_log_key(JOURNAL_KEY) == "uploads/2026-11-27.jsonl"


@pytest.mark.parametrize(
    "key",
    [
        "raw/bus_vehicles/x.json.gz",
        "raw/bus_vehicles/2026-13-01/x.json.gz",
        "coverage/2026-11-27.parquet",
        "uploads/2026-11-27.jsonl",
    ],
)
def test_a_key_without_a_collection_date_folder_is_refused(key: str) -> None:
    with pytest.raises(InvalidUploadLogError, match="collection date"):
        upload_log_key(key)


def test_an_entry_records_the_size_and_md5_of_the_data() -> None:
    entry = upload_entry(RAW_KEY, b"hello")

    assert entry == UploadEntry(RAW_KEY, 5, "5d41402abc4b2a76b9719d911017c592")


def test_recorded_entries_read_back(tmp_path: Path) -> None:
    log = UploadLog(LocalStore(tmp_path))
    raw, journal = upload_entry(RAW_KEY, b"a"), upload_entry(JOURNAL_KEY, b"b")

    log.record(raw)
    log.record(journal)

    assert UploadLog(LocalStore(tmp_path)).entries() == {RAW_KEY: raw, JOURNAL_KEY: journal}


def test_an_empty_buffer_has_no_entries(tmp_path: Path) -> None:
    assert UploadLog(LocalStore(tmp_path)).entries() == {}


def test_a_line_cut_off_by_a_crash_is_ignored_and_then_replaced(tmp_path: Path) -> None:
    store = LocalStore(tmp_path)
    log = UploadLog(store)
    log.record(upload_entry(RAW_KEY, b"a"))
    path = tmp_path / "uploads" / "2026-11-27.jsonl"
    path.write_bytes(path.read_bytes() + b'{"key":"raw/bus_vehi')

    assert list(log.entries()) == [RAW_KEY]
    log.record(upload_entry(JOURNAL_KEY, b"b"))
    assert list(log.entries()) == [RAW_KEY, JOURNAL_KEY]


def test_a_malformed_whole_line_names_the_log_file(tmp_path: Path) -> None:
    (tmp_path / "uploads").mkdir()
    (tmp_path / "uploads" / "2026-11-27.jsonl").write_bytes(b'{"key":"a"}\n')

    with pytest.raises(InvalidUploadLogError, match=r"uploads/2026-11-27\.jsonl"):
        UploadLog(LocalStore(tmp_path)).entries()
