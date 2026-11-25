"""Log setup for the collector, with every API key removed from every line.

httpx logs each request's full URL, key included, so its request log is
switched off. Every line, its arguments and any traceback also pass through
key removal as they are written, as a backstop for anything else that might
print a key.
"""

import logging
import sys
from typing import TextIO

from .keys import ApiKeys

FORMAT = "%(asctime)s %(levelname)s %(threadName)s %(name)s: %(message)s"

# Libraries that log request URLs below WARNING.
_URL_LOGGERS = ("httpx", "httpcore")


class RedactingFormatter(logging.Formatter):
    def __init__(self, keys: ApiKeys) -> None:
        super().__init__(FORMAT)
        self._keys = keys

    def format(self, record: logging.LogRecord) -> str:
        return self._keys.redact(super().format(record))


def configure_logging(keys: ApiKeys, stream: TextIO = sys.stderr) -> None:
    """Send INFO and above to stream, replacing any handlers set up before."""
    handler = logging.StreamHandler(stream)
    handler.setFormatter(RedactingFormatter(keys))
    root = logging.getLogger()
    for old in list(root.handlers):
        root.removeHandler(old)
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    for name in _URL_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
