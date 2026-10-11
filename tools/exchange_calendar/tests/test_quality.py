"""Data-quality rules for the exchange holiday dataset."""

from datetime import datetime, timezone

import pandas as pd
import pytest

import exchange_calendars as ec

from tools.exchange_calendar.build import (
    CLOSURE_TYPES,
    COLUMNS,
    UNNAMED_CLOSURE,
    _apply_official,
    _holiday_names,
    _native_holiday_names,
    build_dataset,
    library_closure_dates,
    validate_rows,
)
from tools.exchange_calendar.official import OfficialEvent
from tools.exchange_calendar.exchanges import EXCHANGES
from tools.exchange_calendar.quality import (
    STALENESS_THRESHOLD_DAYS,
    diff_datasets,
    staleness_warning,
    unresolved_disagreements,
)

ROOT_DATA = __import__("pathlib").Path(__file__).resolve().parents[3] / "data" / "exchange_calendar"
STATUSES = {"official", "secondary-confirmed", "unverified", "pending"}
EVENT_TYPES = {
    "full_closure",
    "early_close",
    "late_open",
    "typhoon_closure",
    "typhoon_partial",
    "weather_closure",
    "other",
}
WEATHER_TYPES = {"typhoon_closure", "typhoon_partial", "weather_closure"}


@pytest.fixture(scope="module")
def dataset():
    frame = pd.read_csv(ROOT_DATA / "holidays.csv", dtype=str, keep_default_na=False)
    disagreements = pd.read_csv(ROOT_DATA / "disagreements.csv", dtype=str, keep_default_na=False)
    return frame, disagreements


def test_schema_and_required_fields(dataset):
    frame, _ = dataset
    assert list(frame.columns) == COLUMNS
    for column in ("source_url", "retrieved_at", "status"):
        assert frame[column].str.len().gt(0).all()
    assert set(frame["status"]).issubset(STATUSES)
    assert set(frame["event_type"]).issubset(EVENT_TYPES)
    parsed = frame["retrieved_at"].map(lambda value: datetime.fromisoformat(value.replace("Z", "+00:00")))
    assert parsed.notna().all()


def test_no_unresolved_library_official_disagreements(dataset):
    _, disagreements = dataset
    pending = unresolved_disagreements(disagreements)
    assert pending.empty
    # The known HKEX cases are real conflicts that the official notice resolved.
    resolved_dates = set(zip(disagreements["exchange"], disagreements["date"]))
    for date in ("2023-09-01", "2023-09-08", "2020-10-13", "2021-10-13", "2024-09-06"):
        assert ("HKEX", date) in resolved_dates


def test_ten_years_without_year_gaps(dataset):
    frame, _ = dataset
    closures = frame[frame["event_type"].isin(CLOSURE_TYPES)]
    for code, spec in EXCHANGES.items():
        subset = closures[closures["exchange"] == code]
        years = set(subset["date"].str.slice(0, 4).astype(int))
        # At least the past 10 complete years before the 2026-10-11 probe date,
        # and no missing year from 2001 through 2025 inside this exchange's window.
        required = range(2001, 2026)
        end_year = int(spec.end[:4])
        expected = {year for year in required if year <= end_year}
        missing = sorted(expected - years)
        assert not missing, f"{code} missing closure years {missing}"
        assert subset["date"].min() <= "2016-10-11"


def test_library_closures_have_no_gaps(dataset):
    frame, _ = dataset
    for code, spec in EXCHANGES.items():
        expected = library_closure_dates(spec)
        actual = set(
            frame[
                (frame["exchange"] == code) & frame["event_type"].isin(CLOSURE_TYPES)
            ]["date"]
        )
        missing = sorted(expected - actual)
        assert not missing, f"{code} dropped library closures {missing[:10]}"


def test_closure_names_come_from_sources_or_say_unnamed(dataset):
    frame, _ = dataset
    closures = frame[frame["event_type"].isin(CLOSURE_TYPES)]
    assert (closures["name"] != "Closure").all()
    assert closures["name"].str.len().gt(0).all()
    unnamed = closures[closures["name"] == UNNAMED_CLOSURE]
    for code, group in unnamed.groupby("exchange"):
        spec = EXCHANGES[code]
        calendar = ec.get_calendar(spec.ec_code, start=spec.start, end=spec.end)
        library_names = set(_holiday_names(calendar, spec.start, spec.end))
        library_names |= set(_native_holiday_names(spec))
        leaked = sorted(set(group["date"]) & library_names)
        assert not leaked, f"{code} has a library name for unnamed closures {leaked[:5]}"


