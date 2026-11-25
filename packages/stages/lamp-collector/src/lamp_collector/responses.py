"""Read a CTA response far enough to judge the call and steer the collector.

Each call gets a status. A valid response also gives its vehicle count and the
route or pattern IDs the collector acts on. Only the fields read here are
validated, and a parse error names the field and the value it found.
"""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from google.protobuf.message import DecodeError
from google.transit import gtfs_realtime_pb2

from .feeds import Feed
from .fetch import Failure, FetchResult

# Bus Tracker's answer for a route or pattern with nothing to report. It is a
# true answer, so it does not make a call fail.
NO_DATA_MESSAGE = "No data found for parameter"

_XML_ERROR_CODE = re.compile(rb"<errCd>([^<]*)</errCd>")
_XML_ERROR_NAME = re.compile(rb"<errNm>([^<]*)</errNm>")
_SHOWN_VALUE_LENGTH = 80


class Status(StrEnum):
    OK = "ok"
    API_ERROR = "api_error"
    HTTP_ERROR = "http_error"
    TIMEOUT = "timeout"
    CONNECTION_ERROR = "connection_error"
    PARSE_ERROR = "parse_error"


@dataclass(frozen=True)
class Reading:
    """What one call's response says.

    vehicle_count is set for feeds that report vehicles. route_ids comes from
    getroutes, and pattern_ids lists the patterns that vehicles are on, or that
    getpatterns defined.
    """

    status: Status
    error: str | None = None
    vehicle_count: int | None = None
    route_ids: tuple[str, ...] = ()
    pattern_ids: tuple[str, ...] = ()


class _ReportedError(Exception):
    """CTA answered with an error of its own."""


class _InvalidResponseError(Exception):
    """The body is not what the requested format promises."""


def read_response(result: FetchResult) -> Reading:
    if result.failure is Failure.TIMEOUT:
        return Reading(Status.TIMEOUT, error=result.error)
    if result.failure is Failure.CONNECTION:
        return Reading(Status.CONNECTION_ERROR, error=result.error)
    if result.http_status != 200 or result.body is None:
        return Reading(Status.HTTP_ERROR, error=f"HTTP status {result.http_status}")
    try:
        return _READERS[result.request.feed](result.body)
    except _ReportedError as error:
        return Reading(Status.API_ERROR, error=str(error))
    except _InvalidResponseError as error:
        return Reading(Status.PARSE_ERROR, error=str(error))


def _read_bus_vehicles(body: bytes) -> Reading:
    vehicles = _entries(_bustime_response(body), "vehicle")
    patterns = {str(_integer(vehicle, "vehicle.pid", "pid")) for vehicle in vehicles}
    return Reading(Status.OK, vehicle_count=len(vehicles), pattern_ids=tuple(sorted(patterns)))


def _read_bus_routes(body: bytes) -> Reading:
    routes = _entries(_bustime_response(body), "routes")
    if not routes:
        raise _InvalidResponseError("getroutes listed no routes")
    return Reading(Status.OK, route_ids=tuple(_text(route, "routes.rt", "rt") for route in routes))


def _read_bus_patterns(body: bytes) -> Reading:
    patterns = _entries(_bustime_response(body), "ptr")
    ids = {str(_integer(pattern, "ptr.pid", "pid")) for pattern in patterns}
    return Reading(Status.OK, pattern_ids=tuple(sorted(ids)))


def _bustime_response(body: bytes) -> dict[str, object]:
    """Return Bus Tracker's response object, raising for any error but "no data"."""
    response = _object(_json(body), "bustime-response")
    messages = [_text(error, "error.msg", "msg") for error in _entries(response, "error")]
    reported = [message for message in messages if message != NO_DATA_MESSAGE]
    if reported:
        raise _ReportedError("; ".join(reported))
    return response


