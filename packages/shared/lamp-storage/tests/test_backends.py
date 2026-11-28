"""Behaviour specific to one backend."""

from collections.abc import Iterator
from pathlib import Path

import boto3
import pytest
from botocore.stub import Stubber
from lamp_storage import LocalStore, S3Credentials, S3Store, StorageError, s3_client
from moto import mock_aws

# A made-up key pair, which moto accepts like any other.
GIVEN_KEYS = S3Credentials("given-id", "given-secret")


@pytest.fixture
def s3_bucket() -> Iterator[str]:
    with mock_aws():
        boto3.client("s3").create_bucket(Bucket="lamp-test")
        yield "lamp-test"


def test_local_writes_leave_no_temporary_files(tmp_path: Path) -> None:
    store = LocalStore(tmp_path)

    store.put("a/b", b"x")
    store.put("a/b", b"y")
    store.put_new("a/c", b"z")
    store.put_new("a/c", b"z")

    assert sorted(path.name for path in (tmp_path / "a").iterdir()) == ["b", "c"]


def test_local_delete_removes_emptied_folders_but_not_the_root(tmp_path: Path) -> None:
    store = LocalStore(tmp_path)
    store.put("raw/bus/2026-11-20/a", b"x")
    store.put("raw/train/2026-11-20/b", b"x")

    store.delete("raw/bus/2026-11-20/a")

    assert not (tmp_path / "raw" / "bus").exists()
    assert (tmp_path / "raw" / "train" / "2026-11-20" / "b").exists()
    store.delete("raw/train/2026-11-20/b")
    assert tmp_path.exists()
    assert list(tmp_path.iterdir()) == []


def test_local_list_skips_dot_files(tmp_path: Path) -> None:
    store = LocalStore(tmp_path)
    store.put("a/b", b"x")
    (tmp_path / "a" / ".b.partial").write_bytes(b"left by a crash")

    assert list(store.list_keys()) == ["a/b"]


def test_local_put_under_a_file_raises_storage_error(tmp_path: Path) -> None:
    store = LocalStore(tmp_path)
    store.put("a", b"x")

    with pytest.raises(StorageError, match="parent of it is a file"):
        store.put("a/b", b"y")


def test_s3_store_lists_only_keys_under_its_own_prefix(s3_bucket: str) -> None:
    client = s3_client(endpoint_url=None)
    client.put_object(Bucket=s3_bucket, Key="other/raw/x", Body=b"x")
    store = S3Store(client, s3_bucket, prefix="lamp/")
    store.put("raw/y", b"y")

    assert list(store.list_keys("raw/")) == ["raw/y"]
    assert client.get_object(Bucket=s3_bucket, Key="lamp/raw/y")["Body"].read() == b"y"


def test_s3_store_prefix_must_end_with_a_slash(s3_bucket: str) -> None:
    with pytest.raises(StorageError, match="must end with '/'"):
        S3Store(s3_client(endpoint_url=None), s3_bucket, prefix="lamp")


def test_s3_service_error_names_the_action_key_and_code(s3_bucket: str) -> None:
    client = s3_client(endpoint_url=None)
    store = S3Store(client, s3_bucket)
    with Stubber(client) as stubber:
        stubber.add_client_error("put_object", service_error_code="SlowDown", http_status_code=503)

        with pytest.raises(StorageError, match=r"could not write 'a' in s3://lamp-test/: SlowDown"):
            store.put("a", b"x")


def test_s3_missing_bucket_is_a_storage_error(s3_bucket: str) -> None:
    store = S3Store(s3_client(endpoint_url=None), "no-such-bucket")

    with pytest.raises(StorageError, match="NoSuchBucket"):
        store.put("a", b"x")


def test_s3_client_uses_the_credentials_and_region_it_is_given(
    s3_bucket: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AWS_ACCESS_KEY_ID")
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY")
    monkeypatch.delenv("AWS_DEFAULT_REGION")
    client = s3_client(endpoint_url=None, credentials=GIVEN_KEYS, region="us-east-1")
    S3Store(client, s3_bucket).put("a", b"x")

    assert client.meta.region_name == "us-east-1"
    assert S3Store(client, s3_bucket).get("a") == b"x"


def test_s3_credentials_never_show_in_their_repr() -> None:
    assert "given-id" not in repr(GIVEN_KEYS)
    assert "given-secret" not in repr(GIVEN_KEYS)