def test_spot_checks(dataset):
    frame, _ = dataset

    def rows(exchange, date):
        return frame[(frame["exchange"] == exchange) & (frame["date"] == date)]

    for exchange in ("NYSE", "Nasdaq"):
        for date in ("2001-09-11", "2001-09-12", "2001-09-13", "2001-09-14"):
            matched = rows(exchange, date)
            assert len(matched) == 1
            assert matched.iloc[0]["event_type"] == "full_closure"
        assert rows(exchange, "2001-09-10").empty
        assert rows(exchange, "2001-09-17").empty

    cme_2001 = {
        "2001-09-11": ("full_closure", "unverified"),
        "2001-09-12": ("full_closure", "unverified"),
        "2001-09-13": ("other", "unverified"),
    }
    for date, (event_type, status) in cme_2001.items():
        matched = rows("CME", date)
        assert len(matched) == 1, date
        assert matched.iloc[0]["event_type"] == event_type
        assert matched.iloc[0]["status"] == status
    assert rows("CME", "2001-09-14").empty
    christmas = rows("CME", "2001-12-25")
    assert len(christmas) == 1
    assert christmas.iloc[0]["event_type"] == "full_closure"
    assert christmas.iloc[0]["status"] == "secondary-confirmed"

    rainstorm = rows("HKEX", "2023-09-08")
    assert len(rainstorm) == 1
    assert rainstorm.iloc[0]["event_type"] == "weather_closure"
    assert rainstorm.iloc[0]["status"] == "official"
    assert "Black Rainstorm" in rainstorm.iloc[0]["name"]
    assert "hkex.com.hk" in rainstorm.iloc[0]["source_url"]

    saola = rows("HKEX", "2023-09-01")
    assert saola.iloc[0]["event_type"] == "typhoon_closure"
    assert saola.iloc[0]["status"] == "official"

    sse = rows("SSE", "2024-10-01")
    assert sse.iloc[0]["event_type"] == "full_closure"
    assert sse.iloc[0]["status"] == "official"
    assert "sse.com.cn" in sse.iloc[0]["source_url"]

    jpx = rows("JPX", "2020-10-01")
    assert jpx.iloc[0]["event_type"] == "full_closure"
    assert rows("JPX", "2026-12-31").iloc[0]["status"] == "official"

    krx = rows("KRX", "2016-03-01")
    assert krx.iloc[0]["event_type"] == "full_closure"
    assert krx.iloc[0]["status"] == "unverified"


def test_weather_rows_come_from_hkex_notices(dataset):
    frame, _ = dataset
    weather = frame[frame["event_type"].isin(WEATHER_TYPES)]
    assert not weather.empty
    assert set(weather["exchange"]) == {"HKEX"}
    assert set(weather["status"]) == {"official"}
    assert weather["source_url"].str.startswith("https://www.hkex.com.hk/").all()
    typhoon_dates = set(weather[weather["event_type"] == "typhoon_closure"]["date"])
    assert "2023-09-08" not in typhoon_dates
    assert min(typhoon_dates) == "2016-08-02"


def _row(**overrides):
    row = {
        "exchange": "HKEX",
        "date": "2030-01-02",
        "event_type": "typhoon_partial",
        "open_time": "13:00",
        "close_time": "16:00",
        "name": "Typhoon Signal No. 8 lowered before noon",
        "source_url": "https://www.hkex.com.hk/News/example",
        "retrieved_at": "2026-10-11T00:00:00Z",
        "status": "official",
    }
    row.update(overrides)
    return row


def test_typhoon_partial_requires_traded_hours_and_is_not_a_closure():
    assert "typhoon_partial" not in CLOSURE_TYPES
    validate_rows(pd.DataFrame([_row()], columns=COLUMNS))
    with pytest.raises(ValueError, match="missing hours"):
        validate_rows(pd.DataFrame([_row(open_time="")], columns=COLUMNS))


