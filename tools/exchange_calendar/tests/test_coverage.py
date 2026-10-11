"""Lock the coverage claims in docs/exchange-calendar-coverage.md to a fresh probe."""

import exchange_calendars as ec
import pandas as pd
import pandas_market_calendars as mcal
import pytest


def _weekday_closures(sessions, start, end) -> set[str]:
    session_days = set(pd.DatetimeIndex(sessions).tz_localize(None).strftime("%Y-%m-%d"))
    return {
        day.strftime("%Y-%m-%d")
        for day in pd.bdate_range(start, end)
        if day.strftime("%Y-%m-%d") not in session_days
    }


def _ec_closed(code, start, end) -> set[str]:
    calendar = ec.get_calendar(code, start=start, end=end)
    return _weekday_closures(calendar.sessions, start, end)


def _mcal_closed(code, start, end) -> set[str]:
    calendar = mcal.get_calendar(code)
    sessions = calendar.schedule(start, end).index
    return _weekday_closures(sessions, start, end)


def test_nasdaq_is_an_alias_of_nyse():
    assert type(ec.get_calendar("XNAS")).__name__ == "XNYSExchangeCalendar"
    assert type(ec.get_calendar("XNYS")).__name__ == "XNYSExchangeCalendar"
    assert type(mcal.get_calendar("NASDAQ")).__name__ == "NYSEExchangeCalendar"
    left = ec.get_calendar("XNYS", start="2000-01-01", end="2027-12-31").sessions
    right = ec.get_calendar("XNAS", start="2000-01-01", end="2027-12-31").sessions
    assert left.equals(right)


def test_sse_and_jpx_bounds():
    with pytest.raises(ValueError, match="2026"):
        ec.get_calendar("XSHG", start="2026-01-01", end="2027-01-01")
    with pytest.raises(ValueError, match="1997-01-01"):
        ec.get_calendar("XTKS", start="1996-01-01", end="2000-01-01")


def test_xhkg_misses_2023_09_08_and_native_hkex_has_it():
    calendar = ec.get_calendar("XHKG", start="2023-09-01", end="2023-09-08")
    assert calendar.is_session("2023-09-08")
    native = mcal.get_calendar("HKEX").schedule("2023-09-01", "2023-09-08")
    assert "2023-09-08" not in set(native.index.strftime("%Y-%m-%d"))


def test_closure_counts_and_hkex_diff_for_2001_through_2026():
    start, end = "2001-01-01", "2026-12-31"
    pairs = {
        "NYSE": ("XNYS", "NYSE", 246),
        "SSE": ("XSHG", "SSE", 480),
        "JPX": ("XTKS", "JPX", 419),
    }
    for _label, (ec_code, mcal_code, count) in pairs.items():
        left = _ec_closed(ec_code, start, end)
        right = _mcal_closed(mcal_code, start, end)
        assert len(left) == count
        assert left == right

    xhkg = _ec_closed("XHKG", start, end)
    hkex = _mcal_closed("HKEX", start, end)
    assert len(xhkg) == 375
    assert len(hkex) == 373
    assert xhkg - hkex == {"2020-10-13", "2021-04-06", "2021-10-13", "2024-09-06"}
    assert hkex - xhkg == {"2023-09-01", "2023-09-08"}


def test_cme_was_open_on_2001_09_11_in_all_three_calendars():
    assert ec.get_calendar("CMES", start="2001-09-01", end="2001-09-30").is_session("2001-09-11")
    for code in ("CME_Equity", "CME Globex Equity"):
        sessions = set(mcal.get_calendar(code).schedule("2001-09-10", "2001-09-17").index.strftime("%Y-%m-%d"))
        assert "2001-09-11" in sessions


def test_krx_mirror_does_not_apply_the_late_open():
    calendar = mcal.get_calendar("XKRX")
    # The mirror class lives in pandas_market_calendars.calendars.mirror.
    assert calendar.__class__.__module__.endswith("mirror")
    schedule = calendar.schedule("2024-01-02", "2024-01-02")
    opened = schedule.iloc[0]["market_open"].tz_convert("Asia/Seoul")
    assert opened.strftime("%H:%M") == "09:00"
    xkrx = ec.get_calendar("XKRX", start="2024-01-02", end="2024-01-03")
    local = xkrx.schedule.iloc[0]["open"].tz_convert("Asia/Seoul")
    assert local.strftime("%H:%M") == "10:00"
    assert len(xkrx.late_opens) == 0
