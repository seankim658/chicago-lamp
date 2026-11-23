import errno
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

from .errors import ObjectConflictError, ObjectNotFoundError, StorageError
from .keys import validate_key, validate_prefix


class LocalStore:
    """A store in a local folder. Writes are atomic and survive a crash or power loss.

    Each write goes to a temporary dot-file beside its target, is flushed to disk,
    and then takes the target's name in one step, so a reader never sees half a
    file. Folders are created as needed and removed when deleting leaves them empty.
    """

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

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

    def _write_temporary(self, path: Path, data: bytes) -> Path:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except (FileExistsError, NotADirectoryError):
            raise StorageError(f"cannot write {path}, a parent of it is a file") from None
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


def _sync_folder(folder: Path) -> None:
    """Flush a folder's entries, so a rename into it survives power loss."""
    descriptor = os.open(folder, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
