"""Record a sample of every CTA feed into a zip of test fixtures.

Every feed is called once, getpatterns for every pattern a bus reports, and
ttpositions with its lines in three forms. Then one batch of bus routes and all
train lines are polled every --interval seconds for --minutes, to show how
often positions change. The zip holds each body as CTA returned it and
calls.jsonl with one line per call. Nothing is written if a key appears in it.

Keys come from the environment or .env. GTFS-Realtime is skipped when
CTA_GTFSRT_KEY is not set.
"""

import argparse
import json
import sys
import time
import zipfile
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from lamp_collector.feeds import (
    MAX_IDS_PER_CALL,
    TRAIN_LINES,
    Feed,
    FeedRequest,
    bus_patterns,
    bus_routes,
    bus_vehicles,
    gtfs_realtime,
    train_positions,
)
from lamp_collector.fetch import Fetcher, FetchResult, cta_client
from lamp_collector.keys import ApiKeys, KeyKind
from lamp_collector.settings import Settings
from pydantic import ValidationError

INDEX_NAME = "calls.jsonl"
GTFS_REALTIME_FEEDS = (
    Feed.GTFSRT_VEHICLE_POSITIONS,
    Feed.GTFSRT_TRIP_UPDATES,
    Feed.GTFSRT_SERVICE_ALERTS,
)

# A zip member's name and its bytes.
Member = tuple[str, bytes]


class CaptureError(Exception):
    """A response the capture depends on was missing or unreadable."""


@dataclass(frozen=True)
class Call:
    label: str
    result: FetchResult


class Capture:
    """Fetch requests and keep every result, in order."""

    def __init__(self, fetcher: Fetcher, include_gtfsrt: bool = True) -> None:
        self._fetcher = fetcher
        self.include_gtfsrt = include_gtfsrt
        self.calls: list[Call] = []

    def fetch(self, request: FeedRequest, label: str) -> FetchResult:
        result = self._fetcher.fetch(request)
        self.calls.append(Call(label, result))
        return result


