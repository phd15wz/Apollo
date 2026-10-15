"""Data-quality checks: staleness, past-date diffs, and unresolved disagreements."""

from __future__ import annotations

import hashlib
import json
import warnings
from datetime import datetime, timezone

import pandas as pd

from tools.exchange_calendar.exchanges import ALIASES, EXCHANGES
from tools.exchange_calendar.settlement_upgrades import TIER_B_TERMINAL_SOURCE

# source_url is normally a page URL. Licensed terminal calendars have no URL;
# the exact label below is the only non-URL provenance the quality checks allow.
ALLOWED_NON_URL_SOURCES = frozenset({TIER_B_TERMINAL_SOURCE})


def source_is_allowed(value: str) -> bool:
    """True when source_url is an http(s) URL or an allowed tier-B source label."""
    if value.startswith("https://") or value.startswith("http://"):
        return True
    return value in ALLOWED_NON_URL_SOURCES

CONTENT_COLUMNS = [
    "exchange",
    "date",
    "event_type",
    "open_time",
    "close_time",
    "name",
    "source_url",
    "status",
    "note",
]

STALENESS_THRESHOLD_DAYS = 35
OFFICIAL_SNAPSHOT_DATE = "2026-10-11"

OPEN_ITEMS = [
    (
        "KRX has no parsed official holiday list. open.krx.co.kr and the KIND market "
        "calendar returned page shells without dates. pandas_market_calendars XKRX is a "
        "mirror of exchange_calendars, so library agreement is not a second source. "
        "News rows Isaac marked secondary-confirmed (and the 2026-06-03 local-election "
        "and 2026-07-17 Constitution Day closures those articles add) are "
        "secondary-confirmed. Weaker-B market-wrap rows stay B and are flagged in "
        "note. Unverified KRX full_closure rows that match the licensed Bloomberg "
        "settlement calendar (retrieved 2026-10-13) are secondary-confirmed. Year-end "
        "KRX closures that calendar does not list, and KRX late opens, stay unverified. "
        "2027 and later are omitted: the exchange announces that year around December, "
        "and library projections through 2050 were not copied."
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
        "CME rows describe CME equity index futures (Globex) hours only. Do not use "
        "them for interest-rate, FX, or crypto futures until a product column exists. "
        "The dataset uses exchange_calendars CMES. Native pandas_market_calendars "
        "CME_Equity and CME Globex Equity disagree on some full closures (several Good "
        "Fridays) and on early-close sets. Sourced overrides from the 2026-10-14 round: "
        "2001-09-11 and 2001-09-12 are secondary-confirmed full closures from fetched "
        "Mondovisione CME-release mirrors; 2001-09-13 stays unverified. Short "
        "equity-index Globex sessions are ``early_close`` (previous-evening 17:00 CT "
        "open): official 2018-12-05 (08:30); secondary-confirmed 2004-06-11 (08:00), "
        "2007-01-02, 2010-04-02, 2012-04-06, 2015-04-03 (CNBC), 2021-04-02 "
        "(Wayback MarketWatch 9:15 ET plus CME advisory), 2023-04-07 "
        "(Wayback MarketWatch), 2026-04-03 (SIFMA 8:15 a.m. CDT plus CME "
        "advisory). 2025-01-09 stays ``other`` (same-day Globex reopen). "
        "Official Good Friday full closures from fetched CME clearing "
        "advisories: 2024-03-29 and 2025-04-18. Remaining disagreed CMES closures stay "
        "unverified, including 2027-03-26. The CME Group holiday page is product-specific "
        "and was not parsed into session rows. CMES and both native CME calendars treat "
        "2001-09-11 through 2001-09-14 as sessions. The CME early-close clocks for "
        "2026-11-27, 2026-12-24, and 2027-11-26 are 12:15 per CME Globex Equity and 12:00 "
        "per CMES and CME_Equity; they are unverified. The Juneteenth 2027 early close at "
        "12:00 is unverified: the two native calendars show it and CMES does not. The same "
        "native pattern holds for 2026-06-19, and earlier Juneteenth dates were not checked "
        "and are not added. urllib of cmegroup.com returned HTTP 403 in this run; WebFetch "
        "retrieved several CME notices. Close clocks for 2024-11-29, 2024-12-24, and "
        "2025-11-28 stay at the CMES 12:00 value and are unverified; they were not changed "
        "from broker (tier C) 12:15 reports and need a CME tier-A check."
    ),
    (
        "Nasdaq is an alias of NYSE. Both libraries implement XNAS / NASDAQ as the NYSE "
        "calendar classes, and there is no independent Nasdaq holiday dataset. Queries for "
        "Nasdaq return NYSE rows. The Nasdaq Trader 2026 holiday page is still fetched as a "
        "check that Nasdaq's announced closures and 1:00 p.m. early closes equal NYSE 2026; "
        "a mismatch is logged loudly and is not written as Nasdaq rows."
    ),
    (
        "HKEX regular holidays were not fetched as an annual calendar. Closures are "
        "secondary-confirmed only where exchange_calendars XHKG and pandas_market_calendars "
        "HKEX agree. Early closes exist in exchange_calendars (12:00 HKT) and not in the "
        "native HKEX calendar, so those rows are unverified. 2021-04-06 is closed in "
        "XHKG and open in native HKEX; no official notice was fetched. The licensed "
        "Bloomberg settlement calendar (retrieved 2026-10-13) confirms a full closure, "
        "so the row is secondary-confirmed. Morning-only severe-weather sessions that both libraries comment out "
        "are not in the dataset. HKEX Severe Weather Trading, in effect from 23 September "
        "2024, is not modeled. HKEX typhoon_closure history starts at 2016-08-02 and is "
        "incomplete before 2016: no pre-2016 notice was fetched, and no typhoon notice "
        "between 2016 and 2024 is in the dataset unless it was fetched. A missing typhoon row "
        "does not mean the market traded that day."
    ),
    (
        "Date checks on the SSE English schedule page and the SSE 2026 notice could not run in "
        "this snapshot: both timed out during the TLS handshake. Their SSE rows keep their "
        "snapshot status until a refresh can read the pages and check the dates."
    ),
    (
        "Historical official archives before the pages in the official snapshot were not "
        "fetched. A refresh re-checks those URLs and rebuilds the library base. It does "
        "not scrape a new December SSE or KRX notice; that notice has to be added to "
        "tools/exchange_calendar/official.py after it is read."
    ),
    (
        "HKEX half-days 2025-12-24, 2025-12-31, and 2026-02-16 are official from circular "
        "CT/166/2025 (no afternoon session) plus the securities trading-hours half-day session "
        "(09:30-12:00). The circular does not print 12:00. 2024-12-31 and 2025-01-28 are "
        "secondary-confirmed from news that names a half-day or shortened session but not the "
        "clock. Other HKEX early closes stay unverified. The trading-hours page still lists no "
        "dates. Live urllib of the CT/166/2025 PDF returns binary; phrase/date checks may not "
        "parse it."
    ),
    (
        "CME native early closes outside CMES are not in the dataset. CME_Equity and CME Globex "
        "Equity show 12:00 or 12:15 closes on sessions that CMES treats as normal days, for "
        "example Juneteenth 2022 through 2026 and some Good Fridays. Those sessions have no row, "
        "so no disagreement is recorded for them. The 2027 Juneteenth row is the only one added."
    ),
    (
        "CME 2001-09-11 and 2001-09-12 are secondary-confirmed from fetched Mondovisione "
        "copies of CME releases (tier B, not official). 2001-09-13 remains unverified: the "
        "resume notice says non-equity reopens that morning and equity-index no earlier than "
        "2001-09-14, which does not by itself type 2001-09-13. The fetched Wikipedia article "
        "says the US stock exchanges remained closed until 2001-09-17, which does not match the "
        "reported reopening of securities markets on 2001-09-14. NYSE rows for "
        "2001-09-14 stay full closures, because the libraries and the fetched article agree on "
        "the NYSE closure. Nasdaq queries use those NYSE rows. urllib of CME Group pages "
        "returned HTTP 403; several notices were read with WebFetch on 2026-10-14."
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
        "aliases": dict(ALIASES),
        "windows": windows,
        "weaker_b_rows": [
            {"exchange": row.exchange, "date": row.date, "note": row.note}
            for row in frame.itertuples(index=False)
            if "weaker B" in getattr(row, "note", "")
        ],
        "needs_cme_tier_a": [
            row.date
            for row in frame.itertuples(index=False)
            if row.exchange == "CME" and "Needs a CME tier-A check" in getattr(row, "note", "")
        ],
        "open_items": open_items,
        "fetch_failures": failed_fetches,
    }


def write_json(path, payload) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
