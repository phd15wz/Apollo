"""FRED available_at uses confirmed NYSE sessions, not the raw holiday file."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from tools.macro.availability import (
    dxy_available_at,
    fred_available_at,
    is_fred_pull_time,
    is_nyse_session,
    next_nyse_session,
    nyse_full_closure_dates,
    select_visible_indices,
)

NY = ZoneInfo("America/New_York")


def test_next_session_skips_weekends_and_full_closures() -> None:
    closures = {date(2025, 11, 27)}
    assert next_nyse_session(date(2025, 11, 26), closures) == date(2025, 11, 28)
    assert next_nyse_session(date(2025, 11, 28), closures) == date(2025, 12, 1)
    assert is_nyse_session(date(2025, 11, 28), closures)
    assert not is_nyse_session(date(2025, 11, 27), closures)
    assert not is_nyse_session(date(2025, 11, 29), closures)


def test_unverified_and_early_close_do_not_remove_a_session() -> None:
    frame = pd.DataFrame(
        [
            {
                "exchange": "NYSE",
                "date": "2026-05-25",
                "event_type": "full_closure",
                "status": "unverified",
            },
            {
                "exchange": "NYSE",
                "date": "2026-05-26",
                "event_type": "early_close",
                "status": "official",
            },
            {
                "exchange": "NYSE",
                "date": "2026-05-27",
                "event_type": "full_closure",
                "status": "pending",
            },
            {
                "exchange": "NYSE",
                "date": "2026-05-28",
                "event_type": "full_closure",
                "status": "official",
            },
            {
                "exchange": "CME",
                "date": "2026-05-25",
                "event_type": "full_closure",
                "status": "official",
            },
        ]
    )
    closures = nyse_full_closure_dates(frame)
    assert closures == {date(2026, 5, 28)}
    # 2026-05-22 is a Friday. Monday stays open: its closure is unverified.
    assert date(2026, 5, 22).weekday() == 4
    assert next_nyse_session(date(2026, 5, 22), closures) == date(2026, 5, 25)
    assert is_nyse_session(date(2026, 5, 26), closures)
    assert next_nyse_session(date(2026, 5, 27), closures) == date(2026, 5, 29)


def test_unknown_status_is_not_treated_as_closed() -> None:
    frame = pd.DataFrame(
        [
            {
                "exchange": "NYSE",
                "date": "2026-05-28",
                "event_type": "full_closure",
                "status": "guessed",
            }
        ]
    )
    with pytest.raises(ValueError, match="unknown status"):
        nyse_full_closure_dates(frame)


def test_real_calendar_keeps_thanksgiving_friday_early_close() -> None:
    closures = nyse_full_closure_dates()
    assert date(2025, 11, 27) in closures
    assert date(2025, 11, 28) not in closures
    fetched = datetime(2025, 11, 26, 17, 0, tzinfo=NY)
    available = fred_available_at(date(2025, 11, 26), fetched)
    assert available == datetime(2025, 11, 28, 9, 30, tzinfo=NY)


def test_july_fourth_stamp_is_the_next_cash_open_in_edt() -> None:
    fetched = datetime(2025, 7, 3, 17, 0, tzinfo=NY)
    available = fred_available_at(date(2025, 7, 3), fetched, closures={date(2025, 7, 4)})
    assert available == datetime(2025, 7, 7, 9, 30, tzinfo=NY)
    assert available.utcoffset() == timedelta(hours=-4)


def test_late_fetch_keeps_the_fetch_time() -> None:
    closures = {date(2025, 7, 4)}
    on_the_open = datetime(2025, 7, 7, 9, 30, tzinfo=NY)
    assert fred_available_at(date(2025, 7, 3), on_the_open, closures=closures) == on_the_open
    late = datetime(2025, 7, 7, 9, 31, tzinfo=NY)
    assert fred_available_at(date(2025, 7, 3), late, closures=closures) == late


def test_pull_window_is_sixteen_fifteen_on_a_session() -> None:
    closures: set[date] = set()
    assert is_fred_pull_time(datetime(2025, 11, 26, 16, 15, tzinfo=NY), closures=closures)
    assert not is_fred_pull_time(datetime(2025, 11, 26, 16, 14, 59, tzinfo=NY), closures=closures)
    assert not is_fred_pull_time(datetime(2025, 11, 29, 18, 0, tzinfo=NY), closures=closures)
    assert not is_fred_pull_time(
        datetime(2025, 11, 27, 16, 15, tzinfo=NY),
        closures={date(2025, 11, 27)},
    )
    # Early close is still a pull day when it is not in the closure set.
    assert is_fred_pull_time(datetime(2025, 11, 28, 16, 15, tzinfo=NY), closures=closures)
    as_utc = datetime(2025, 11, 26, 16, 15, tzinfo=NY).astimezone(timezone.utc)
    assert is_fred_pull_time(as_utc, closures=closures)
    assert as_utc == datetime(2025, 11, 26, 21, 15, tzinfo=timezone.utc)


def test_real_calendar_pulls_on_the_thanksgiving_early_close() -> None:
    assert not is_fred_pull_time(datetime(2025, 11, 27, 17, 0, tzinfo=NY))
    assert is_fred_pull_time(datetime(2025, 11, 28, 16, 15, tzinfo=NY))


def test_naive_timestamps_raise() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        fred_available_at(date(2025, 11, 26), datetime(2025, 11, 26, 17, 0))
    with pytest.raises(ValueError, match="timezone-aware"):
        dxy_available_at(datetime(2025, 11, 26, 21, 0))


def test_dxy_available_at_is_new_york() -> None:
    stamp = dxy_available_at(datetime(2025, 11, 26, 21, 0, tzinfo=timezone.utc))
    assert stamp == datetime(2025, 11, 26, 16, 0, tzinfo=NY)


def test_no_session_within_a_year_raises() -> None:
    start = date(2026, 1, 5)
    closures = {start + timedelta(days=offset) for offset in range(1, 400)}
    with pytest.raises(LookupError, match="no NYSE session"):
        next_nyse_session(start, closures)


def test_select_visible_includes_the_decision_instant() -> None:
    stamps = [
        datetime(2025, 11, 28, 9, 30, tzinfo=NY),
        datetime(2025, 12, 1, 9, 30, tzinfo=NY),
    ]
    decision = datetime(2025, 11, 28, 9, 30, tzinfo=NY)
    assert select_visible_indices(stamps, decision).tolist() == [0]
    with pytest.raises(ValueError, match="timezone-aware"):
        select_visible_indices(stamps, datetime(2025, 11, 28, 9, 30))
