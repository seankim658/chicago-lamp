import httpx
import pytest
from lamp_collector.heartbeat import HeartbeatError, send_heartbeat

URL = "https://hc-ping.example/4f1c0de0-secret-check-id"


class Recorder:
    def __init__(self, status: int = 200) -> None:
        self.requests: list[httpx.Request] = []
        self._status = status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self._status, text="OK")

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))


def test_a_healthy_check_pings_the_url() -> None:
    recorder = Recorder()

    send_heartbeat(recorder.client(), URL, [])

    [request] = recorder.requests
    assert request.method == "POST"
    assert str(request.url) == URL


def test_problems_are_sent_to_the_fail_endpoint_one_per_line() -> None:
    recorder = Recorder()

    send_heartbeat(recorder.client(), URL, ["bus_vehicles: no polls", "disk: 9.0 GB free"])

    [request] = recorder.requests
    assert str(request.url) == f"{URL}/fail"
    assert request.content == b"bus_vehicles: no polls\ndisk: 9.0 GB free"


def test_a_refused_ping_raises_without_showing_the_url() -> None:
    with pytest.raises(HeartbeatError) as caught:
        send_heartbeat(Recorder(status=404).client(), URL, [])

    assert "secret-check-id" not in str(caught.value)
    assert "404" in str(caught.value)


def test_an_unreachable_url_raises_without_showing_it() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach {request.url}")

    client = httpx.Client(transport=httpx.MockTransport(refuse))
    with pytest.raises(HeartbeatError) as caught:
        send_heartbeat(client, URL, [])

    assert "secret-check-id" not in str(caught.value)
    assert "ConnectError" in str(caught.value)
