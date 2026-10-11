"""Build the holiday dataset from library calendars plus the official snapshot."""

from __future__ import annotations

import warnings
from datetime import datetime, timezone

import exchange_calendars as ec
import pandas as pd
import pandas_market_calendars as mcal

from tools.exchange_calendar.exchanges import EXCHANGES, LIBRARY_SOURCE_URL, Exchange
from tools.exchange_calendar.official import (
    COMPLETE_CLOSURE_YEARS,
    OfficialEvent,
    official_by_exchange,
    official_events,
)

warnings.filterwarnings(
    "ignore",
    message=".*discontinued.*",
    category=UserWarning,
)

COLUMNS = [
    "exchange",
    "date",
    "event_type",
    "open_time",
    "close_time",
    "name",
    "source_url",
    "retrieved_at",
    "status",
]

CLOSURE_TYPES = {"full_closure", "typhoon_closure", "typhoon_partial"}
DISAGREEMENT_COLUMNS = [
    "exchange",
    "date",
    "library",
    "library_claim",
    "official_claim",
    "source_url",
    "resolution",
    "note",
]


def _local_clock(ts: pd.Timestamp, tz: str, session_date: str) -> str:
    local = ts.tz_convert(tz)
    clock = local.strftime("%H:%M")
    if local.strftime("%Y-%m-%d") != session_date:
        return local.strftime("%Y-%m-%dT%H:%M")
    return clock


def _weekday_closures(sessions: pd.DatetimeIndex, start: str, end: str) -> set[str]:
    session_days = set(pd.DatetimeIndex(sessions).tz_localize(None).normalize().strftime("%Y-%m-%d"))
    closed = []
    for day in pd.bdate_range(start, end):
        label = day.strftime("%Y-%m-%d")
        if label not in session_days:
            closed.append(label)
    return set(closed)


def _holiday_names(calendar, start: str, end: str) -> dict[str, str]:
    try:
        named = calendar.regular_holidays.holidays(start, end, return_name=True)
    except Exception:
        return {}
    if named is None or len(named) == 0:
        return {}
    out = {}
    for ts, name in named.items():
        label = pd.Timestamp(ts).tz_localize(None).strftime("%Y-%m-%d")
        out.setdefault(label, str(name))
    return out


def _expected_open(calendar, session_date: pd.Timestamp):
    current = None
    for start, tod in calendar.open_times:
        if start is None or pd.Timestamp(session_date) >= pd.Timestamp(start).tz_localize(None):
            current = tod
        else:
            break
    return current


def _krx_late_opens(calendar, tz: str) -> list[dict]:
    """KRX special offsets move the open to 10:00. ``late_opens`` itself is empty."""
    schedule = calendar.schedule
    names_by_year_first = {}
    rows = []
    for session, open_ts in schedule["open"].items():
        session_ts = pd.Timestamp(session)
        expected = _expected_open(calendar, session_ts)
        if expected is None:
            continue
        local = open_ts.tz_convert(tz)
        actual = local.time().replace(second=0, microsecond=0)
        if actual <= expected:
            continue
        label = session_ts.strftime("%Y-%m-%d")
        year = session_ts.year
        names_by_year_first.setdefault(year, label)
        name = "First business day of the year" if names_by_year_first[year] == label else "CSAT day"
        rows.append(
            {
                "date": label,
                "event_type": "late_open",
                "open_time": _local_clock(open_ts, tz, label),
                "close_time": _local_clock(schedule.loc[session, "close"], tz, label),
                "name": name,
            }
        )
    return rows


def _mcal_sessions_and_closes(code: str, start: str, end: str, tz: str) -> tuple[set[str], dict[str, str]]:
    calendar = mcal.get_calendar(code)
    schedule = calendar.schedule(start_date=start, end_date=end)
    sessions = set(schedule.index.strftime("%Y-%m-%d"))
    closes = schedule["market_close"].dt.tz_convert(tz).dt.strftime("%H:%M")
    close_map = {day: clock for day, clock in zip(schedule.index.strftime("%Y-%m-%d"), closes)}
    return sessions, close_map


def library_closure_dates(exchange: Exchange) -> set[str]:
    """Weekday dates exchange_calendars treats as closed inside the exchange window."""
    calendar = ec.get_calendar(exchange.ec_code, start=exchange.start, end=exchange.end)
    return _weekday_closures(calendar.sessions, exchange.start, exchange.end)


