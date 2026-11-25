"""Read every recorded CTA response the fixture capture saved."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from lamp_collector.feeds import Feed, FeedRequest
from lamp_collector.fetch import FetchResult
from lamp_collector.responses import Reading, Status, read_response

NOW = datetime(2026, 11, 23, 15, 0, 0, tzinfo=UTC)
CTA_ERROR_MARK = b"<errCd>"


def recorded(cta_fixtures: Path) -> list[tuple[str, bytes, Reading]]:
    """Return each recorded body with its file name and how it reads."""
    readings: list[tuple[str, bytes, Reading]] = []
    for line in (cta_fixtures / "calls.jsonl").read_text().splitlines():
        entry = json.loads(line)
        if entry["file"] is None:
            continue
        body = (cta_fixtures / entry["file"]).read_bytes()
        request = FeedRequest(Feed(entry["feed"]), entry["url"])
        result = FetchResult(request, NOW, NOW, http_status=entry["http_status"], body=body)
        readings.append((entry["file"], body, read_response(result)))
    return readings


def test_every_recorded_response_reads_without_a_parse_error(cta_fixtures: Path) -> None:
    unreadable = [
        (name, reading.error)
        for name, _, reading in recorded(cta_fixtures)
        if reading.status is Status.PARSE_ERROR
    ]

    assert unreadable == []


def test_a_recorded_cta_error_body_reads_as_an_api_error(cta_fixtures: Path) -> None:
    errors = [
        (name, reading) for name, body, reading in recorded(cta_fixtures) if CTA_ERROR_MARK in body
    ]
    if not errors:
        pytest.skip("the fixtures hold no CTA error body")

    assert {reading.status for _, reading in errors} == {Status.API_ERROR}


def test_recorded_bus_and_train_polls_find_vehicles(cta_fixtures: Path) -> None:
    counts = {
        feed: sum(
            reading.vehicle_count or 0
            for name, _, reading in recorded(cta_fixtures)
            if name.startswith(feed)
        )
        for feed in (Feed.BUS_VEHICLES, Feed.TRAIN_POSITIONS)
    }

    assert all(count > 0 for count in counts.values()), counts
