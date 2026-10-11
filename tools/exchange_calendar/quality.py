"""Data-quality checks: staleness, past-date diffs, and unresolved disagreements."""

from __future__ import annotations

import hashlib
import json
import warnings
from datetime import datetime, timezone

import pandas as pd

from tools.exchange_calendar.exchanges import EXCHANGES

CONTENT_COLUMNS = [
    "exchange",
    "date",
    "event_type",
    "open_time",
    "close_time",
    "name",
    "source_url",
    "status",
]

STALENESS_THRESHOLD_DAYS = 35
OFFICIAL_SNAPSHOT_DATE = "2026-10-11"

OPEN_ITEMS = [
    (
        "KRX has no parsed official holiday list. open.krx.co.kr and the KIND market "
        "calendar returned page shells without dates. pandas_market_calendars XKRX is a "
        "mirror of exchange_calendars, so every KRX row is unverified. 2027 and later "
        "are omitted: the exchange announces that year around December, and library "
        "projections through 2050 were not copied."
    ),
    (
        "KRX late opens (first business day of the year, and CSAT days, at 10:00 KST) "
        "come from exchange_calendars special offsets. The late_opens property on that "
        "calendar is empty even when the schedule open is 10:00. The pandas_market_calendars "
        "mirror shows 09:00. CSAT rows also shift the close (16:00 before 2016-08-01, "
        "16:30 after). The last non-January late open in the 2001-2026 schedule is "
        "2020-12-03; 2021-2026 have only the January late open. The 2021 CSAT day and "
        "2025-11-13 were not added because no Korea Exchange page with dates could be read "
        "in this run. Those rows are unverified."
    ),
    (
        "SSE 2027 is omitted. exchange_calendars 4.13.2 refuses dates after 2026. "
        "pandas_market_calendars SSE documents its post-2026 rules as guesses and those "
        "guesses were not copied. 2024-2026 weekday closures that match the fetched SSE "
        "pages are official."
    ),
    (
        "CME is not one calendar. The dataset uses exchange_calendars CMES. Native "
        "pandas_market_calendars CME_Equity and CME Globex Equity disagree on some "
        "full closures (several Good Fridays) and on early-close sets; disagreed rows "
        "are unverified. The CME Group holiday page is product-specific and was not "
        "parsed into session rows. CMES and both native CME calendars treat 2001-09-11 "
        "through 2001-09-14 as sessions. The dataset carries reported closures for 2001-09-11 "
        "to 2001-09-13 as unverified rows, cited to a fetched Wikipedia article that says US "
        "stock exchanges were closed through 2001-09-17. The 2001-09-12 and 2001-09-13 CME "
        "details were not found in a fetched source. The CME early-close clocks for 2026-11-27, "
        "2026-12-24, and 2027-11-26 are 12:15 per CME Globex Equity and 12:00 per CMES and "
        "CME_Equity; they are unverified. The Juneteenth 2027 early close at 12:00 is unverified: "
        "the two native calendars show it and CMES does not. The same native pattern holds for "
        "2026-06-19, and earlier Juneteenth dates were not checked and are not added. CME Group "
        "pages returned HTTP 403 in this run."
    ),
    (
        "Nasdaq is not an independent calendar in either library (alias of NYSE / XNYS), so "
        "agreement between the libraries is not a second source. Only the 2026 Nasdaq Trader "
        "holiday page was fetched, so only 2026 Nasdaq rows are official. Every other Nasdaq "
        "row is unverified, including rows where the two libraries agree."
    ),
    (
        "HKEX regular holidays were not fetched as an annual calendar. Closures are "
        "secondary-confirmed only where exchange_calendars XHKG and pandas_market_calendars "
        "HKEX agree. Early closes exist in exchange_calendars (12:00 HKT) and not in the "
        "native HKEX calendar, so those rows are unverified. 2021-04-06 is closed in "
        "XHKG and open in native HKEX, with no official notice fetched, so it stays "
        "unverified. Morning-only severe-weather sessions that both libraries comment out "
        "are not in the dataset. HKEX Severe Weather Trading, in effect from 23 September "
        "2024, is not modeled. HKEX typhoon_closure history starts at 2016-08-02 and is "
        "incomplete before 2016: no pre-2016 notice was fetched, and no typhoon notice "
        "between 2016 and 2024 is in the dataset unless it was fetched. A missing typhoon row "
        "does not mean the market traded that day."
    ),
    (
        "Historical official archives before the pages in the official snapshot were not "
        "fetched. A refresh re-checks those URLs and rebuilds the library base. It does "
        "not scrape a new December SSE or KRX notice; that notice has to be added to "
        "tools/exchange_calendar/official.py after it is read."
    ),
    (
        "Lunch breaks on HKEX, SSE, JPX, and historical KRX are not columns. open_time "
        "and close_time are the session open and the session close in exchange-local time. "
        "A CME clock that falls on the previous local date is stored as YYYY-MM-DDTHH:MM."
    ),
]


