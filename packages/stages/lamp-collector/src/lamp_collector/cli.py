"""The lamp-collector command."""

import argparse
import logging
import signal
import sys
from collections.abc import Callable, Sequence
from types import FrameType

import httpx
from lamp_storage import LocalStore, S3Store, s3_client
from pydantic import ValidationError

from .collector import Collector, Services, steady_feeds
from .fetch import Fetcher, cta_client, utc_now
from .health import HealthCheck
from .heartbeat import HeartbeatError, heartbeat_client, send_heartbeat
from .journal import JournalWriter
from .logs import configure_logging
from .poll import PollRunner, Recorder
from .raw import RawWriter
from .schedule import Pacer
from .settings import CollectorSettings, HealthSettings, UploaderSettings
from .upload import Uploader
from .upload_log import UploadLog
from .upload_logs import configure_upload_logging

logger = logging.getLogger(__name__)

EXIT_FEED_DIED = 1
EXIT_UNHEALTHY = 1
EXIT_UPLOAD_FAILED = 1
EXIT_BAD_SETTINGS = 2
EXIT_NOT_REPORTED = 3


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
    commands.add_parser(
        "health",
        help="Check LAMP_BUFFER_DIR for a feed with no successful poll in 10 minutes and "
        "for under 10 GB of free disk, and report the result to LAMP_HEALTH_PING_URL "
        "when it is set. Exit 1 when a problem is found.",
    )
    commands.add_parser(
        "upload",
        help="Copy every file in LAMP_BUFFER_DIR that is due and not yet uploaded to "
        "LAMP_PRIVATE_BUCKET, once. Buffered files are kept. Exit 1 when an upload failed.",
    )
    arguments = parser.parse_args(argv)
    if arguments.command == "health":
        return health()
    if arguments.command == "upload":
        return upload()
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


def health() -> int:
    """Check the buffer once, log each problem found, and report to the ping URL if set."""
    try:
        settings = HealthSettings()
    except ValidationError as error:
        sys.stderr.write(f"lamp-collector: the settings are not valid\n{error}\n")
        return EXIT_BAD_SETTINGS
    configure_logging(settings.api_keys())
    problems = check_buffer(settings)
    url = settings.lamp_health_ping_url
    if url is not None and not report(url.get_secret_value(), problems):
        return EXIT_NOT_REPORTED
    return EXIT_UNHEALTHY if problems else 0


def upload() -> int:
    """Make one upload pass, reading settings from the environment and .env."""
    try:
        settings = UploaderSettings()
    except ValidationError as error:
        sys.stderr.write(f"lamp-collector: the settings are not valid\n{error}\n")
        return EXIT_BAD_SETTINGS
    configure_upload_logging(settings.secrets())
    report = build_uploader(settings).run_pass(utc_now())
    return 0 if report.ok else EXIT_UPLOAD_FAILED


def build_uploader(settings: UploaderSettings) -> Uploader:
    buffer = LocalStore(settings.lamp_buffer_dir)
    client = s3_client(
        settings.lamp_s3_endpoint_url, settings.credentials(), settings.aws_default_region
    )
    archive = S3Store(client, settings.lamp_private_bucket)
    return Uploader(buffer, archive, UploadLog(buffer))


def check_buffer(settings: HealthSettings) -> list[str]:
    """Run the health check now and log what it found."""
    buffer_dir = settings.lamp_buffer_dir
    check = HealthCheck(LocalStore(buffer_dir), buffer_dir, steady_feeds(settings.api_keys()))
    problems = check.problems(utc_now())
    for problem in problems:
        logger.warning("%s", problem)
    if not problems:
        logger.info("every feed polled successfully in the last 10 minutes")
    return problems


def report(url: str, problems: list[str]) -> bool:
    """Send the result to the ping URL, and return whether it arrived."""
    try:
        with heartbeat_client() as client:
            send_heartbeat(client, url, problems)
    except HeartbeatError as error:
        logger.error("the result was not reported: %s", error)
        return False
    return True


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
