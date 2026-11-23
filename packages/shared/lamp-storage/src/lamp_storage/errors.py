class StorageError(Exception):
    """A store could not complete an operation."""


class InvalidKeyError(StorageError):
    """A key or prefix breaks the rules every store applies to object names."""


class ObjectNotFoundError(StorageError):
    """No object exists under the requested key."""


class ObjectConflictError(StorageError):
    """A create-only write found a different object already under its key."""
