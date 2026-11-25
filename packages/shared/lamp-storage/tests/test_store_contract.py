"""Behaviour every Store must share, run against each backend."""

from collections.abc import Iterator
from pathlib import Path

import boto3
import pytest
from lamp_storage import (
    InvalidKeyError,
    LocalStore,
    ObjectConflictError,
    ObjectNotFoundError,
    S3Store,
    Store,
    s3_client,
)
from moto import mock_aws

BUCKET = "lamp-test"


@pytest.fixture(params=["local", "s3"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Store]:
    if request.param == "local":
        yield LocalStore(tmp_path / "store")
        return
    with mock_aws():
        boto3.client("s3").create_bucket(Bucket=BUCKET)
        yield S3Store(s3_client(endpoint_url=None), BUCKET, prefix="lamp/")


def test_put_then_get_returns_the_same_bytes(store: Store) -> None:
    store.put("raw/bus/2026-11-20/a.json.gz", b"\x1f\x8b payload")

    assert store.get("raw/bus/2026-11-20/a.json.gz") == b"\x1f\x8b payload"


def test_put_replaces_an_existing_object(store: Store) -> None:
    store.put("events/a.parquet", b"first")
    store.put("events/a.parquet", b"second")

    assert store.get("events/a.parquet") == b"second"


def test_put_new_writes_when_nothing_is_there(store: Store) -> None:
    assert store.put_new("raw/a.gz", b"data") is True
    assert store.get("raw/a.gz") == b"data"


def test_put_new_with_identical_data_reports_it_was_already_there(store: Store) -> None:
    store.put_new("raw/a.gz", b"data")

    assert store.put_new("raw/a.gz", b"data") is False


def test_put_new_with_different_data_raises_and_keeps_the_original(store: Store) -> None:
    store.put_new("raw/a.gz", b"original")

    with pytest.raises(ObjectConflictError, match=r"raw/a\.gz"):
        store.put_new("raw/a.gz", b"changed")
    assert store.get("raw/a.gz") == b"original"


def test_get_of_a_missing_key_raises_not_found(store: Store) -> None:
    with pytest.raises(ObjectNotFoundError, match="missing"):
        store.get("missing")


def test_exists_reports_whether_an_object_is_there(store: Store) -> None:
    store.put("a/b", b"x")

    assert store.exists("a/b") is True
    assert store.exists("a/c") is False
    assert store.exists("a") is False


def test_list_keys_returns_sorted_keys_under_the_prefix(store: Store) -> None:
    for key in ["raw/b/2", "raw/a/1", "raw/b/1", "rawer/x", "compacted/z"]:
        store.put(key, b"x")

    assert list(store.list_keys("raw/")) == ["raw/a/1", "raw/b/1", "raw/b/2"]
    assert list(store.list_keys("raw")) == ["raw/a/1", "raw/b/1", "raw/b/2", "rawer/x"]
    assert list(store.list_keys()) == [
        "compacted/z",
        "raw/a/1",
        "raw/b/1",
        "raw/b/2",
        "rawer/x",
    ]


def test_list_keys_with_a_deep_prefix_returns_only_keys_under_it(store: Store) -> None:
    for key in ["raw/b/2", "raw/a/1", "raw/b/1", "raw/bb/3"]:
        store.put(key, b"x")

    assert list(store.list_keys("raw/b/")) == ["raw/b/1", "raw/b/2"]
    assert list(store.list_keys("raw/b")) == ["raw/b/1", "raw/b/2", "raw/bb/3"]
    assert list(store.list_keys("raw/b/1")) == ["raw/b/1"]
    assert list(store.list_keys("raw/c/")) == []
    assert list(store.list_keys("raw/b/1/")) == []


def test_list_keys_of_an_empty_store_is_empty(store: Store) -> None:
    assert list(store.list_keys()) == []


def test_delete_removes_the_object(store: Store) -> None:
    store.put("a/b", b"x")

    store.delete("a/b")

    assert store.exists("a/b") is False
    assert list(store.list_keys()) == []


def test_delete_of_a_missing_key_does_nothing(store: Store) -> None:
    store.delete("never/written")


@pytest.mark.parametrize(
    "key",
    ["", "/abs", "a//b", "a/", "../escape", "a/../b", "a/./b", ".hidden", "a\\b"],
)
def test_every_operation_refuses_an_invalid_key(store: Store, key: str) -> None:
    with pytest.raises(InvalidKeyError):
        store.put(key, b"x")
    with pytest.raises(InvalidKeyError):
        store.put_new(key, b"x")
    with pytest.raises(InvalidKeyError):
        store.get(key)
    with pytest.raises(InvalidKeyError):
        store.exists(key)
    with pytest.raises(InvalidKeyError):
        store.delete(key)


def test_list_keys_refuses_an_invalid_prefix(store: Store) -> None:
    with pytest.raises(InvalidKeyError):
        list(store.list_keys("../"))