class Pacer:
    """Yield once per interval, timed from the first tick, so slow calls don't drift."""

    def __init__(
        self,
        interval_seconds: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._interval = interval_seconds
        self._clock = clock
        self._sleep = sleep

    def ticks(self, count: int) -> Iterator[None]:
        start = self._clock()
        for tick in range(count):
            delay = start + tick * self._interval - self._clock()
            if delay > 0:
                self._sleep(delay)
            yield


def capture_once(capture: Capture) -> list[str]:
    """Call every feed once and return the first batch of route IDs."""
    routes = route_ids(capture.fetch(bus_routes(), "all routes"))
    vehicles = [capture.fetch(bus_vehicles(batch), "all routes") for batch in batches(routes)]
    for batch in batches(pattern_ids(vehicles)):
        capture.fetch(bus_patterns(batch), "every pattern seen")
    capture_train_forms(capture)
    if capture.include_gtfsrt:
        for feed in GTFS_REALTIME_FEEDS:
            capture.fetch(gtfs_realtime(feed), "once")
    return routes[:MAX_IDS_PER_CALL]


def capture_train_forms(capture: Capture) -> None:
    """Try the forms of a multi-line ttpositions call that CTA might accept."""
    capture.fetch(train_positions(), "all lines, comma separated")
    repeated = (*(("rt", line) for line in TRAIN_LINES), ("outputType", "JSON"))
    capture.fetch(replace(train_positions(), params=repeated), "all lines, repeated rt")
    capture.fetch(train_positions(["Red"]), "one line")


def capture_rates(capture: Capture, routes: Sequence[str], ticks: Iterator[None]) -> None:
    for _ in ticks:
        capture.fetch(bus_vehicles(routes), "rate sample")
        capture.fetch(train_positions(), "rate sample")


def batches(ids: Sequence[str]) -> list[list[str]]:
    return [
        list(ids[start : start + MAX_IDS_PER_CALL])
        for start in range(0, len(ids), MAX_IDS_PER_CALL)
    ]


def route_ids(result: FetchResult) -> list[str]:
    """Read the route IDs from a getroutes response."""
    routes = _field(_bustime_response(result), "routes")
    if not isinstance(routes, list) or not routes:
        raise CaptureError(f"getroutes listed no routes, got routes={routes!r}")
    ids: list[str] = []
    for route in routes:
        route_id = _field(route, "rt")
        if not isinstance(route_id, str):
            raise CaptureError(f"getroutes gave a route without a text rt: {route!r}")
        ids.append(route_id)
    return ids


def pattern_ids(results: Sequence[FetchResult]) -> list[str]:
    """Return every distinct pattern ID the vehicles in these responses report."""
    found: set[str] = set()
    for result in results:
        try:
            vehicles = _field(_bustime_response(result), "vehicle")
        except CaptureError:
            continue
        if not isinstance(vehicles, list):
            continue
        for vehicle in vehicles:
            pattern = _field(vehicle, "pid")
            if pattern is not None:
                found.add(str(pattern))
    return sorted(found)


def _bustime_response(result: FetchResult) -> object:
    if result.http_status != 200 or result.body is None:
        raise CaptureError(
            f"{result.request.public_url} got status {result.http_status}, error {result.error}"
        )
    try:
        document: object = json.loads(result.body)
    except ValueError as error:
        raise CaptureError(f"{result.request.public_url} returned invalid JSON: {error}") from None
    return _field(document, "bustime-response")


def _field(document: object, name: str) -> object:
    if not isinstance(document, dict):
        return None
    return document.get(name)


def fixture_members(calls: Sequence[Call]) -> list[Member]:
    """Return each body as feed/NNNN.ext, then calls.jsonl describing every call."""
    members: list[Member] = []
    lines: list[str] = []
    for number, call in enumerate(calls):
        result = call.result
        name = None
        if result.body is not None:
            name = f"{result.request.feed.value}/{number:04d}.{result.request.feed.extension}"
            members.append((name, result.body))
        lines.append(json.dumps(_index_entry(name, call)))
    members.append((INDEX_NAME, "".join(f"{line}\n" for line in lines).encode()))
    return members


def _index_entry(name: str | None, call: Call) -> dict[str, object]:
    result = call.result
    entry: dict[str, object] = {
        "file": name,
        "feed": result.request.feed.value,
        "label": call.label,
        "url": result.request.public_url,
        "request_time_utc": result.request_time_utc.isoformat(),
        "response_time_utc": None,
        "http_status": result.http_status,
        "failure": None if result.failure is None else result.failure.value,
        "error": result.error,
    }
    if result.response_time_utc is not None:
        entry["response_time_utc"] = result.response_time_utc.isoformat()
    return entry


def leaked_keys(members: Sequence[Member], keys: ApiKeys) -> list[str]:
    """Return the names of members that hold either key."""
    secrets = {keys.bus.encode(), keys.train.encode()}
    return [name for name, data in members if any(secret in data for secret in secrets)]


def write_zip(path: Path, members: Sequence[Member]) -> None:
    partial = path.with_name(f".{path.name}.partial")
    with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in members:
            archive.writestr(name, data)
    partial.replace(path)


def summary(calls: Sequence[Call]) -> list[str]:
    """One line per feed with its call count and how many got no HTTP 200."""
    total = Counter(call.result.request.feed.value for call in calls)
    failed = Counter(
        call.result.request.feed.value for call in calls if call.result.http_status != 200
    )
    return [f"{feed}: {count} calls, {failed[feed]} not HTTP 200" for feed, count in total.items()]


def run(capture: Capture, minutes: float, interval_seconds: float) -> int:
    """Capture until done or interrupted, and return the exit status."""
    try:
        routes = capture_once(capture)
        rounds = int(minutes * 60 / interval_seconds)
        sys.stdout.write(f"sampling rates for {minutes:g} minutes\n")
        capture_rates(capture, routes, Pacer(interval_seconds).ticks(rounds))
    except CaptureError as error:
        sys.stderr.write(f"capture stopped early: {error}\n")
        return 1
    except KeyboardInterrupt:
        sys.stderr.write("capture interrupted, keeping what was recorded\n")
        return 130
    return 0


def parse_arguments(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("output", type=Path, help="zip file to write, which must not exist")
    parser.add_argument("--minutes", type=float, default=10, help="rate sampling time")
    parser.add_argument("--interval", type=float, default=15, help="seconds between samples")
    return parser.parse_args(argv)


def main(argv: Sequence[str]) -> int:
    arguments = parse_arguments(argv)
    if arguments.output.exists():
        sys.stderr.write(f"{arguments.output} already exists\n")
        return 2
    try:
        keys = Settings().api_keys()
    except ValidationError as error:
        sys.stderr.write(f"{error}\n")
        return 2
    include_gtfsrt = keys.has(KeyKind.GTFSRT)
    if not include_gtfsrt:
        sys.stdout.write("skipping GTFS-Realtime, CTA_GTFSRT_KEY is not set\n")
    with cta_client() as client:
        capture = Capture(Fetcher(client, keys), include_gtfsrt)
        status = run(capture, arguments.minutes, arguments.interval)
    members = fixture_members(capture.calls)
    leaks = leaked_keys(members, keys)
    if leaks:
        sys.stderr.write(f"a key appears in {', '.join(leaks)}, so nothing was written\n")
        return 1
    write_zip(arguments.output, members)
    for line in summary(capture.calls):
        sys.stdout.write(f"{line}\n")
    sys.stdout.write(f"wrote {len(capture.calls)} calls to {arguments.output}\n")
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
