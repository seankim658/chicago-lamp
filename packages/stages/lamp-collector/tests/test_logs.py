import io
import logging
from collections.abc import Iterator

import httpx
import pytest
from lamp_collector.feeds import train_positions
from lamp_collector.fetch import Fetcher
from lamp_collector.keys import REDACTED, ApiKeys
from lamp_collector.logs import configure_logging

KEYS = ApiKeys(
    bus="Bus0Key0Bus0Key0Bus0Key0B",
    train="train0key0train0key0train0key012",
    gtfsrt="Gtfs0Key0Gtfs0Key0Gtfs0",
)


@pytest.fixture
def output() -> Iterator[io.StringIO]:
    """Log to a string for the test, then put the root logger back as it was."""
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    saved_url_levels = {name: logging.getLogger(name).level for name in ("httpx", "httpcore")}
    stream = io.StringIO()
    configure_logging(KEYS, stream)
    yield stream
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)
    for name, level in saved_url_levels.items():
        logging.getLogger(name).setLevel(level)


def assert_no_key(text: str) -> None:
    for key in (KEYS.bus, KEYS.train, KEYS.gtfsrt):
        assert str(key) not in text


def test_info_lines_are_written_with_level_and_logger(output: io.StringIO) -> None:
    logging.getLogger("lamp_collector.test").info("loaded %d bus routes", 125)

    assert "INFO MainThread lamp_collector.test: loaded 125 bus routes" in output.getvalue()


def test_debug_lines_are_not_written(output: io.StringIO) -> None:
    logging.getLogger("lamp_collector.test").debug("noise")

    assert output.getvalue() == ""


def test_a_key_in_a_message_or_its_arguments_is_removed(output: io.StringIO) -> None:
    logger = logging.getLogger("lamp_collector.test")

    logger.warning(f"message holds {KEYS.bus}")
    logger.warning("argument holds %s", KEYS.train)

    assert_no_key(output.getvalue())
    assert output.getvalue().count(REDACTED) == 2


def test_a_key_in_a_traceback_is_removed(output: io.StringIO) -> None:
    try:
        raise RuntimeError(f"failed calling ?key={KEYS.gtfsrt}")
    except RuntimeError:
        logging.getLogger("lamp_collector.test").exception("poll failed")

    assert "RuntimeError: failed calling ?key=REDACTED" in output.getvalue()
    assert_no_key(output.getvalue())


def test_a_real_call_through_httpx_logs_no_url(output: io.StringIO) -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200)))

    Fetcher(client, KEYS).fetch(train_positions())

    assert "HTTP Request" not in output.getvalue()
    assert_no_key(output.getvalue())
