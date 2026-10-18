from __future__ import annotations

from datetime import datetime, timezone

from tools.okx.status import announcement_types, announcements, upcoming_maintenance
from tools.okx.tests.conftest import FakeTransport, load_fixture, make_client


def test_upcoming_maintenance_filters_and_uses_utc() -> None:
    transport = FakeTransport()
    transport.add("/api/v5/system/status", load_fixture("system_status.json"))
    client = make_client(transport)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    windows = upcoming_maintenance(client, now=now)
    titles = [w.title for w in windows]
    assert titles == ["Live incident", "Scheduled upgrade"]
    scheduled = windows[-1]
    assert scheduled.begin_utc.tzinfo is timezone.utc
    assert scheduled.begin_utc.isoformat() == "2030-01-01T00:00:00+00:00"
    assert scheduled.end_utc is not None
    assert scheduled.end_utc.isoformat() == "2030-01-01T02:00:00+00:00"
    assert "spot_margin" in scheduled.services
    assert "perpetual" in scheduled.services
    ongoing = windows[0]
    assert ongoing.state == "ongoing"
    assert "websocket" in ongoing.services


def test_announcements_and_types_keyless_fixtures() -> None:
    transport = FakeTransport()
    transport.add("/api/v5/support/announcements", load_fixture("announcements.json"))
    transport.add("/api/v5/support/announcement-types", load_fixture("announcement_types.json"))
    client = make_client(transport)
    rows = announcements(client)
    assert rows[0].title.startswith("BTC-USDT-SWAP")
    assert rows[0].published_ms == 1700000000000
    kinds = announcement_types(client)
    assert kinds[0]["annType"] == "announcements-delistings"
    header_names = {name.lower() for name in transport.calls[0]["headers"]}
    assert "ok-access-key" not in header_names
