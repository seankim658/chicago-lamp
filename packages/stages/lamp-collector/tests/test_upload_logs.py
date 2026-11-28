import io
import logging
from collections.abc import Iterator

import pytest
from lamp_collector.upload_logs import configure_upload_logging

STORAGE_KEY = "uploader-secret-access-key-0123456789"


@pytest.fixture
def restore_root_logger() -> Iterator[None]:
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    yield
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)


@pytest.mark.usefixtures("restore_root_logger")
def test_every_secret_is_removed_from_log_lines() -> None:
    stream = io.StringIO()
    configure_upload_logging((STORAGE_KEY,), stream)

    logging.getLogger("lamp_collector.upload").error("signing with %s failed", STORAGE_KEY)

    assert STORAGE_KEY not in stream.getvalue()
    assert "signing with REDACTED failed" in stream.getvalue()


@pytest.mark.usefixtures("restore_root_logger")
def test_info_lines_are_written_and_debug_lines_are_not() -> None:
    stream = io.StringIO()
    configure_upload_logging((STORAGE_KEY,), stream)

    logging.getLogger("lamp_collector.upload").info("uploaded 3")
    logging.getLogger("botocore").debug("request headers")

    assert "uploaded 3" in stream.getvalue()
    assert "request headers" not in stream.getvalue()