def _status_for_closure(exchange: Exchange, date: str, native_closed: list[set[str]]) -> str:
    if not exchange.independent_mcal:
        return "unverified"
    if native_closed and all(date in closed for closed in native_closed):
        return "secondary-confirmed"
    return "unverified"


def _status_for_early(exchange: Exchange, date: str, ec_close: str, native_closes: list[dict[str, str]]) -> str:
    if not exchange.independent_mcal or not native_closes:
        return "unverified"
    for close_map in native_closes:
        if close_map.get(date) != ec_close:
            return "unverified"
    return "secondary-confirmed"


def _base_rows(exchange: Exchange) -> list[dict]:
    calendar = ec.get_calendar(exchange.ec_code, start=exchange.start, end=exchange.end)
    names = _holiday_names(calendar, exchange.start, exchange.end)
    native_closes = []
    native_closed = []
    for code in exchange.mcal_codes:
        sessions, close_map = _mcal_sessions_and_closes(code, exchange.start, exchange.end, exchange.tz)
        native_closes.append(close_map)
        native_closed.append(_weekday_closures(pd.DatetimeIndex(sorted(sessions)), exchange.start, exchange.end))

    rows = []
    for date in sorted(_weekday_closures(calendar.sessions, exchange.start, exchange.end)):
        rows.append(
            {
                "exchange": exchange.code,
                "date": date,
                "event_type": "full_closure",
                "open_time": "",
                "close_time": "",
                "name": names.get(date, "Closure"),
                "source_url": LIBRARY_SOURCE_URL,
                "status": _status_for_closure(exchange, date, native_closed),
            }
        )

    schedule = calendar.schedule
    for early in calendar.early_closes:
        label = pd.Timestamp(early).strftime("%Y-%m-%d")
        open_ts = schedule.loc[early, "open"]
        close_ts = schedule.loc[early, "close"]
        close_clock = _local_clock(close_ts, exchange.tz, label)
        rows.append(
            {
                "exchange": exchange.code,
                "date": label,
                "event_type": "early_close",
                "open_time": _local_clock(open_ts, exchange.tz, label),
                "close_time": close_clock,
                "name": "Early close",
                "source_url": LIBRARY_SOURCE_URL,
                "status": _status_for_early(exchange, label, close_clock[-5:], native_closes),
            }
        )

    if exchange.detect_late_opens:
        for late in _krx_late_opens(calendar, exchange.tz):
            rows.append(
                {
                    "exchange": exchange.code,
                    "date": late["date"],
                    "event_type": "late_open",
                    "open_time": late["open_time"],
                    "close_time": late["close_time"],
                    "name": late["name"],
                    "source_url": LIBRARY_SOURCE_URL,
                    "status": "unverified",
                }
            )
    return rows


def _library_claims(exchange: Exchange) -> list[tuple[str, set[str], dict[str, str]]]:
    """Return (library id, closed dates, close clock by session date) for disagreement checks."""
    claims = []
    calendar = ec.get_calendar(exchange.ec_code, start=exchange.start, end=exchange.end)
    ec_closed = _weekday_closures(calendar.sessions, exchange.start, exchange.end)
    ec_clocks = {}
    for session, close_ts in calendar.schedule["close"].items():
        label = pd.Timestamp(session).strftime("%Y-%m-%d")
        ec_clocks[label] = _local_clock(close_ts, exchange.tz, label)
    claims.append((f"exchange_calendars:{exchange.ec_code}", ec_closed, ec_clocks))
    for code in exchange.mcal_codes:
        sessions, close_map = _mcal_sessions_and_closes(code, exchange.start, exchange.end, exchange.tz)
        closed = _weekday_closures(pd.DatetimeIndex(sorted(sessions)), exchange.start, exchange.end)
        claims.append((f"pandas_market_calendars:{code}", closed, close_map))
    return claims


def _apply_official(rows: list[dict], events: list[OfficialEvent]) -> list[dict]:
    """Replace every library row on a date that has a sourced event.

    A sourced event describes the whole session for that exchange and date, so a
    library row of any type on the same date conflicts with it and is dropped,
    even when the event type differs (for example a library ``early_close`` on a
    day the exchange says was a ``full_closure``).
    """
    overridden = {(event.exchange, event.date) for event in events}
    kept = [row for row in rows if (row["exchange"], row["date"]) not in overridden]
    for event in events:
        kept.append(
            {
                "exchange": event.exchange,
                "date": event.date,
                "event_type": event.event_type,
                "open_time": event.open_time,
                "close_time": event.close_time,
                "name": event.name,
                "source_url": event.source_url,
                "status": "official",
            }
        )
    return kept


