"""The record of buffered files whose upload to object storage was verified.

There is one file per collection date, with one JSON line per uploaded file
giving its key, size and MD5. Reading it never needs object storage, so a pass
can skip what is already uploaded without listing the bucket, and the size and
MD5 show whether a buffered copy still matches what was uploaded.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import date

from lamp_storage import LocalStore

UPLOAD_LOG_PREFIX = "uploads/"

# Where the collection date folder sits in each kind of key the collector writes.
_DATE_SEGMENTS = {"raw": 2, "coverage": 2}


class InvalidUploadLogError(ValueError):
    """A key has no collection date folder, or a log line can't be read."""


@dataclass(frozen=True)
class UploadEntry:
    key: str
    size: int
    md5: str


def upload_entry(key: str, data: bytes) -> UploadEntry:
    md5 = hashlib.md5(data, usedforsecurity=False).hexdigest()
    return UploadEntry(key, len(data), md5)


def upload_log_key(key: str) -> str:
    """The log file for a buffered file, named by the file's collection date."""
    return f"{UPLOAD_LOG_PREFIX}{_collection_date(key).isoformat()}.jsonl"


class UploadLog:
    def __init__(self, store: LocalStore) -> None:
        self._store = store

    def record(self, entry: UploadEntry) -> None:
        line = json.dumps({"key": entry.key, "size": entry.size, "md5": entry.md5})
        self._store.append_line(upload_log_key(entry.key), line.encode())

    def entries(self) -> dict[str, UploadEntry]:
        """Every logged upload by key, from every log file."""
        entries: dict[str, UploadEntry] = {}
        for log_key in self._store.list_keys(UPLOAD_LOG_PREFIX):
            # A crash during an append can leave half a line at the end.
            for line in self._store.get(log_key).split(b"\n")[:-1]:
                entry = _parse_line(log_key, line)
                entries[entry.key] = entry
        return entries


def _collection_date(key: str) -> date:
    segments = key.split("/")
    position = _DATE_SEGMENTS.get(segments[0])
    if position is not None and len(segments) > position + 1:
        try:
            return date.fromisoformat(segments[position])
        except ValueError:
            pass
    raise InvalidUploadLogError(f"key {key!r} has no collection date folder")


def _parse_line(log_key: str, line: bytes) -> UploadEntry:
    try:
        row = json.loads(line)
        return UploadEntry(str(row["key"]), int(row["size"]), str(row["md5"]))
    except (ValueError, KeyError, TypeError) as error:
        raise InvalidUploadLogError(f"cannot read a line of {log_key}: {line[:80]!r}") from error
