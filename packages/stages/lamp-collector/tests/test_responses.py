import json
from datetime import UTC, datetime

import pytest
from google.transit import gtfs_realtime_pb2
from lamp_collector.feeds import (
    Feed,
    FeedRequest,
    bus_patterns,
    bus_routes,
    bus_vehicles,
    gtfs_realtime,
    train_positions,
)
from lamp_collector.fetch import Failure, FetchResult
from lamp_collector.responses import Reading, Status, read_response

NOW = datetime(2026, 11, 23, 15, 0, 0, tzinfo=UTC)
NO_DATA = {"rt": "N5", "msg": "No data found for parameter"}


def read(request: FeedRequest, body: bytes) -> Reading:
    return read_response(FetchResult(request, NOW, NOW, http_status=200, body=body))


def bustime(content: dict[str, object]) -> bytes:
    return json.dumps({"bustime-response": content}).encode()


def ctatt(content: dict[str, object]) -> bytes:
    return json.dumps({"ctatt": {"tmst": "2026-11-23T23:00:29", **content}}).encode()


def gtfs_feed(vehicles: int, alerts: int = 0) -> bytes:
    message = gtfs_realtime_pb2.FeedMessage()
    message.header.gtfs_realtime_version = "2.0"
    for number in range(vehicles):
        entity = message.entity.add(id=f"v{number}")
        entity.vehicle.vehicle.id = str(number)
    for number in range(alerts):
        entity = message.entity.add(id=f"a{number}")
        entity.alert.header_text.translation.add(text="Detour")
    serialized: bytes = message.SerializeToString()
    return serialized


@pytest.mark.parametrize(
    ("failure", "status"),
    [(Failure.TIMEOUT, Status.TIMEOUT), (Failure.CONNECTION, Status.CONNECTION_ERROR)],
)
def test_a_call_without_a_response_keeps_its_failure(failure: Failure, status: Status) -> None:
    result = FetchResult(bus_routes(), NOW, failure=failure, error="ReadTimeout: gave up")

    assert read_response(result) == Reading(status, error="ReadTimeout: gave up")


def test_a_status_other_than_200_is_an_http_error() -> None:
    result = FetchResult(bus_routes(), NOW, NOW, http_status=503, body=b"busy")

    assert read_response(result) == Reading(Status.HTTP_ERROR, error="HTTP status 503")


def test_bus_vehicles_count_and_list_their_patterns() -> None:
    body = bustime(
        {
            "vehicle": [{"vid": "1", "pid": 30}, {"vid": "2", "pid": 4}, {"vid": "3", "pid": 30}],
            "error": [NO_DATA],
        }
    )

    assert read(bus_vehicles(["3", "4", "N5"]), body) == Reading(
        Status.OK, vehicle_count=3, pattern_ids=("30", "4")
    )


def test_routes_with_no_buses_are_still_ok() -> None:
    body = bustime({"error": [NO_DATA, {"rt": "X4", "msg": "No data found for parameter"}]})

    assert read(bus_vehicles(["N5", "X4"]), body) == Reading(Status.OK, vehicle_count=0)


def test_a_single_error_object_is_read_like_a_list_of_one() -> None:
    assert read(bus_vehicles(["N5"]), bustime({"error": NO_DATA})).status is Status.OK


def test_any_other_bus_tracker_error_is_an_api_error() -> None:
    body = bustime(
        {
            "vehicle": [{"vid": "1", "pid": 30}],
            "error": [{"msg": "Invalid API access key supplied"}],
        }
    )

    assert read(bus_vehicles(["3"]), body) == Reading(
        Status.API_ERROR, error="Invalid API access key supplied"
    )


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (b"<html>", "not JSON"),
        (b'{"other": {}}', "bustime-response must be an object, got None"),
        (
            bustime({"vehicle": [{"vid": "1", "pid": "30"}]}),
            "vehicle.pid must be an integer, got '30'",
        ),
        (bustime({"vehicle": ["bus"]}), "vehicle entry must be an object, got 'bus'"),
        (bustime({"error": [{"rt": "1"}]}), "error.msg must be text, got None"),
    ],
)
def test_an_unreadable_bus_response_is_a_parse_error_naming_the_field(
    body: bytes, message: str
) -> None:
    reading = read(bus_vehicles(["3"]), body)

    assert reading.status is Status.PARSE_ERROR
    assert reading.error is not None
    assert message in reading.error


