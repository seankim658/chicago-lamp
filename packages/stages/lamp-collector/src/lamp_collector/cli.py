"""The lamp-collector command."""

import argparse
import logging
import signal
import sys
from collections.abc import Callable, Sequence
from types import FrameType

import httpx
from lamp_storage import LocalStore
from pydantic import ValidationError

from .collector import Collector, Services
from .fetch import Fetcher, cta_client
from .journal import JournalWriter
from .logs import configure_logging
from .poll import PollRunner, Recorder
from .raw import RawWriter
from .schedule import Pacer
from .settings import CollectorSettings

logger = logging.getLogger(__name__)

EXIT_FEED_DIED = 1
EXIT_BAD_SETTINGS = 2


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="lamp-collector", description="Collect CTA's real-time feeds."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "run",
        help="Poll every feed until stopped, saving each response and its journal row "
        "to LAMP_BUFFER_DIR.",
    )
    parser.parse_args(argv)
    return run()


def run() -> int:
    """Run the collector until SIGINT or SIGTERM, reading settings from the environment and .env."""
    try:
        settings = CollectorSettings()
    except ValidationError as error:
        sys.stderr.write(f"lamp-collector: the settings are not valid\n{error}\n")
        return EXIT_BAD_SETTINGS
    configure_logging(settings.api_keys())
    with cta_client() as client:
        collector = build_collector(settings, client)
        stop_on_signals(collector.stop)
        healthy = collector.run()
    logger.info("stopped")
    return 0 if healthy else EXIT_FEED_DIED


def build_collector(settings: CollectorSettings, client: httpx.Client) -> Collector:
    keys = settings.api_keys()
    store = LocalStore(settings.lamp_buffer_dir)
    pacer = Pacer()
    recorder = Recorder(RawWriter(store), JournalWriter(store))
    runner = PollRunner(Fetcher(client, keys), recorder)
    return Collector(Services(store, runner, pacer), keys)


def stop_on_signals(stop: Callable[[], None]) -> None:
    def handle(signal_number: int, frame: FrameType | None) -> None:
        logger.info("stopping on %s", signal.Signals(signal_number).name)
        stop()

    for signal_number in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signal_number, handle)