def test_weather_rows_need_an_official_hkex_source():
    with pytest.raises(ValueError, match="official HKEX notice"):
        validate_rows(pd.DataFrame([_row(event_type="weather_closure", open_time="", close_time="", status="unverified")], columns=COLUMNS))
    with pytest.raises(ValueError, match="official HKEX notice"):
        validate_rows(pd.DataFrame([_row(source_url="https://news.example/typhoon")], columns=COLUMNS))
    with pytest.raises(ValueError, match="closure rows with hours"):
        validate_rows(pd.DataFrame([_row(event_type="weather_closure")], columns=COLUMNS))


def test_sse_and_krx_future_year_not_invented(dataset):
    frame, _ = dataset
    future = frame[frame["exchange"].isin(["SSE", "KRX"]) & frame["date"].str.startswith("2027")]
    assert future.empty
    # 2026 itself was already announced and is present.
    for exchange in ("SSE", "KRX"):
        assert frame[(frame["exchange"] == exchange) & frame["date"].str.startswith("2026")].shape[0] > 0


def test_cme_early_close_clocks_and_juneteenth_2027_stay_unverified(dataset):
    frame, _ = dataset
    cme = frame[frame["exchange"] == "CME"]
    for date in ("2026-11-27", "2026-12-24", "2027-11-26"):
        row = cme[cme["date"] == date]
        assert len(row) == 1, date
        assert row.iloc[0]["event_type"] == "early_close"
        assert row.iloc[0]["close_time"] == "12:15"
        assert row.iloc[0]["status"] == "unverified"
    juneteenth = cme[cme["date"] == "2027-06-18"]
    assert len(juneteenth) == 1
    assert juneteenth.iloc[0]["event_type"] == "early_close"
    assert juneteenth.iloc[0]["close_time"] == "12:00"
    assert juneteenth.iloc[0]["status"] == "unverified"
    assert "Juneteenth" in juneteenth.iloc[0]["name"]


def test_hkex_early_closes_use_library_hours_and_stay_unverified(dataset):
    frame, _ = dataset
    early = frame[(frame["exchange"] == "HKEX") & (frame["event_type"] == "early_close")]
    assert len(early) == 52
    assert set(early["status"]) == {"unverified"}
    before = early[early["date"] == "2001-12-24"].iloc[0]
    after = early[early["date"] == "2012-12-24"].iloc[0]
    assert (before["open_time"], before["close_time"]) == ("10:00", "12:30")
    assert (after["open_time"], after["close_time"]) == ("09:30", "12:00")


def test_krx_late_opens_are_unverified_and_never_official(dataset):
    frame, _ = dataset
    late = frame[(frame["exchange"] == "KRX") & (frame["event_type"] == "late_open")]
    assert set(late["status"]) == {"unverified"}
    assert set(late["open_time"]) == {"10:00"}
    assert not frame[(frame["exchange"] == "KRX") & (frame["status"] == "official")].shape[0]


def test_krx_non_january_late_opens_come_from_the_csat_offset(dataset):
    frame, _ = dataset
    late = frame[(frame["exchange"] == "KRX") & (frame["event_type"] == "late_open")]
    non_january = late[~late["date"].str[5:7].eq("01")]
    assert len(non_january) > 0
    assert set(non_january["name"]) == {"CSAT day"}
    assert (non_january["date"] < "2027-01-01").all()


def test_unverified_hkex_library_disagreement_stays_unverified(dataset):
    frame, _ = dataset
    row = frame[(frame["exchange"] == "HKEX") & (frame["date"] == "2021-04-06")]
    assert len(row) == 1
    assert row.iloc[0]["status"] == "unverified"
    assert row.iloc[0]["event_type"] == "full_closure"


def test_staleness_warns_after_35_days():
    old = "2026-01-01T00:00:00Z"
    now = datetime(2026, 10, 11, tzinfo=timezone.utc)
    with pytest.warns(UserWarning, match="stale"):
        assert staleness_warning(old, now=now, threshold_days=STALENESS_THRESHOLD_DAYS)
    fresh = "2026-10-01T00:00:00Z"
    assert staleness_warning(fresh, now=now) is False


def test_committed_metadata_is_not_stale():
    import json

    metadata = json.loads((ROOT_DATA / "metadata.json").read_text(encoding="utf-8"))
    now = datetime(2026, 10, 11, tzinfo=timezone.utc)
    assert metadata["staleness_threshold_days"] == 35
    assert staleness_warning(metadata["last_refreshed"], now=now) is False
    assert metadata["open_items"]


