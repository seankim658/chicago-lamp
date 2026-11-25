import errno
import os
import threading
from pathlib import Path

import pytest
from lamp_storage import InvalidKeyError, LocalStore, StorageError

KEY = "coverage/journal/2026-11-24/20261125T04Z.jsonl"


@pytest.fixture
def store(tmp_path: Path) -> LocalStore:
    return LocalStore(tmp_path)


def test_lines_are_added_in_order_and_create_the_object(store: LocalStore) -> None:
    store.append_line(KEY, b'{"n": 1}')
    store.append_line(KEY, b'{"n": 2}')

    assert store.get(KEY) == b'{"n": 1}\n{"n": 2}\n'
    assert list(store.list_keys()) == [KEY]


@pytest.mark.parametrize(
    ("left_by_crash", "expected"),
    [
        (b"a\nhalf a li", b"a\nb\n"),
        (b"half a li", b"b\n"),
    ],
)
def test_a_line_torn_by_a_crash_is_dropped_by_the_next_append(
    store: LocalStore, tmp_path: Path, left_by_crash: bytes, expected: bytes
) -> None:
    path = tmp_path / KEY
    path.parent.mkdir(parents=True)
    path.write_bytes(left_by_crash)

    store.append_line(KEY, b"b")

    assert store.get(KEY) == expected


@pytest.mark.parametrize(
    ("line", "message"),
    [
        (b"", "must not be empty"),
        (b"a\nb", "must not hold a newline"),
    ],
)
def test_a_line_that_is_not_one_whole_line_is_refused(
    store: LocalStore, line: bytes, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        store.append_line(KEY, line)
    assert not store.exists(KEY)


def test_a_failed_write_leaves_the_object_as_it_was(
    store: LocalStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.append_line(KEY, b"first")
    real_write = os.write

    def write_part_then_fill_the_disk(descriptor: int, data: bytes) -> int:
        real_write(descriptor, data[:3])
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(os, "write", write_part_then_fill_the_disk)

    with pytest.raises(StorageError, match=r"cannot append to .*No space left on device"):
        store.append_line(KEY, b"second")
    monkeypatch.undo()
    assert store.get(KEY) == b"first\n"


def test_appends_from_many_threads_keep_every_line_whole(store: LocalStore) -> None:
    def append_many(thread: int) -> None:
        for number in range(200):
            store.append_line(KEY, f"{thread}-{number}".encode())

    threads = [threading.Thread(target=append_many, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    lines = store.get(KEY).splitlines()
    assert sorted(lines) == sorted(f"{t}-{n}".encode() for t in range(8) for n in range(200))


def test_an_invalid_key_is_refused(store: LocalStore) -> None:
    with pytest.raises(InvalidKeyError):
        store.append_line("../outside", b"x")


def test_appending_under_a_file_raises_storage_error(store: LocalStore) -> None:
    store.put("a", b"x")

    with pytest.raises(StorageError, match="parent of it is a file"):
        store.append_line("a/b", b"y")


def test_a_torn_line_can_be_dropped_without_appending(store: LocalStore, tmp_path: Path) -> None:
    store.append_line(KEY, b"a")
    with (tmp_path / KEY).open("ab") as file:
        file.write(b"half a li")

    assert store.drop_torn_line(KEY) is True
    assert store.get(KEY) == b"a\n"
    assert store.drop_torn_line(KEY) is False


def test_dropping_a_torn_line_of_a_missing_object_does_nothing(store: LocalStore) -> None:
    assert store.drop_torn_line(KEY) is False
    assert not store.exists(KEY)
