"""Where each CTA feed lives and how one call to it is built.

A request never holds a key. The key is added only when the URL to send is built,
so the URL kept for records and logs cannot carry one.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlencode

from .keys import ApiKeys, KeyKind

BUS_TRACKER_URL = "https://www.ctabustracker.com/bustime/api/v3"
TRAIN_TRACKER_URL = "https://lapi.transitchicago.com/api/1.0"
GTFS_REALTIME_URL = "https://transitdata.transitchicago.com/GtfsRealtime"

# Train Tracker's route codes for the eight 'L' lines.
TRAIN_LINES = ("Red", "Blue", "Brn", "G", "Org", "P", "Pink", "Y")

# Bus Tracker refuses more than ten routes or patterns in one call.
MAX_IDS_PER_CALL = 10


class InvalidRequestError(ValueError):
    """A request was asked for with arguments CTA would refuse."""


class Feed(StrEnum):
    """A feed the collector polls.

    Each value names the feed's folder in storage, so it never changes.
    """

    BUS_VEHICLES = "bus_vehicles"
    BUS_ROUTES = "bus_routes"
    BUS_PATTERNS = "bus_patterns"
    TRAIN_POSITIONS = "train_positions"
    GTFSRT_VEHICLE_POSITIONS = "gtfsrt_vehicle_positions"
    GTFSRT_TRIP_UPDATES = "gtfsrt_trip_updates"
    GTFSRT_SERVICE_ALERTS = "gtfsrt_service_alerts"

    @property
    def key_kind(self) -> KeyKind:
        if self in _BUS_TRACKER_FEEDS:
            return KeyKind.BUS
        if self in _GTFS_REALTIME_FILES:
            return KeyKind.GTFSRT
        return KeyKind.TRAIN

    @property
    def extension(self) -> str:
        """The file extension of the feed's raw responses."""
        if self in _GTFS_REALTIME_FILES:
            return "pb"
        return "json"


_BUS_TRACKER_FEEDS = frozenset({Feed.BUS_VEHICLES, Feed.BUS_ROUTES, Feed.BUS_PATTERNS})

_GTFS_REALTIME_FILES = {
    Feed.GTFSRT_VEHICLE_POSITIONS: "VehiclePositions.pb",
    Feed.GTFSRT_TRIP_UPDATES: "TripUpdates.pb",
    Feed.GTFSRT_SERVICE_ALERTS: "ServiceAlerts.pb",
}


@dataclass(frozen=True)
class FeedRequest:
    """One call to one feed, without its key."""

    feed: Feed
    endpoint: str
    params: tuple[tuple[str, str], ...] = ()

    @property
    def public_url(self) -> str:
        """The URL without the key, safe to store and log."""
        return _with_query(self.endpoint, self.params)

    def url(self, keys: ApiKeys) -> str:
        """The URL to send, with the feed's key. Never store or log it."""
        key = keys.for_kind(self.feed.key_kind)
        return _with_query(self.endpoint, (("key", key), *self.params))


def bus_vehicles(routes: Sequence[str]) -> FeedRequest:
    """Ask Bus Tracker for every vehicle on up to ten routes, with times to the second."""
    params = (("rt", _id_list("route", routes)), ("tmres", "s"), ("format", "json"))
    return FeedRequest(Feed.BUS_VEHICLES, f"{BUS_TRACKER_URL}/getvehicles", params)


def bus_routes() -> FeedRequest:
    return FeedRequest(Feed.BUS_ROUTES, f"{BUS_TRACKER_URL}/getroutes", (("format", "json"),))


def bus_patterns(pattern_ids: Sequence[str]) -> FeedRequest:
    """Ask Bus Tracker for the stops and distances of up to ten patterns."""
    params = (("pid", _id_list("pattern", pattern_ids)), ("format", "json"))
    return FeedRequest(Feed.BUS_PATTERNS, f"{BUS_TRACKER_URL}/getpatterns", params)


def train_positions(lines: Sequence[str] = TRAIN_LINES) -> FeedRequest:
    """Ask Train Tracker for the position of every train on the given lines."""
    if not lines:
        raise InvalidRequestError("train positions need at least one line")
    for line in lines:
        if line not in TRAIN_LINES:
            raise InvalidRequestError(
                f"{line!r} is not a Train Tracker line, use one of {TRAIN_LINES}"
            )
    params = (("rt", ",".join(lines)), ("outputType", "JSON"))
    return FeedRequest(Feed.TRAIN_POSITIONS, f"{TRAIN_TRACKER_URL}/ttpositions.aspx", params)


def gtfs_realtime(feed: Feed) -> FeedRequest:
    """Ask for one of CTA's GTFS-Realtime feeds, which take no parameters but the key."""
    file = _GTFS_REALTIME_FILES.get(feed)
    if file is None:
        raise InvalidRequestError(f"{feed.value} is not a GTFS-Realtime feed")
    return FeedRequest(feed, f"{GTFS_REALTIME_URL}/{file}")


def _id_list(kind: str, ids: Sequence[str]) -> str:
    """Join route or pattern IDs into the comma list Bus Tracker expects."""
    if not 1 <= len(ids) <= MAX_IDS_PER_CALL:
        raise InvalidRequestError(
            f"Bus Tracker takes 1 to {MAX_IDS_PER_CALL} {kind}s in one call, got {len(ids)}"
        )
    for identifier in ids:
        if not (identifier.isascii() and identifier.isalnum()):
            raise InvalidRequestError(
                f"a {kind} ID must be letters and digits only, got {identifier!r}"
            )
    return ",".join(ids)


def _with_query(endpoint: str, params: tuple[tuple[str, str], ...]) -> str:
    if not params:
        return endpoint
    return f"{endpoint}?{urlencode(params, safe=',')}"