def parse_timestamp(value: str) -> datetime:
    text = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def staleness_warning(last_refreshed: str, *, now: datetime | None = None, threshold_days: int = STALENESS_THRESHOLD_DAYS) -> bool:
    """Warn when the dataset has not been refreshed within the threshold.

    Returns True when a warning was emitted. This check does not fail the build.
    """
    refreshed = parse_timestamp(last_refreshed)
    current = now or datetime.now(timezone.utc)
    age_days = (current - refreshed).total_seconds() / 86400
    if age_days > threshold_days:
        warnings.warn(
            f"exchange calendar dataset is stale: last refreshed {age_days:.0f} days ago "
            f"(threshold {threshold_days} days)",
            UserWarning,
            stacklevel=2,
        )
        return True
    return False


def content_hash(frame: pd.DataFrame) -> str:
    slim = frame[CONTENT_COLUMNS].copy()
    payload = slim.to_csv(index=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:12]


def diff_datasets(previous: pd.DataFrame, updated: pd.DataFrame, *, as_of: str) -> dict:
    """Diff holiday facts. retrieved_at is ignored. Past means date < as_of."""
    old = _indexed(previous)
    new = _indexed(updated)
    old_keys = set(old)
    new_keys = set(new)
    added = sorted(new_keys - old_keys)
    removed = sorted(old_keys - new_keys)
    changed = sorted(
        key for key in old_keys & new_keys if old[key] != new[key]
    )

    def split(keys: list[tuple]) -> tuple[list, list]:
        past, future = [], []
        for key in keys:
            (past if key[1] < as_of else future).append(_public_key(key))
        return past, future

    added_past, added_future = split(added)
    removed_past, removed_future = split(removed)
    changed_past, changed_future = split(changed)
    past_change_count = len(added_past) + len(removed_past) + len(changed_past)
    return {
        "as_of": as_of,
        "past_change_count": past_change_count,
        "past": {"added": added_past, "removed": removed_past, "changed": changed_past},
        "future": {"added": added_future, "removed": removed_future, "changed": changed_future},
    }


ACCEPTED_RESOLUTIONS = frozenset({"resolved", "labeled-unverified"})


def unresolved_disagreements(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty or "resolution" not in frame.columns:
        return frame.iloc[0:0]
    return frame[~frame["resolution"].fillna("").str.lower().isin(ACCEPTED_RESOLUTIONS)].reset_index(drop=True)


def assert_no_unresolved(frame: pd.DataFrame) -> None:
    pending = unresolved_disagreements(frame)
    if len(pending):
        preview = pending.head(20).to_dict(orient="records")
        raise AssertionError(f"{len(pending)} unresolved library/official disagreements: {preview}")


def _indexed(frame: pd.DataFrame) -> dict[tuple, tuple]:
    indexed = {}
    for record in frame[CONTENT_COLUMNS].itertuples(index=False):
        key = (record.exchange, record.date, record.event_type)
        indexed[key] = (
            record.open_time,
            record.close_time,
            record.name,
            record.source_url,
            record.status,
        )
    return indexed


def _public_key(key: tuple) -> dict:
    return {"exchange": key[0], "date": key[1], "event_type": key[2]}


def dataset_metadata(frame: pd.DataFrame, *, retrieved_at: str, fetch_log: list[dict] | None = None) -> dict:
    counts = frame.groupby("exchange").size().to_dict()
    statuses = frame.groupby("status").size().to_dict()
    types = frame.groupby("event_type").size().to_dict()
    windows = {
        code: {"start": spec.start, "end": spec.end, "ec_code": spec.ec_code, "note": spec.note}
        for code, spec in EXCHANGES.items()
    }
    failed_fetches = [item for item in (fetch_log or []) if not item.get("ok")]
    unparsed_pages = [item for item in (fetch_log or []) if item.get("dates_unparsed")]
    open_items = list(OPEN_ITEMS)
    for item in unparsed_pages:
        open_items.append(
            f"Date check could not parse {item.get('url')}: none of the {item.get('dates_expected')} "
            "dataset dates for it appear in a known layout. The page layout must be read by hand."
        )
    for item in failed_fetches:
        open_items.append(
            f"Live re-fetch failed for {item.get('url')}: {item.get('error') or item.get('status')}. "
            "Official rows from the 2026-10-11 snapshot were kept."
        )
    return {
        "dataset_version": f"{retrieved_at[:10]}.{content_hash(frame)}",
        "last_refreshed": retrieved_at,
        "staleness_threshold_days": STALENESS_THRESHOLD_DAYS,
        "history_start": "2001-01-01",
        "row_count": int(len(frame)),
        "counts_by_exchange": {key: int(value) for key, value in counts.items()},
        "counts_by_status": {key: int(value) for key, value in statuses.items()},
        "counts_by_event_type": {key: int(value) for key, value in types.items()},
        "libraries": {
            "exchange_calendars": "4.13.2",
            "pandas_market_calendars": "5.5.0",
        },
        "cme_base_calendar": "CMES",
        "official_snapshot_parsed_on": OFFICIAL_SNAPSHOT_DATE,
        "windows": windows,
        "open_items": open_items,
        "fetch_failures": failed_fetches,
    }


def write_json(path, payload) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