def _official_claim(event: OfficialEvent) -> str:
    if event.event_type == "early_close":
        return f"early_close {event.close_time}"
    if event.event_type in CLOSURE_TYPES:
        return "closed"
    return event.event_type


def _library_claim(event: OfficialEvent, closed: set[str], clocks: dict[str, str]) -> str:
    if event.event_type in CLOSURE_TYPES:
        return "closed" if event.date in closed else "open"
    if event.date in closed:
        return "closed"
    clock = clocks.get(event.date)
    if clock is None:
        return "open"
    clock_hhmm = clock[-5:]
    if clock_hhmm == event.close_time:
        return f"early_close {event.close_time}"
    return f"session_close {clock_hhmm}"


def build_disagreements(rows: list[dict]) -> pd.DataFrame:
    """Compare each official fact with each library. Unresolved rows fail the build tests."""
    official = official_by_exchange()
    records = []
    present = {(row["exchange"], row["date"], row["event_type"]) for row in rows}
    for event in official_events():
        if (event.exchange, event.date, event.event_type) in present:
            continue
        records.append(
            {
                "exchange": event.exchange,
                "date": event.date,
                "library": "dataset",
                "library_claim": "missing",
                "official_claim": event.event_type,
                "source_url": event.source_url,
                "resolution": "unresolved",
                "note": "Official snapshot event is missing from the dataset.",
            }
        )
    for code, exchange in EXCHANGES.items():
        events = official.get(code, [])
        if not events:
            continue
        claims = _library_claims(exchange)
        complete_years = COMPLETE_CLOSURE_YEARS.get(code, frozenset())
        official_closure_dates = {
            event.date for event in events if event.event_type in CLOSURE_TYPES
        }
        for library, closed, clocks in claims:
            for event in events:
                library_claim = _library_claim(event, closed, clocks)
                official_claim = _official_claim(event)
                if library_claim == official_claim:
                    continue
                year = int(event.date[:4])
                # A complete fetched holiday list that disagrees with a library
                # stays unresolved until someone checks the parse. Weather notices
                # are not a complete year list; the fetched notice is the resolution.
                complete_conflict = year in complete_years
                records.append(
                    {
                        "exchange": code,
                        "date": event.date,
                        "library": library,
                        "library_claim": library_claim,
                        "official_claim": official_claim,
                        "source_url": event.source_url,
                        "resolution": "unresolved" if complete_conflict else "resolved",
                        "note": (
                            "Official page and library disagree inside a year treated as a complete list."
                            if complete_conflict
                            else "Fetched official notice differs from this library. The dataset keeps the official event."
                        ),
                    }
                )
            for date in sorted(closed):
                year = int(date[:4])
                if year not in complete_years or date in official_closure_dates:
                    continue
                records.append(
                    {
                        "exchange": code,
                        "date": date,
                        "library": library,
                        "library_claim": "closed",
                        "official_claim": "open",
                        "source_url": "",
                        "resolution": "unresolved",
                        "note": (
                            "Library closes this weekday in a year whose official page "
                            "was fetched as a complete closure list, and the page does not list it."
                        ),
                    }
                )
    frame = pd.DataFrame.from_records(records, columns=DISAGREEMENT_COLUMNS)
    if len(frame):
        frame = frame.sort_values(["exchange", "date", "library"]).reset_index(drop=True)
    return frame


def build_dataset(retrieved_at: str | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    if retrieved_at is None:
        retrieved_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    official = official_by_exchange()
    rows: list[dict] = []
    for exchange in EXCHANGES.values():
        base = _base_rows(exchange)
        rows.extend(_apply_official(base, official.get(exchange.code, [])))
    for row in rows:
        row["retrieved_at"] = retrieved_at
    frame = pd.DataFrame(rows, columns=COLUMNS)
    frame = frame.sort_values(["exchange", "date", "event_type"]).reset_index(drop=True)
    if frame.duplicated(["exchange", "date", "event_type"]).any():
        raise RuntimeError("dataset has duplicate exchange/date/event_type keys")
    disagreements = build_disagreements(rows)
    return frame, disagreements
