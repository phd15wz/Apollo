import json

import pytest

from tools.exchange_calendar import refresh as refresh_module
from tools.exchange_calendar.official import OfficialEvent
from tools.exchange_calendar.page_dates import check_page, date_tokens

NYSE = "https://www.nyse.com/markets/hours-calendars"
JPX = "https://www.jpx.co.jp/english/corporate/about-jpx/calendar/"
NOTICE = "https://www.hkex.com.hk/News/Market-Communications/2016/1608022news?sc_lang=en"


def _event(exchange, date, url):
    return OfficialEvent(exchange, date, "full_closure", "Holiday", url)


def test_nyse_layout_requires_the_weekday_that_matches_the_date():
    assert "Thursday, January 1" in date_tokens("2026-01-01", "www.nyse.com")
    events = [_event("NYSE", "2026-01-01", NYSE)]
    assert check_page(NYSE, "New Year’s Day Thursday, January 1 Friday, January 1", events).matched == 1
    wrong = check_page(NYSE, "New Year’s Day Tuesday, January 1 Friday, January 2", events)
    assert wrong.missing == ["2026-01-01"]


def test_jpx_layout_matches_abbreviated_month_and_weekday():
    assert "Jan. 1 (Thu.)" in date_tokens("2026-01-01", "www.jpx.co.jp")
    events = [_event("JPX", "2026-01-01", JPX), _event("JPX", "2026-01-12", JPX)]
    text = "2026 Jan. 1 (Thu.) New Year's Day Jan. 12 (Mon.) Coming of Age Day"
    assert check_page(JPX, text, events).missing == []


def test_list_page_with_no_known_layout_is_unparsed_not_a_disagreement():
    events = [_event("JPX", "2026-01-01", JPX), _event("JPX", "2026-01-12", JPX)]
    check = check_page(JPX, "A page in a layout nobody parsed", events)
    assert check.unparsed
    assert check.missing == []


def test_single_notice_that_does_not_name_its_date_is_a_disagreement():
    events = [_event("HKEX", "2016-08-02", NOTICE)]
    check = check_page(NOTICE, "Typhoon signal news about 9 May 2010", events)
    assert not check.unparsed
    assert check.missing == ["2016-08-02"]


def test_refresh_exits_3_and_writes_no_dataset_when_a_page_disagrees(monkeypatch, tmp_path):
    fake = [
        {
            "url": NOTICE,
            "phrase": "cancelled",
            "ok": True,
            "status": 200,
            "error": "",
            "dates_expected": 1,
            "dates_matched": 0,
            "dates_missing": ["2016-08-02"],
            "dates_unparsed": False,
        }
    ]
    holidays = tmp_path / "holidays.csv"
    monkeypatch.setattr(refresh_module, "fetch_official_pages", lambda: fake)
    monkeypatch.setattr(refresh_module, "HOLIDAYS_PATH", holidays)
    monkeypatch.setattr(refresh_module, "FETCH_LOG_PATH", tmp_path / "fetch-log.json")
    monkeypatch.setattr(refresh_module, "DATA_DIR", tmp_path)
    assert refresh_module.refresh() == 3
    assert not holidays.exists()
    assert json.loads((tmp_path / "fetch-log.json").read_text())[0]["dates_missing"] == ["2016-08-02"]


def test_committed_fetch_log_names_every_checked_date():
    log = json.loads((refresh_module.DATA_DIR / "fetch-log.json").read_text(encoding="utf-8"))
    assert all(not item.get("dates_missing") for item in log)
