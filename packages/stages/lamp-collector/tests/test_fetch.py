from collections.abc import Callable, Iterator
from datetime import UTC, datetime

import httpx
import pytest
from lamp_collector.feeds import bus_routes, train_positions
from lamp_collector.fetch import Failure, Fetcher
from lamp_collector.keys import ApiKeys

BUS_KEY = "Bus0Key0Bus0Key0Bus0Key0B"
TRAIN_KEY = "train0key0train0key0train0key012"
KEYS = ApiKeys(bus=BUS_KEY, train=TRAIN_KEY)

SENT = datetime(2026, 11, 23, 15, 0, 0, tzinfo=UTC)
RECEIVED = datetime(2026, 11, 23, 15, 0, 1, tzinfo=UTC)

Handler = Callable[[httpx.Request], httpx.Response]


def fetcher(handler: Handler) -> Fetcher:
    times: Iterator[datetime] = iter([SENT, RECEIVED])
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return Fetcher(client, KEYS, clock=lambda: next(times))


def test_response_is_recorded_with_its_times_status_and_body() -> None:
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(str(request.url))
        return httpx.Response(200, content=b'{"bustime-response": {}}')

    result = fetcher(handler).fetch(bus_routes())

    assert sent == [bus_routes().url(KEYS)]
    assert result.request == bus_routes()
    assert result.request_time_utc == SENT
    assert result.response_time_utc == RECEIVED
    assert result.http_status == 200
    assert result.body == b'{"bustime-response": {}}'
    assert result.failure is None
    assert result.error is None


def test_http_error_keeps_the_status_and_body() -> None:
    result = fetcher(lambda request: httpx.Response(503, content=b"busy")).fetch(bus_routes())

    assert result.http_status == 503
    assert result.body == b"busy"
    assert result.failure is None


@pytest.mark.parametrize(
    ("error", "failure"),
    [
        (httpx.ReadTimeout, Failure.TIMEOUT),
        (httpx.ConnectTimeout, Failure.TIMEOUT),
        (httpx.ConnectError, Failure.CONNECTION),
        (httpx.RemoteProtocolError, Failure.CONNECTION),
    ],
)
def test_transport_failure_is_recorded_without_the_key(
    error: type[httpx.TransportError], failure: Failure
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error(f"gave up on {request.url}", request=request)

    result = fetcher(handler).fetch(train_positions())

    assert result.failure is failure
    assert result.request_time_utc == SENT
    assert result.response_time_utc is None
    assert result.http_status is None
    assert result.body is None
    assert result.error is not None
    assert result.error.startswith(f"{error.__name__}: gave up on ")
    assert TRAIN_KEY not in result.error
    assert TRAIN_KEY not in repr(result)
