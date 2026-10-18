"""Exchange status and public announcements (keyless).

Verified against the v5 docs TOC and the public REST list:
``GET /api/v5/system/status``, ``GET /api/v5/support/announcements``,
``GET /api/v5/support/announcement-types``. None of these require signing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from tools.okx import endpoints
from tools.okx.client import PublicClient
from tools.okx.types import Announcement, MaintenanceWindow, utc_from_ms

# Documented ``serviceType`` codes on GET /api/v5/system/status.
SERVICE_TYPE_NAMES: dict[str, str] = {
    "0": "websocket",
    "1": "spot_margin",
    "2": "futures",
    "3": "perpetual",
    "4": "options",
    "5": "trading",
}

ACTIVE_STATES = frozenset({"scheduled", "ongoing", "pre_open"})


def _services(row: dict[str, Any]) -> tuple[str, ...]:
    raw = row.get("serviceType", row.get("system", ""))
    if isinstance(raw, list):
        codes = [str(item) for item in raw]
    elif raw is None or raw == "":
        codes = []
    else:
        codes = [part.strip() for part in str(raw).split(",") if part.strip()]
    names = tuple(SERVICE_TYPE_NAMES.get(code, code) for code in codes)
    system = str(row.get("system") or "").strip()
    if system and system not in names:
        names = names + (system,)
    return names


def parse_maintenance(row: dict[str, Any]) -> MaintenanceWindow:
    begin_ms = int(row.get("begin") or 0)
    end_raw = str(row.get("end") or "").strip()
    end_ms = int(end_raw) if end_raw else None
    return MaintenanceWindow(
        title=str(row.get("title") or ""),
        state=str(row.get("state") or ""),
        begin_utc=utc_from_ms(begin_ms),
        end_utc=utc_from_ms(end_ms) if end_ms is not None else None,
        begin_ms=begin_ms,
        end_ms=end_ms,
        service_type=str(row.get("serviceType") or ""),
        services=_services(row),
        href=str(row.get("href") or ""),
        description=str(row.get("scheDesc") or row.get("desc") or ""),
        maint_type=str(row.get("maintType") or ""),
        system=str(row.get("system") or ""),
    )


def system_status(client: PublicClient, *, state: str = "") -> list[MaintenanceWindow]:
    data = client.get(endpoints.SYSTEM_STATUS, {"state": state})
    return [parse_maintenance(row) for row in data]


def upcoming_maintenance(
    client: PublicClient,
    *,
    now: datetime | None = None,
) -> list[MaintenanceWindow]:
    """Scheduled or ongoing windows with UTC start/end and affected services."""
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    windows = system_status(client)
    out: list[MaintenanceWindow] = []
    for window in windows:
        if window.state not in ACTIVE_STATES:
            continue
        if window.end_utc is not None and window.end_utc <= clock:
            continue
        out.append(window)
    out.sort(key=lambda w: w.begin_ms)
    return out


def announcements(
    client: PublicClient,
    *,
    ann_type: str = "",
    page: int | str = "",
) -> list[Announcement]:
    payload = client.get_payload(
        endpoints.ANNOUNCEMENTS,
        {"annType": ann_type, "page": str(page) if page != "" else ""},
    )
    data = payload.get("data") or []
    rows: Iterable[Any]
    if data and isinstance(data[0], dict) and "details" in data[0]:
        rows = data[0].get("details") or []
    else:
        rows = data
    out: list[Announcement] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        published = row.get("pTime") or row.get("ts") or row.get("publishTime") or 0
        out.append(
            Announcement(
                title=str(row.get("title") or ""),
                ann_type=str(row.get("annType") or row.get("type") or ""),
                published_ms=int(published or 0),
                url=str(row.get("url") or row.get("href") or ""),
            )
        )
    return out


def announcement_types(client: PublicClient) -> list[dict[str, str]]:
    data = client.get(endpoints.ANNOUNCEMENT_TYPES)
    out: list[dict[str, str]] = []
    for row in data:
        if isinstance(row, dict):
            out.append(
                {
                    "annType": str(row.get("annType") or row.get("type") or ""),
                    "annTypeDesc": str(row.get("annTypeDesc") or row.get("desc") or ""),
                }
            )
    return out
