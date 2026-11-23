from collections.abc import Iterator
from typing import Protocol


class Store(Protocol):
    """A flat namespace of byte objects, addressed by validated keys.

    Every method raises InvalidKeyError for a key that breaks the key rules, and
    StorageError when the backend fails.
    """

    def put(self, key: str, data: bytes) -> None:
        """Write data under key, replacing any object already there."""
        ...

    def put_new(self, key: str, data: bytes) -> bool:
        """Write data under key only if nothing is there yet.

        Return True when this call wrote the object, and False when an identical
        object was already there, so a retried write is safe. Raise
        ObjectConflictError when a different object is already there.
        """
        ...

    def get(self, key: str) -> bytes:
        """Return the object under key, or raise ObjectNotFoundError."""
        ...

    def exists(self, key: str) -> bool: ...

    def list_keys(self, prefix: str = "") -> Iterator[str]:
        """Yield every key that starts with prefix, in sorted order."""
        ...

    def delete(self, key: str) -> None:
        """Remove the object under key. Removing a missing key does nothing."""
        ...