def test_bus_routes_list_their_ids() -> None:
    body = bustime(
        {"routes": [{"rt": "1", "rtnm": "Bronzeville"}, {"rt": "X9", "rtnm": "Ashland"}]}
    )

    assert read(bus_routes(), body) == Reading(Status.OK, route_ids=("1", "X9"))


def test_an_empty_route_list_is_a_parse_error() -> None:
    reading = read(bus_routes(), bustime({"routes": []}))

    assert reading == Reading(Status.PARSE_ERROR, error="getroutes listed no routes")


def test_bus_patterns_list_the_patterns_they_define() -> None:
    body = bustime({"ptr": [{"pid": 3936, "pt": []}, {"pid": 3932, "pt": []}]})

    assert read(bus_patterns(["3936", "3932"]), body) == Reading(
        Status.OK, pattern_ids=("3932", "3936")
    )


def test_train_positions_count_lists_single_trains_and_empty_lines() -> None:
    body = ctatt(
        {
            "errCd": "0",
            "errNm": None,
            "route": [
                {"@name": "red", "train": [{"rn": "828"}, {"rn": "829"}]},
                {"@name": "p", "train": {"rn": "512"}},
                {"@name": "y"},
            ],
        }
    )

    assert read(train_positions(), body) == Reading(Status.OK, vehicle_count=3)


def test_a_train_tracker_error_code_is_an_api_error() -> None:
    body = ctatt({"errCd": "101", "errNm": "Invalid API key."})

    assert read(train_positions(), body) == Reading(
        Status.API_ERROR, error="errCd 101: Invalid API key."
    )


def test_a_train_response_without_ctatt_is_a_parse_error() -> None:
    reading = read(train_positions(), b'{"route": []}')

    assert reading == Reading(Status.PARSE_ERROR, error="ctatt must be an object, got None")


def test_gtfs_realtime_vehicle_positions_count_vehicle_entities() -> None:
    reading = read(gtfs_realtime(Feed.GTFSRT_VEHICLE_POSITIONS), gtfs_feed(vehicles=2, alerts=1))

    assert reading == Reading(Status.OK, vehicle_count=2)


def test_other_gtfs_realtime_feeds_have_no_vehicle_count() -> None:
    reading = read(gtfs_realtime(Feed.GTFSRT_SERVICE_ALERTS), gtfs_feed(vehicles=0, alerts=2))

    assert reading == Reading(Status.OK)


def test_a_gtfs_realtime_xml_error_is_an_api_error() -> None:
    body = (
        b'<?xml version="1.0" encoding="utf-16"?><ServiceAlerts><tmst>20261123 23:00:29</tmst>'
        b"<errCd>101</errCd><errNm>Invalid API key.</errNm></ServiceAlerts>"
    )

    reading = read(gtfs_realtime(Feed.GTFSRT_SERVICE_ALERTS), body)

    assert reading == Reading(Status.API_ERROR, error="errCd 101: Invalid API key.")


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (b"", "has no header"),
        (b"\xff\xff\xff", "not a GTFS-Realtime message"),
        (b"<html>busy</html>", "XML without an error code"),
    ],
)
def test_an_unreadable_gtfs_realtime_body_is_a_parse_error(body: bytes, message: str) -> None:
    reading = read(gtfs_realtime(Feed.GTFSRT_TRIP_UPDATES), body)

    assert reading.status is Status.PARSE_ERROR
    assert reading.error is not None
    assert message in reading.error
