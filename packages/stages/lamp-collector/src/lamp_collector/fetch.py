"""One call to a CTA feed, recorded whatever its outcome."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

import httpx

from .feeds import FeedRequest
from .keys import ApiKeys

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(UTC)


def cta_client(timeout_seconds: float = 10.0) -> httpx.Client:
    return httpx.Client(timeout=timeout_seconds, headers={"User-Agent": "chicago-lamp"})


class Failure(StrEnum):
    """Why a call got no HTTP response."""

    TIMEOUT = "timeout"
    CONNECTION = "connection"


@dataclass(frozen=True)
class FetchResult:
    """A call's outcome. The error text never holds a key.

    A call either got an HTTP response, of any status, or has a failure and no
    response time, status or body.
    """

    request: FeedRequest
    request_time_utc: datetime
    response_time_utc: datetime | None = None
    http_status: int | None = None
    body: bytes | None = None
    failure: Failure | None = None
    error: str | None = None


class Fetcher:
    def __init__(self, client: httpx.Client, keys: ApiKeys, clock: Clock = utc_now) -> None:
        self._client = client
        self._keys = keys
        self._clock = clock

    def fetch(self, request: FeedRequest) -> FetchResult:
        sent_at = self._clock()
        try:
            response = self._client.get(request.url(self._keys))
        except httpx.TimeoutException as error:
            return self._failed(request, sent_at, Failure.TIMEOUT, error)
        except httpx.TransportError as error:
            return self._failed(request, sent_at, Failure.CONNECTION, error)
        return FetchResult(
            request,
            sent_at,
            response_time_utc=self._clock(),
            http_status=response.status_code,
            body=response.content,
        )

    def _failed(
        self, request: FeedRequest, sent_at: datetime, failure: Failure, error: Exception
    ) -> FetchResult:
        message = f"{type(error).__name__}: {self._keys.redact(str(error))}"
        return FetchResult(request, sent_at, failure=failure, error=message)
