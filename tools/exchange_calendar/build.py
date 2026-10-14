"""Build the holiday dataset from library calendars plus the official snapshot."""

from __future__ import annotations

import warnings
from datetime import datetime, timezone

import exchange_calendars as ec
import pandas as pd
import pandas_market_calendars as mcal

from tools.exchange_calendar.exchanges import ALIASES, EXCHANGES, LIBRARY_SOURCE_URL, Exchange
from tools.exchange_calendar.quality import source_is_allowed, unresolved_disagreements
from tools.exchange_calendar.settlement_upgrades import (
    FULL_CLOSURE_UPGRADES,
    TIER_B_TERMINAL_SOURCE,
)
from tools.exchange_calendar.official import (
    COMPLETE_CLOSURE_YEARS,
    RESOLVED_OFFICIAL_CONFLICTS,
    UNVERIFIED_EARLY_CLOSE_CLOCKS,
    OfficialEvent,
    official_by_exchange,
    official_events,
    unverified_by_exchange,
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
    "note",
]

UNNAMED_CLOSURE = "Unnamed closure (no holiday name in the libraries)"

# Whole-day closures. typhoon_partial is not here: the exchange traded part of
# the day, so the row carries the hours that did trade.
CLOSURE_TYPES = {"full_closure", "typhoon_closure", "weather_closure"}
# Rows that describe a shortened or shifted session. open_time and close_time
# are required on these.
SESSION_TYPES = {"early_close", "late_open", "typhoon_partial"}
EVENT_TYPES = CLOSURE_TYPES | SESSION_TYPES | {"other"}
STATUSES = ("official", "secondary-confirmed", "unverified", "pending")
DISAGREEMENT_COLUMNS = [
    "exchange",
    "date",
    "library",
    "library_claim",
    "dataset_claim",
    "dataset_status",
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


def _native_holiday_names(exchange: Exchange) -> dict[str, str]:
    """Holiday names from the native pandas_market_calendars classes. Names only, not a second source."""
    names: dict[str, str] = {}
    for code in exchange.mcal_codes:
        calendar = mcal.get_calendar(code)
        try:
            named = calendar.regular_holidays.holidays(exchange.start, exchange.end, return_name=True)
        except Exception:
            continue
        for ts, name in named.items():
            label = pd.Timestamp(ts).tz_localize(None).strftime("%Y-%m-%d")
            names.setdefault(label, str(name))
    return names


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
    native_names = _native_holiday_names(exchange)
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
                "name": names.get(date) or native_names.get(date) or UNNAMED_CLOSURE,
                "source_url": LIBRARY_SOURCE_URL,
                "status": _status_for_closure(exchange, date, native_closed),
                "note": "",
            }
        )

    schedule = calendar.schedule
    early_labels = set()
    for early in calendar.early_closes:
        label = pd.Timestamp(early).strftime("%Y-%m-%d")
        early_labels.add(label)
        open_ts = schedule.loc[early, "open"]
        close_ts = schedule.loc[early, "close"]
        close_clock = _local_clock(close_ts, exchange.tz, label)
        override = UNVERIFIED_EARLY_CLOSE_CLOCKS.get((exchange.code, label))
        if override is not None:
            close_clock = override
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
                "note": "",
            }
        )

    for code, label in UNVERIFIED_EARLY_CLOSE_CLOCKS:
        if code == exchange.code and label not in early_labels:
            raise RuntimeError(f"close-time override {code} {label} matches no early-close session")

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
                    "note": "",
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
                "status": event.status,
                "note": event.note,
            }
        )
    return kept


def apply_full_closure_upgrades(rows: list[dict]) -> list[dict]:
    """Upgrade matching unverified full_closure rows to secondary-confirmed.

    Skips official rows, non-full_closure types, and keys with no matching row.
    Names, hours, and event types are left unchanged.
    """
    for row in rows:
        key = (row["exchange"], row["date"], row["event_type"])
        if key not in FULL_CLOSURE_UPGRADES:
            continue
        if row["status"] != "unverified" or row["event_type"] != "full_closure":
            continue
        row["status"] = "secondary-confirmed"
        row["source_url"] = TIER_B_TERMINAL_SOURCE
    return rows


