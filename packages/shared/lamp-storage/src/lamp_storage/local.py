import errno
import os
import tempfile
import threading
from collections.abc import Iterator
from pathlib import Path

from .errors import ObjectConflictError, ObjectNotFoundError, StorageError
from .keys import validate_key, validate_prefix


class LocalStore:
    """A store in a local folder. Writes are atomic and survive a crash or power loss.

    Each write goes to a temporary dot-file beside its target, is flushed to disk,
    and then takes the target's name in one step, so a reader never sees half a
    file. Folders are created as needed and removed when deleting leaves them empty.

    Lines can also be appended to an object. Each line is flushed to disk before
    append_line returns. A crash during an append can leave half a line at the
    end, which the next append to that object drops, or drop_torn_line on its own.
    Appends through one store are safe from many threads. Separate stores on the
    same folder do not coordinate their appends.
    """

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._append_lock = threading.Lock()

    def put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        temporary = self._write_temporary(path, data)
        temporary.replace(path)
        _sync_folder(path.parent)

    def put_new(self, key: str, data: bytes) -> bool:
        path = self._path(key)
        temporary = self._write_temporary(path, data)
        try:
            os.link(temporary, path)
        except FileExistsError:
            return self._check_identical(key, path, data)
        finally:
            temporary.unlink()
        _sync_folder(path.parent)
        return True

    def append_line(self, key: str, line: bytes) -> None:
        """Add line and a newline to the end of the object, creating it if missing.

        Raise ValueError when line is empty or holds a newline. When the write
        fails, the object is left as it was.
        """
        _check_line(line)
        path = self._path(key)
        with self._append_lock:
            created = not path.exists()
            _make_parent(path)
            descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                self._append(descriptor, key, line)
            finally:
                os.close(descriptor)
            if created:
                _sync_folder(path.parent)

    def drop_torn_line(self, key: str) -> bool:
        """Cut an appended object back to its last whole line.

        Return True when there was half a line to drop. A missing object is left
        missing.
        """
        path = self._path(key)
        with self._append_lock:
            try:
                descriptor = os.open(path, os.O_RDWR)
            except FileNotFoundError:
                return False
            try:
                size = os.fstat(descriptor).st_size
                dropped = _drop_torn_line(descriptor) < size
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        return dropped

    def get(self, key: str) -> bytes:
        path = self._path(key)
        try:
            return path.read_bytes()
        except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
            raise ObjectNotFoundError(f"no object {key!r} in {self._root}") from None

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def list_keys(self, prefix: str = "") -> Iterator[str]:
        validate_prefix(prefix)
        keys: list[str] = []
        for folder, subfolders, files in self._root.walk():
            subfolders[:] = [name for name in subfolders if not name.startswith(".")]
            for name in files:
                key = (folder / name).relative_to(self._root).as_posix()
                if not name.startswith(".") and key.startswith(prefix):
                    keys.append(key)
        return iter(sorted(keys))

    def delete(self, key: str) -> None:
        path = self._path(key)
        path.unlink(missing_ok=True)
        self._remove_empty_folders(path.parent)

    def _path(self, key: str) -> Path:
        return self._root.joinpath(*validate_key(key).split("/"))

    def _append(self, descriptor: int, key: str, line: bytes) -> None:
        size = _drop_torn_line(descriptor)
        try:
            _write_all(descriptor, line + b"\n")
            os.fsync(descriptor)
        except OSError as error:
            os.ftruncate(descriptor, size)
            raise StorageError(f"cannot append to {key!r} in {self._root}: {error}") from error

    def _write_temporary(self, path: Path, data: bytes) -> Path:
        _make_parent(path)
        descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
        with os.fdopen(descriptor, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        return Path(name)

    def _check_identical(self, key: str, path: Path, data: bytes) -> bool:
        if path.read_bytes() == data:
            return False
        raise ObjectConflictError(f"a different object already exists at {key!r} in {self._root}")

    def _remove_empty_folders(self, folder: Path) -> None:
        while folder != self._root:
            try:
                folder.rmdir()
            except FileNotFoundError:
                pass
            except OSError as error:
                if error.errno == errno.ENOTEMPTY:
                    return
                raise
            folder = folder.parent


def _make_parent(path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except (FileExistsError, NotADirectoryError):
        raise StorageError(f"cannot write {path}, a parent of it is a file") from None


def _check_line(line: bytes) -> None:
    if not line:
        raise ValueError("an appended line must not be empty")
    if b"\n" in line:
        raise ValueError(f"an appended line must not hold a newline, got {line[:80]!r}")


def _drop_torn_line(descriptor: int) -> int:
    """Cut the file back to its last whole line, and return its size."""
    size = os.fstat(descriptor).st_size
    if size == 0 or os.pread(descriptor, 1, size - 1) == b"\n":
        return size
    whole = os.pread(descriptor, size, 0).rfind(b"\n") + 1
    os.ftruncate(descriptor, whole)
    return whole


def _write_all(descriptor: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(descriptor, view) :]


def _sync_folder(folder: Path) -> None:
    """Flush a folder's entries, so a rename into it survives power loss."""
    descriptor = os.open(folder, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
