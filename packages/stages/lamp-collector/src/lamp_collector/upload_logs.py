"""Log setup for the uploader, with its object storage keys removed from every line.

botocore logs requests only below INFO, but every line, its arguments and any
traceback still pass through key removal, as a backstop.
"""

import logging
import sys
from collections.abc import Sequence
from typing import TextIO

from .keys import REDACTED
from .logs import FORMAT


class _SecretRemovingFormatter(logging.Formatter):
    def __init__(self, secrets: Sequence[str]) -> None:
        super().__init__(FORMAT)
        self._secrets = sorted((secret for secret in secrets if secret), key=len, reverse=True)

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        for secret in self._secrets:
            text = text.replace(secret, REDACTED)
        return text


def configure_upload_logging(secrets: Sequence[str], stream: TextIO = sys.stderr) -> None:
    """Send INFO and above to stream, replacing any handlers set up before."""
    handler = logging.StreamHandler(stream)
    handler.setFormatter(_SecretRemovingFormatter(secrets))
    root = logging.getLogger()
    for old in list(root.handlers):
        root.removeHandler(old)
    root.addHandler(handler)
    root.setLevel(logging.INFO)