def validate_rows(frame: pd.DataFrame) -> None:
    """Reject rows the schema does not allow. Raises ValueError."""
    problems = []
    bad_types = sorted(set(frame["event_type"]) - EVENT_TYPES)
    if bad_types:
        problems.append(f"unknown event_type {bad_types}")
    bad_status = sorted(set(frame["status"]) - set(STATUSES))
    if bad_status:
        problems.append(f"unknown status {bad_status}")
    closures = frame[frame["event_type"].isin(CLOSURE_TYPES)]
    timed = closures[(closures["open_time"] != "") | (closures["close_time"] != "")]
    if len(timed):
        problems.append(f"closure rows with hours: {timed[['exchange', 'date']].values.tolist()[:5]}")
    sessions = frame[frame["event_type"].isin(SESSION_TYPES)]
    untimed = sessions[(sessions["open_time"] == "") | (sessions["close_time"] == "")]
    if len(untimed):
        problems.append(f"session rows missing hours: {untimed[['exchange', 'date', 'event_type']].values.tolist()[:5]}")
    weather = frame[frame["event_type"].isin({"typhoon_closure", "typhoon_partial", "weather_closure"})]
    unsourced = weather[(weather["status"] != "official") | ~weather["source_url"].str.startswith("https://www.hkex.com.hk/")]
    if len(unsourced):
        problems.append(f"weather rows without an official HKEX notice: {unsourced[['exchange', 'date']].values.tolist()[:5]}")
    unnamed_other = frame[(frame["event_type"] == "other") & (frame["name"] == "")]
    if len(unnamed_other):
        problems.append("event_type other requires a name")
    bad_sources = frame[~frame["source_url"].map(source_is_allowed)]
    if len(bad_sources):
        preview = bad_sources[["exchange", "date", "source_url"]].head(5).values.tolist()
        problems.append(f"source_url must be an http(s) URL or an allowed tier-B source label: {preview}")
    if problems:
        raise ValueError("; ".join(problems))


def _hhmm(clock: str) -> str:
    return clock[-5:]


