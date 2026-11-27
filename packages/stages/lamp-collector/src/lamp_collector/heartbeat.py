"""Reporting a health check to a dead man's switch, such as Healthchecks.io.

The switch alerts when a check reports failure, and also when reports stop
coming, which catches the host being down. Its URL works as a password, since
anyone holding it can report, so it never appears in an error.
"""

from collections.abc import Sequence

import httpx

TIMEOUT_SECONDS = 10.0
# Retries of a call that could not connect.
RETRIES = 2


class HeartbeatError(Exception):
    """The report did not reach the switch. The message never holds its URL."""


def heartbeat_client() -> httpx.Client:
    transport = httpx.HTTPTransport(retries=RETRIES)
    return httpx.Client(
        timeout=TIMEOUT_SECONDS, transport=transport, headers={"User-Agent": "chicago-lamp"}
    )


def send_heartbeat(client: httpx.Client, url: str, problems: Sequence[str]) -> None:
    """Report success to url, or report the problems, one per line, to its /fail endpoint."""
    base = url.rstrip("/")
    target = f"{base}/fail" if problems else url
    try:
        response = client.post(target, content="\n".join(problems).encode())
        response.raise_for_status()
    except httpx.HTTPError as error:
        message = str(error).replace(base, "LAMP_HEALTH_PING_URL")
        raise HeartbeatError(f"{type(error).__name__}: {message}") from None
