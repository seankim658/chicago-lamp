from urllib.parse import parse_qsl, urlsplit

import pytest
from lamp_collector.feeds import (
    TRAIN_LINES,
    Feed,
    FeedRequest,
    InvalidRequestError,
    bus_patterns,
    bus_routes,
    bus_vehicles,
    gtfs_realtime,
    train_positions,
)
from lamp_collector.keys import ApiKeys, KeyKind

BUS_KEY = "Bus0Key0Bus0Key0Bus0Key0B"
TRAIN_KEY = "train0key0train0key0train0key012"
GTFSRT_KEY = "gtfsrt0key0gtfsrt0key0gtfsrt0k"
KEYS = ApiKeys(bus=BUS_KEY, train=TRAIN_KEY, gtfsrt=GTFSRT_KEY)

BUS_TRACKER = "https://www.ctabustracker.com/bustime/api/v3"
GTFS_RT = "https://transitdata.transitchicago.com/GtfsRealtime"


@pytest.mark.parametrize(
    ("feed", "kind"),
    [
        (Feed.BUS_VEHICLES, KeyKind.BUS),
        (Feed.BUS_ROUTES, KeyKind.BUS),
        (Feed.BUS_PATTERNS, KeyKind.BUS),
        (Feed.TRAIN_POSITIONS, KeyKind.TRAIN),
        (Feed.GTFSRT_VEHICLE_POSITIONS, KeyKind.GTFSRT),
        (Feed.GTFSRT_TRIP_UPDATES, KeyKind.GTFSRT),
        (Feed.GTFSRT_SERVICE_ALERTS, KeyKind.GTFSRT),
    ],
)
def test_each_feed_uses_its_own_key(feed: Feed, kind: KeyKind) -> None:
    assert feed.key_kind is kind


def test_gtfs_realtime_feeds_are_protobuf_and_the_rest_json() -> None:
    extensions = {feed: feed.extension for feed in Feed}

    assert extensions == {
        Feed.BUS_VEHICLES: "json",
        Feed.BUS_ROUTES: "json",
        Feed.BUS_PATTERNS: "json",
        Feed.TRAIN_POSITIONS: "json",
        Feed.GTFSRT_VEHICLE_POSITIONS: "pb",
        Feed.GTFSRT_TRIP_UPDATES: "pb",
        Feed.GTFSRT_SERVICE_ALERTS: "pb",
    }


def test_bus_vehicles_asks_for_seconds_and_json() -> None:
    request = bus_vehicles(["1", "X9", "N5"])

    assert request.feed is Feed.BUS_VEHICLES
    assert request.public_url == f"{BUS_TRACKER}/getvehicles?rt=1,X9,N5&tmres=s&format=json"
    assert request.url(KEYS) == (
        f"{BUS_TRACKER}/getvehicles?key={BUS_KEY}&rt=1,X9,N5&tmres=s&format=json"
    )


def test_bus_routes_url() -> None:
    assert bus_routes().url(KEYS) == f"{BUS_TRACKER}/getroutes?key={BUS_KEY}&format=json"


def test_bus_patterns_url() -> None:
    request = bus_patterns(["3936", "3932"])

    assert request.url(KEYS) == f"{BUS_TRACKER}/getpatterns?key={BUS_KEY}&pid=3936,3932&format=json"


def test_train_positions_asks_for_every_line_by_default() -> None:
    request = train_positions()

    assert request.url(KEYS) == (
        "https://lapi.transitchicago.com/api/1.0/ttpositions.aspx"
        f"?key={TRAIN_KEY}&rt=Red,Blue,Brn,G,Org,P,Pink,Y&outputType=JSON"
    )
    assert TRAIN_LINES == ("Red", "Blue", "Brn", "G", "Org", "P", "Pink", "Y")


@pytest.mark.parametrize(
    ("feed", "file"),
    [
        (Feed.GTFSRT_VEHICLE_POSITIONS, "VehiclePositions.pb"),
        (Feed.GTFSRT_TRIP_UPDATES, "TripUpdates.pb"),
        (Feed.GTFSRT_SERVICE_ALERTS, "ServiceAlerts.pb"),
    ],
)
def test_gtfs_realtime_urls_use_the_gtfs_realtime_key(feed: Feed, file: str) -> None:
    assert gtfs_realtime(feed).url(KEYS) == f"{GTFS_RT}/{file}?key={GTFSRT_KEY}"


def test_gtfs_realtime_refuses_a_feed_that_is_not_gtfs_realtime() -> None:
    with pytest.raises(InvalidRequestError, match="bus_vehicles is not a GTFS-Realtime feed"):
        gtfs_realtime(Feed.BUS_VEHICLES)


@pytest.mark.parametrize(
    "feed_request",
    [
        bus_vehicles(["22"]),
        bus_routes(),
        bus_patterns(["1"]),
        train_positions(["Red"]),
        gtfs_realtime(Feed.GTFSRT_TRIP_UPDATES),
    ],
    ids=lambda feed_request: feed_request.feed.value,
)
def test_public_url_is_the_sent_url_without_its_key(feed_request: FeedRequest) -> None:
    sent = urlsplit(feed_request.url(KEYS))
    public = urlsplit(feed_request.public_url)
    sent_query = parse_qsl(sent.query)

    assert ("key", KEYS.for_kind(feed_request.feed.key_kind)) in sent_query
    assert public.path == sent.path
    assert parse_qsl(public.query) == [pair for pair in sent_query if pair[0] != "key"]


def test_more_than_ten_routes_in_one_call_is_refused() -> None:
    routes = [str(number) for number in range(11)]

    with pytest.raises(InvalidRequestError, match="1 to 10 routes in one call, got 11"):
        bus_vehicles(routes)


def test_more_than_ten_patterns_in_one_call_is_refused() -> None:
    with pytest.raises(InvalidRequestError, match="1 to 10 patterns in one call, got 11"):
        bus_patterns([str(number) for number in range(11)])


@pytest.mark.parametrize("routes", [[], [""], ["1,2"], ["1 "], ["\u00e9"]])
def test_empty_or_malformed_route_ids_are_refused(routes: list[str]) -> None:
    with pytest.raises(InvalidRequestError, match="route"):
        bus_vehicles(routes)


def test_unknown_train_line_is_refused() -> None:
    with pytest.raises(InvalidRequestError, match="'Purple' is not a Train Tracker line"):
        train_positions(["Red", "Purple"])


def test_no_train_lines_is_refused() -> None:
    with pytest.raises(InvalidRequestError, match="at least one line"):
        train_positions([])
