"""Rules for object keys, so a key means the same thing in every store.

A key is a relative path of `/`-separated segments. No segment may be empty, be
`.` or `..`, start with a dot, or hold a backslash. These rules stop a key from
escaping a local root or a bucket prefix, and leave dot-names free for a store's
own temporary files.
"""

from .errors import InvalidKeyError


def validate_key(key: str) -> str:
    """Return key unchanged, or raise InvalidKeyError naming the rule it breaks."""
    if not key:
        raise InvalidKeyError("a key must not be empty")
    for segment in key.split("/"):
        _check_segment(segment, key)
    return key


def validate_prefix(prefix: str) -> str:
    """Return prefix unchanged when it is empty or a valid key, with or without a trailing `/`."""
    if prefix:
        validate_key(prefix.removesuffix("/"))
    return prefix


def _check_segment(segment: str, key: str) -> None:
    if not segment:
        raise InvalidKeyError(f"key {key!r} has an empty segment or a leading or trailing '/'")
    if segment.startswith("."):
        raise InvalidKeyError(f"key {key!r} has a segment starting with '.': {segment!r}")
    if "\\" in segment:
        raise InvalidKeyError(f"key {key!r} holds a backslash")