def build_disagreements(rows: list[dict]) -> pd.DataFrame:
    """Compare every dataset closure or early close with each library calendar.

    A mismatch is resolved in one of three ways, and no year-based rule decides it:

    - ``resolved``: the row is an official fact and ``RESOLVED_OFFICIAL_CONFLICTS``
      names this exchange, date, and library.
    - ``labeled-unverified``: the libraries disagree with a row that is labeled
      ``unverified``. The dataset makes no confirmed claim for that row.
    - ``unresolved``: anything else. ``build_dataset`` raises on these.

    A closure that a library reports on a date in a complete-list year, where the
    fetched page does not list it, is always unresolved.
    """
    by_key = {
        (row["exchange"], row["date"]): row
        for row in rows
        if row["event_type"] in CLOSURE_TYPES | {"early_close", "other"}
    }
    official_closure_dates = {
        (event.exchange, event.date)
        for event in official_events()
        if event.event_type in CLOSURE_TYPES
    }
    present = {(row["exchange"], row["date"], row["event_type"]) for row in rows}
    records = []
    for event in official_events():
        if (event.exchange, event.date, event.event_type) in present:
            continue
        records.append(
            _record(
                event.exchange,
                event.date,
                "dataset",
                "missing",
                event.event_type,
                "absent",
                event.source_url,
                "unresolved",
                "Official snapshot event is missing from the dataset.",
            )
        )
    for code, exchange in EXCHANGES.items():
        if code in ALIASES:
            continue
        complete_years = COMPLETE_CLOSURE_YEARS.get(code, frozenset())
        claims = _library_claims(exchange)
        dates = {date for ex, date in by_key if ex == code}
        for library, closed, clocks in claims:
            dates |= {day for day in closed if day >= exchange.start}
        for date in sorted(dates):
            row = by_key.get((code, date))
            dataset_closed = row is not None and row["event_type"] in CLOSURE_TYPES
            dataset_early = row is not None and row["event_type"] == "early_close"
            dataset_other = row is not None and row["event_type"] == "other"
            status = row["status"] if row is not None else "absent"
            source_url = row["source_url"] if row is not None else ""
            year = int(date[:4])
            for library, closed, clocks in claims:
                library_closed = date in closed
                library_clock = _hhmm(clocks[date]) if date in clocks else ""
                closure_mismatch = dataset_closed != library_closed
                clock_mismatch = (
                    dataset_early
                    and not library_closed
                    and library_clock != _hhmm(row["close_time"])
                )
                if not closure_mismatch and not clock_mismatch:
                    continue
                dataset_claim = (
                    "closed" if dataset_closed
                    else f"early_close {_hhmm(row['close_time'])}" if dataset_early
                    else "other" if dataset_other
                    else "open"
                )
                library_claim = (
                    "closed" if library_closed
                    else f"session_close {library_clock}" if library_clock
                    else "open"
                )
                sourced_conflict = (code, date, library) in RESOLVED_OFFICIAL_CONFLICTS
                if status in {"official", "secondary-confirmed"} and sourced_conflict:
                    resolution = "resolved"
                    note = (
                        "Fetched official notice differs from this library. The dataset keeps the official event."
                        if status == "official"
                        else "Fetched secondary source differs from this library. The dataset keeps the sourced event."
                    )
                elif status == "official":
                    resolution = "unresolved"
                    note = "Official fact and library disagree, and the conflict is not in RESOLVED_OFFICIAL_CONFLICTS."
                elif (
                    library_closed
                    and year in complete_years
                    and (code, date) not in official_closure_dates
                ):
                    resolution = "unresolved"
                    note = "Library closes this weekday in a year whose fetched official list is complete, and the list does not include it."
                elif status == "unverified":
                    resolution = "labeled-unverified"
                    note = "Libraries disagree and no official source settled this row. The row is labeled unverified."
                else:
                    resolution = "unresolved"
                    note = f"Libraries disagree with a row labeled {status}. Only unverified rows may carry a library disagreement."
                records.append(
                    _record(
                        code,
                        date,
                        library,
                        library_claim,
                        dataset_claim,
                        status,
                        source_url,
                        resolution,
                        note,
                    )
                )
    frame = pd.DataFrame.from_records(records, columns=DISAGREEMENT_COLUMNS)
    if len(frame):
        frame = frame.sort_values(["exchange", "date", "library"]).reset_index(drop=True)
    return frame


def _record(
    exchange: str,
    date: str,
    library: str,
    library_claim: str,
    dataset_claim: str,
    dataset_status: str,
    source_url: str,
    resolution: str,
    note: str,
) -> dict:
    return {
        "exchange": exchange,
        "date": date,
        "library": library,
        "library_claim": library_claim,
        "dataset_claim": dataset_claim,
        "dataset_status": dataset_status,
        "source_url": source_url,
        "resolution": resolution,
        "note": note,
    }


def build_dataset(retrieved_at: str | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    if retrieved_at is None:
        retrieved_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    official = official_by_exchange()
    unverified = unverified_by_exchange()
    rows: list[dict] = []
    for exchange in EXCHANGES.values():
        if exchange.code in ALIASES:
            continue
        base = _base_rows(exchange)
        events = official.get(exchange.code, []) + unverified.get(exchange.code, [])
        rows.extend(apply_full_closure_upgrades(_apply_official(base, events)))
    for row in rows:
        row["retrieved_at"] = retrieved_at
    frame = pd.DataFrame(rows, columns=COLUMNS)
    frame = frame.sort_values(["exchange", "date", "event_type"]).reset_index(drop=True)
    if frame.duplicated(["exchange", "date", "event_type"]).any():
        raise RuntimeError("dataset has duplicate exchange/date/event_type keys")
    validate_rows(frame)
    disagreements = build_disagreements(rows)
    pending = unresolved_disagreements(disagreements)
    if len(pending):
        raise RuntimeError(f"{len(pending)} unresolved library/official disagreements: {pending.head(10).to_dict(orient='records')}")
    return frame, disagreements