def _read_train_positions(body: bytes) -> Reading:
    response = _object(_json(body), "ctatt")
    code = response.get("errCd")
    if code != "0":
        raise _ReportedError(f"errCd {code}: {response.get('errNm')}")
    trains = sum(len(_entries(route, "train")) for route in _entries(response, "route"))
    return Reading(Status.OK, vehicle_count=trains)


def _read_gtfs_realtime(count_vehicles: bool) -> Callable[[bytes], Reading]:
    def read(body: bytes) -> Reading:
        message = _feed_message(body)
        if not count_vehicles:
            return Reading(Status.OK)
        vehicles = sum(1 for entity in message.entity if entity.HasField("vehicle"))
        return Reading(Status.OK, vehicle_count=vehicles)

    return read


def _feed_message(body: bytes) -> gtfs_realtime_pb2.FeedMessage:
    """Decode a GTFS-Realtime body. CTA sends errors as XML in place of protobuf."""
    if body.lstrip().startswith(b"<"):
        raise _xml_error(body)
    try:
        message = gtfs_realtime_pb2.FeedMessage.FromString(body)
    except DecodeError as error:
        raise _InvalidResponseError(f"body is not a GTFS-Realtime message: {error}") from None
    if not message.HasField("header"):
        raise _InvalidResponseError("GTFS-Realtime message has no header")
    return message


def _xml_error(body: bytes) -> Exception:
    code = _XML_ERROR_CODE.search(body)
    if code is None:
        return _InvalidResponseError(f"body is XML without an error code: {_show(body)}")
    name = _XML_ERROR_NAME.search(body)
    detail = name.group(1).decode(errors="replace") if name else None
    return _ReportedError(f"errCd {code.group(1).decode(errors='replace')}: {detail}")


def _json(body: bytes) -> object:
    try:
        document: object = json.loads(body)
    except ValueError:
        raise _InvalidResponseError(f"body is not JSON: {_show(body)}") from None
    return document


def _object(document: object, name: str) -> dict[str, object]:
    value = document.get(name) if isinstance(document, dict) else None
    if not isinstance(value, dict):
        raise _InvalidResponseError(f"{name} must be an object, got {_show(value)}")
    return value


def _entries(parent: dict[str, object], name: str) -> list[dict[str, object]]:
    """Read a list CTA sends as a list, a lone object when it has one entry, or nothing."""
    value = parent.get(name)
    if value is None:
        return []
    entries: list[dict[str, object]] = []
    for item in value if isinstance(value, list) else [value]:
        if not isinstance(item, dict):
            raise _InvalidResponseError(f"{name} entry must be an object, got {_show(item)}")
        entries.append(item)
    return entries


def _integer(entry: dict[str, object], label: str, name: str) -> int:
    value = entry.get(name)
    if not isinstance(value, int) or isinstance(value, bool):
        raise _InvalidResponseError(f"{label} must be an integer, got {_show(value)}")
    return value


def _text(entry: dict[str, object], label: str, name: str) -> str:
    value = entry.get(name)
    if not isinstance(value, str):
        raise _InvalidResponseError(f"{label} must be text, got {_show(value)}")
    return value


def _show(value: object) -> str:
    shown = repr(value)
    if len(shown) > _SHOWN_VALUE_LENGTH:
        return f"{shown[:_SHOWN_VALUE_LENGTH]}..."
    return shown


_READERS: dict[Feed, Callable[[bytes], Reading]] = {
    Feed.BUS_VEHICLES: _read_bus_vehicles,
    Feed.BUS_ROUTES: _read_bus_routes,
    Feed.BUS_PATTERNS: _read_bus_patterns,
    Feed.TRAIN_POSITIONS: _read_train_positions,
    Feed.GTFSRT_VEHICLE_POSITIONS: _read_gtfs_realtime(count_vehicles=True),
    Feed.GTFSRT_TRIP_UPDATES: _read_gtfs_realtime(count_vehicles=False),
    Feed.GTFSRT_SERVICE_ALERTS: _read_gtfs_realtime(count_vehicles=False),
}
