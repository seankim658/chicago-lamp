from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from .errors import ObjectConflictError, ObjectNotFoundError, StorageError
from .keys import validate_key, validate_prefix

if TYPE_CHECKING:
    from types_boto3_s3 import S3Client

# Codes S3 and R2 return when a create-only write finds an object already there.
_EXISTS_CODES = frozenset({"PreconditionFailed", "412"})
_MISSING_CODES = frozenset({"NoSuchKey", "404", "NotFound"})


@dataclass(frozen=True)
class S3Credentials:
    """An access key pair for an S3-compatible service, kept out of every repr."""

    access_key_id: str = field(repr=False)
    secret_access_key: str = field(repr=False)


def s3_client(
    endpoint_url: str | None,
    credentials: S3Credentials | None = None,
    region: str | None = None,
) -> "S3Client":
    """Build an S3 client for AWS S3, Cloudflare R2 or another S3-compatible service.

    Credentials and region not given here come from the standard AWS environment
    variables. Every upload carries a checksum that the service verifies before
    storing it.
    """
    config = Config(
        connect_timeout=5,
        read_timeout=30,
        retries={"max_attempts": 5, "mode": "standard"},
        request_checksum_calculation="when_supported",
    )
    if credentials is None:
        return boto3.client("s3", endpoint_url=endpoint_url, config=config, region_name=region)
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        config=config,
        region_name=region,
        aws_access_key_id=credentials.access_key_id,
        aws_secret_access_key=credentials.secret_access_key,
    )


class S3Store:
    """A store in one bucket of an S3-compatible service, under an optional prefix."""

    def __init__(self, client: "S3Client", bucket: str, prefix: str = "") -> None:
        validate_prefix(prefix)
        if prefix and not prefix.endswith("/"):
            raise StorageError(f"an S3 store prefix must end with '/', got {prefix!r}")
        self._client = client
        self._bucket = bucket
        self._prefix = prefix

    def put(self, key: str, data: bytes) -> None:
        with self._errors("write", key):
            self._client.put_object(Bucket=self._bucket, Key=self._name(key), Body=data)

    def put_new(self, key: str, data: bytes) -> bool:
        try:
            with self._errors("write", key):
                self._client.put_object(
                    Bucket=self._bucket, Key=self._name(key), Body=data, IfNoneMatch="*"
                )
        except _ExistsError:
            if self.get(key) == data:
                return False
            raise ObjectConflictError(
                f"a different object already exists at {key!r} in {self._where()}"
            ) from None
        return True

    def get(self, key: str) -> bytes:
        with self._errors("read", key):
            response = self._client.get_object(Bucket=self._bucket, Key=self._name(key))
            return response["Body"].read()

    def exists(self, key: str) -> bool:
        try:
            with self._errors("check", key):
                self._client.head_object(Bucket=self._bucket, Key=self._name(key))
        except ObjectNotFoundError:
            return False
        return True

    def list_keys(self, prefix: str = "") -> Iterator[str]:
        validate_prefix(prefix)
        paginator = self._client.get_paginator("list_objects_v2")
        with self._errors("list", prefix):
            pages = paginator.paginate(Bucket=self._bucket, Prefix=self._prefix + prefix)
            names = [item["Key"] for page in pages for item in page.get("Contents", [])]
        return iter(sorted(name.removeprefix(self._prefix) for name in names))

    def delete(self, key: str) -> None:
        with self._errors("delete", key):
            self._client.delete_object(Bucket=self._bucket, Key=self._name(key))

    def _name(self, key: str) -> str:
        return self._prefix + validate_key(key)

    def _where(self) -> str:
        return f"s3://{self._bucket}/{self._prefix}"

    def _errors(self, action: str, key: str) -> "_ErrorMapper":
        return _ErrorMapper(action, key, self._where())


class _ExistsError(Exception):
    """A create-only write found an object already under its key."""


class _ErrorMapper:
    """Turn botocore failures into storage errors that name the action and key."""

    def __init__(self, action: str, key: str, where: str) -> None:
        self._action = action
        self._key = key
        self._where = where

    def __enter__(self) -> None:
        return None

    def __exit__(self, kind: object, error: BaseException | None, trace: object) -> None:
        if isinstance(error, ClientError):
            code = str(error.response.get("Error", {}).get("Code", ""))
            if code in _EXISTS_CODES:
                raise _ExistsError from error
            if code in _MISSING_CODES:
                raise ObjectNotFoundError(f"no object {self._key!r} in {self._where}") from error
            raise StorageError(self._message(code or "unknown error")) from error
        if isinstance(error, BotoCoreError):
            raise StorageError(self._message(str(error))) from error

    def _message(self, reason: str) -> str:
        return f"could not {self._action} {self._key!r} in {self._where}: {reason}"