def test_past_date_diff_flags_history_and_ignores_retrieved_at():
    base = pd.DataFrame(
        [
            {
                "exchange": "NYSE",
                "date": "2016-01-01",
                "event_type": "full_closure",
                "open_time": "",
                "close_time": "",
                "name": "New Year's Day",
                "source_url": "https://example.test/old",
                "retrieved_at": "2026-01-01T00:00:00Z",
                "status": "secondary-confirmed",
            },
            {
                "exchange": "NYSE",
                "date": "2027-01-01",
                "event_type": "full_closure",
                "open_time": "",
                "close_time": "",
                "name": "New Year's Day",
                "source_url": "https://example.test/old",
                "retrieved_at": "2026-01-01T00:00:00Z",
                "status": "official",
            },
        ]
    )
    refreshed = base.copy()
    refreshed["retrieved_at"] = "2026-10-11T00:00:00Z"
    only_timestamp = diff_datasets(base, refreshed, as_of="2026-10-11")
    assert only_timestamp["past_change_count"] == 0

    changed = refreshed.copy()
    changed.loc[changed["date"] == "2016-01-01", "name"] = "Changed"
    changed.loc[changed["date"] == "2027-01-01", "name"] = "Future change"
    diff = diff_datasets(base, changed, as_of="2026-10-11")
    assert diff["past_change_count"] == 1
    assert diff["past"]["changed"] == [
        {"exchange": "NYSE", "date": "2016-01-01", "event_type": "full_closure"}
    ]
    assert diff["future"]["changed"][0]["date"] == "2027-01-01"


def test_official_event_replaces_library_rows_of_any_type_on_that_date():
    base = [
        {
            "exchange": "CME",
            "date": "2026-04-03",
            "event_type": "full_closure",
            "open_time": "",
            "close_time": "",
            "name": "Good Friday",
            "source_url": "library",
            "status": "unverified",
        },
        {
            "exchange": "CME",
            "date": "2026-11-27",
            "event_type": "early_close",
            "open_time": "2026-11-26T17:00",
            "close_time": "12:00",
            "name": "Early close",
            "source_url": "library",
            "status": "unverified",
        },
        {
            "exchange": "CME",
            "date": "2026-12-25",
            "event_type": "full_closure",
            "open_time": "",
            "close_time": "",
            "name": "Christmas",
            "source_url": "library",
            "status": "secondary-confirmed",
        },
    ]
    events = [
        OfficialEvent("CME", "2026-04-03", "early_close", "Good Friday", "https://example.test/cme", "2026-04-02T17:00", "08:15"),
        OfficialEvent("CME", "2026-11-27", "early_close", "Day after Thanksgiving", "https://example.test/cme", "2026-11-26T17:00", "12:15"),
    ]
    merged = _apply_official(base, events)
    keys = [(row["date"], row["event_type"]) for row in merged]
    assert len(keys) == len(set(keys))
    by_date = {}
    for row in merged:
        by_date.setdefault(row["date"], []).append(row)
    assert [row["event_type"] for row in by_date["2026-04-03"]] == ["early_close"]
    assert by_date["2026-04-03"][0]["close_time"] == "08:15"
    assert [row["close_time"] for row in by_date["2026-11-27"]] == ["12:15"]
    assert by_date["2026-12-25"][0]["source_url"] == "library"


def test_rebuild_matches_committed_facts():
    """A rebuild may move retrieved_at, but the holiday facts stay put."""
    committed, disagreements = pd.read_csv(
        ROOT_DATA / "holidays.csv", dtype=str, keep_default_na=False
    ), pd.read_csv(ROOT_DATA / "disagreements.csv", dtype=str, keep_default_na=False)
    rebuilt, rebuilt_disagreements = build_dataset(retrieved_at="2026-10-11T00:00:00Z")
    diff = diff_datasets(committed, rebuilt, as_of="2099-01-01")
    assert diff["past_change_count"] == 0
    assert diff["future"]["added"] == []
    assert diff["future"]["removed"] == []
    assert diff["future"]["changed"] == []
    left = disagreements.fillna("").sort_values(list(disagreements.columns)).reset_index(drop=True)
    right = rebuilt_disagreements.fillna("").sort_values(list(rebuilt_disagreements.columns)).reset_index(drop=True)
    pd.testing.assert_frame_equal(left, right)
