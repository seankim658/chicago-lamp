import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from capture_fixtures import (
    INDEX_NAME,
    Call,
    Capture,
    CaptureError,
    Pacer,
    capture_once,
    capture_rates,
    fixture_members,
    leaked_keys,
    main,
    pattern_ids,
    route_ids,
    write_zip,
)
from lamp_collector.feeds import bus_routes, bus_vehicles
from lamp_collector.fetch import Fetcher, FetchResult
from lamp_collector.keys import ApiKeys

BUS_KEY = "Bus0Key0Bus0Key0Bus0Key0B"
TRAIN_KEY = "train0key0train0key0train0key012"
KEYS = ApiKeys(bus=BUS_KEY, train=TRAIN_KEY)
NOW = datetime(2026, 11, 23, 15, 0, 0, tzinfo=UTC)
ROUTES = [str(number) for number in range(1, 13)]


def bustime(body: dict[str, object]) -> bytes:
    return json.dumps({"bustime-response": body}).encode()


def fake_cta(request: httpx.Request) -> httpx.Response:
    """Answer like CTA, with made-up data, for every endpoint the capture calls."""
    path = request.url.path
    if path.endswith("/getroutes"):
        return httpx.Response(200, content=bustime({"routes": [{"rt": rt} for rt in ROUTES]}))
    if path.endswith("/getvehicles"):
        routes = request.url.params["rt"].split(",")
        vehicles = [{"vid": f"8{rt}", "rt": rt, "pid": int(rt) * 10} for rt in routes]
        return httpx.Response(200, content=bustime({"vehicle": vehicles}))
    if path.endswith("/getpatterns"):
        return httpx.Response(200, content=bustime({"ptr": []}))
    if path.endswith("/ttpositions.aspx"):
        return httpx.Response(200, content=b'{"ctatt": {"route": []}}')
    return httpx.Response(200, content=b"\x0a\x00")


def capture_with(handler: httpx.MockTransport) -> Capture:
    client = httpx.Client(transport=handler)
    return Capture(Fetcher(client, KEYS, clock=lambda: NOW))


def result(body: bytes | None, status: int | None = 200) -> FetchResult:
    return FetchResult(bus_routes(), NOW, http_status=status, body=body)


def test_capture_once_calls_every_feed_and_returns_the_first_batch() -> None:
    capture = capture_with(httpx.MockTransport(fake_cta))

    first_batch = capture_once(capture)

    assert first_batch == ROUTES[:10]
    urls = [call.result.request.public_url for call in capture.calls]
    assert [url.rsplit("/", 1)[1] for url in urls] == [
        "getroutes?format=json",
        "getvehicles?rt=1,2,3,4,5,6,7,8,9,10&tmres=s&format=json",
        "getvehicles?rt=11,12&tmres=s&format=json",
        "getpatterns?pid=10,100,110,120,20,30,40,50,60,70&format=json",
        "getpatterns?pid=80,90&format=json",
        "ttpositions.aspx?rt=Red,Blue,Brn,G,Org,P,Pink,Y&outputType=JSON",
        "ttpositions.aspx?rt=Red&rt=Blue&rt=Brn&rt=G&rt=Org&rt=P&rt=Pink&rt=Y&outputType=JSON",
        "ttpositions.aspx?rt=Red&outputType=JSON",
        "VehiclePositions.pb",
        "TripUpdates.pb",
        "ServiceAlerts.pb",
    ]


def test_capture_rates_polls_buses_and_trains_on_every_tick() -> None:
    capture = capture_with(httpx.MockTransport(fake_cta))

    capture_rates(capture, ["1", "2"], iter([None, None]))

    assert [call.result.request.feed.value for call in capture.calls] == [
        "bus_vehicles",
        "train_positions",
        "bus_vehicles",
        "train_positions",
    ]
    assert {call.label for call in capture.calls} == {"rate sample"}


def test_pacer_waits_out_each_interval_from_the_first_tick() -> None:
    now = [100.0]
    slept: list[float] = []

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds + 2  # each tick's work takes two seconds

    pacer = Pacer(15, clock=lambda: now[0], sleep=sleep)

    assert len(list(pacer.ticks(3))) == 3
    assert slept == [15, 13]


def test_route_ids_reads_getroutes() -> None:
    assert route_ids(result(bustime({"routes": [{"rt": "1"}, {"rt": "X9"}]}))) == ["1", "X9"]


@pytest.mark.parametrize(
    ("body", "status", "message"),
    [
        (None, None, "got status None"),
        (b"busy", 503, "got status 503"),
        (b"<xml/>", 200, "invalid JSON"),
        (bustime({"error": [{"msg": "Invalid API access key supplied"}]}), 200, "no routes"),
    ],
)
def test_route_ids_refuses_an_unusable_response(
    body: bytes | None, status: int | None, message: str
) -> None:
    with pytest.raises(CaptureError, match=message):
        route_ids(result(body, status))


def test_pattern_ids_collects_distinct_ids_and_skips_unusable_responses() -> None:
    results = [
        result(bustime({"vehicle": [{"pid": 5}, {"pid": 7}]})),
        result(bustime({"vehicle": [{"pid": 5}, {"vid": "1"}]})),
        result(bustime({"error": [{"rt": "N5", "msg": "No data found for parameter"}]})),
        result(None, None),
    ]

    assert pattern_ids(results) == ["5", "7"]


def test_fixture_members_store_bodies_by_feed_and_index_every_call() -> None:
    capture = capture_with(httpx.MockTransport(fake_cta))
    capture.fetch(bus_routes(), "all routes")
    capture.calls.append(Call("broken", FetchResult(bus_vehicles(["1"]), NOW)))

    members = dict(fixture_members(capture.calls))

    assert list(members) == ["bus_routes/0000.json", INDEX_NAME]
    entries = [json.loads(line) for line in members[INDEX_NAME].decode().splitlines()]
    assert entries[0]["file"] == "bus_routes/0000.json"
    assert entries[0]["url"] == bus_routes().public_url
    assert entries[0]["http_status"] == 200
    assert entries[1]["file"] is None
    assert entries[1]["label"] == "broken"
    assert entries[1]["response_time_utc"] is None


def test_leaked_keys_names_every_member_holding_a_key() -> None:
    members = [
        ("a.json", b"clean"),
        ("b.json", f"echo {BUS_KEY}".encode()),
        (INDEX_NAME, f"key={TRAIN_KEY}".encode()),
    ]

    assert leaked_keys(members, KEYS) == ["b.json", INDEX_NAME]


def test_write_zip_holds_every_member(tmp_path: Path) -> None:
    path = tmp_path / "fixtures.zip"

    write_zip(path, [("bus_routes/0000.json", b"{}"), (INDEX_NAME, b"\n")])

    with zipfile.ZipFile(path) as archive:
        assert archive.read("bus_routes/0000.json") == b"{}"
        assert archive.namelist() == ["bus_routes/0000.json", INDEX_NAME]
    assert list(tmp_path.iterdir()) == [path]


def test_main_refuses_to_overwrite_an_existing_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    existing = tmp_path / "fixtures.zip"
    existing.write_bytes(b"keep")

    assert main([str(existing)]) == 2
    assert "already exists" in capsys.readouterr().err
    assert existing.read_bytes() == b"keep"


def test_main_stops_before_any_call_when_a_key_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CTA_BUS_KEY", raising=False)
    monkeypatch.setenv("CTA_TRAIN_KEY", TRAIN_KEY)

    assert main([str(tmp_path / "fixtures.zip")]) == 2
    assert "cta_bus_key" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []
