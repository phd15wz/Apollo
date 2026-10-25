"""refresh_once with injected FRED and Yahoo clients. No live HTTP."""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tools.macro.cache import CachedPrint, load_series, save_series
from tools.macro.refresh import OutsidePullWindow, main, refresh_once
from tools.macro.series import DXY_TICKER, FRED_DEFAULT_OBSERVATION_START, FRED_SERIES

NY = ZoneInfo("America/New_York")
FAKE_KEY = "test-fred-key"
NOW = datetime(2025, 11, 26, 17, 0, tzinfo=NY)


def _print(series_id: str, obs: date, level: float, available: datetime) -> CachedPrint:
    if series_id == DXY_TICKER:
        return CachedPrint(series_id, obs, level, available, "yahoo", ticker=DXY_TICKER)
    return CachedPrint(series_id, obs, level, available, "fred")


def _stamp(day: date, hour: int = 9, minute: int = 30) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=NY)


def _install_fetchers(
    monkeypatch: pytest.MonkeyPatch, book: dict[str, list[CachedPrint]]
) -> dict[str, dict[str, object]]:
    seen: dict[str, dict[str, object]] = {}

    def fetch_fred(series_id: str, *, fetched_at: datetime, **kwargs: object) -> list[CachedPrint]:
        seen[series_id] = {"fetched_at": fetched_at, **kwargs}
        return book[series_id]

    def fetch_dxy() -> list[CachedPrint]:
        seen[DXY_TICKER] = {"fetched_at": NOW}
        return book[DXY_TICKER]

    monkeypatch.setattr("tools.macro.refresh.fetch_observations", fetch_fred)
    monkeypatch.setattr("tools.macro.refresh.fetch_dxy_history", fetch_dxy)
    monkeypatch.setattr("tools.macro.refresh.is_fred_pull_time", lambda when, **kwargs: True)
    return seen


def _consistent_book() -> dict[str, list[CachedPrint]]:
    visible_days = [date(2025, 11, 24), date(2025, 11, 25)]
    hidden = date(2025, 11, 26)
    days = visible_days + [hidden]
    dgs10 = [4.0, 4.25, 4.5]
    dgs2 = [3.0, 3.25, 3.5]
    slope = [1.0, 1.0, 1.0]
    real = [1.5, 1.55, 1.6]
    stamps = [_stamp(day) for day in visible_days] + [_stamp(date(2025, 11, 28))]
    return {
        "DGS10": [_print("DGS10", day, level, stamp) for day, level, stamp in zip(days, dgs10, stamps)],
        "DGS2": [_print("DGS2", day, level, stamp) for day, level, stamp in zip(days, dgs2, stamps)],
        "T10Y2Y": [_print("T10Y2Y", day, level, stamp) for day, level, stamp in zip(days, slope, stamps)],
        "DFII10": [_print("DFII10", day, level, stamp) for day, level, stamp in zip(days, real, stamps)],
        DXY_TICKER: [_print(DXY_TICKER, date(2025, 11, 25), 104.2, _stamp(date(2025, 11, 25), 16, 0))],
    }


def test_happy_path_hides_the_same_day_print_and_keeps_research_off_the_gate(tmp_path: Path) -> None:
    summary = refresh_once(
        now=NOW,
        cache_dir=tmp_path,
        fetch_fred=lambda series_id, **kwargs: _consistent_book()[series_id],
        fetch_dxy=lambda: _consistent_book()[DXY_TICKER],
    )
    assert summary.gate is False
    by_id = {item.series_id: item for item in summary.series}
    assert by_id["DGS10"].latest_obs_date == date(2025, 11, 25)
    assert by_id["DGS10"].latest_level == 4.25
    assert by_id["DGS10"].short_sample is True
    assert by_id["DGS10"].sharp_move is False
    assert by_id[DXY_TICKER].sharp_move is False
    stored = load_series(tmp_path, "DGS10")
    assert [row.level for row in stored] == [4.0, 4.25, 4.5]
    text = (tmp_path / "series" / "DGS10.json").read_text(encoding="utf-8")
    assert FAKE_KEY not in text
    assert "<html" not in text
    names = sorted(path.name for path in (tmp_path / "series").glob("*.json"))
    assert names == ["DFII10.json", "DGS10.json", "DGS2.json", "DX-Y.NYB.json", "T10Y2Y.json"]


def test_replaced_level_keeps_the_original_available_at(tmp_path: Path) -> None:
    original = _stamp(date(2025, 11, 26))
    save_series(tmp_path, "DGS10", [_print("DGS10", date(2025, 11, 25), 4.0, original)])
    book = _consistent_book()
    later = _stamp(date(2025, 12, 1))
    book["DGS10"] = [_print("DGS10", date(2025, 11, 25), 4.25, later)]
    book["DGS2"] = [_print("DGS2", date(2025, 11, 25), 3.25, later)]
    book["T10Y2Y"] = [_print("T10Y2Y", date(2025, 11, 25), 1.0, later)]
    book["DFII10"] = [_print("DFII10", date(2025, 11, 25), 1.5, later)]
    refresh_once(
        now=NOW,
        cache_dir=tmp_path,
        fetch_fred=lambda series_id, **kwargs: book[series_id],
        fetch_dxy=lambda: book[DXY_TICKER],
    )
    stored = load_series(tmp_path, "DGS10")
    assert stored[0].level == 4.25
    assert stored[0].available_at == original


def test_single_bad_obs_date_is_skipped_logged_and_still_written(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One date outside the 1 bp band is dropped. The refresh still writes the rest.

    1990-11-21 is the known 2 bp repro symptom. The check is the same for every date.
    """
    caplog.set_level(logging.WARNING, logger="tools.macro.refresh")
    cached_good = date(2024, 1, 2)
    cached_stamp = _stamp(date(2024, 1, 3))
    bad = date(1990, 11, 21)
    bad_stamp = _stamp(date(1990, 11, 23))
    save_series(
        tmp_path,
        "DGS10",
        [
            _print("DGS10", bad, 8.40, bad_stamp),
            _print("DGS10", cached_good, 4.10, cached_stamp),
        ],
    )
    save_series(
        tmp_path,
        "DGS2",
        [
            _print("DGS2", bad, 7.50, bad_stamp),
            _print("DGS2", cached_good, 3.10, cached_stamp),
        ],
    )
    save_series(
        tmp_path,
        "T10Y2Y",
        [
            _print("T10Y2Y", bad, 0.92, bad_stamp),
            _print("T10Y2Y", cached_good, 1.00, cached_stamp),
        ],
    )
    save_series(
        tmp_path,
        "DFII10",
        [
            _print("DFII10", bad, 2.00, bad_stamp),
            _print("DFII10", cached_good, 1.80, cached_stamp),
        ],
    )

    edge = date(2025, 11, 24)
    kept = date(2025, 11, 25)
    also_bad = date(2025, 11, 26)
    days = [edge, kept, also_bad]
    # edge is exactly 1 bp (kept). also_bad is 2 bp (skipped). bad is only in the cache.
    fetched = {
        "DGS10": [4.00, 4.25, 4.50],
        "DGS2": [3.00, 3.25, 3.50],
        "T10Y2Y": [1.00 + 0.01, 1.00, 1.02],
        "DFII10": [1.50, 1.55, 1.60],
    }
    starts: list[date] = []

    def fetch_fred(series_id: str, *, fetched_at: datetime, observation_start: date) -> list[CachedPrint]:
        assert fetched_at == NOW
        starts.append(observation_start)
        return [
            _print(series_id, day, level, _stamp(day))
            for day, level in zip(days, fetched[series_id])
        ]

    def fetch_dxy() -> list[CachedPrint]:
        return [_print(DXY_TICKER, kept, 104.2, _stamp(kept, 16, 0))]

    summary = refresh_once(
        now=NOW,
        cache_dir=tmp_path,
        observation_start=date(1992, 1, 1),
        fetch_fred=fetch_fred,
        fetch_dxy=fetch_dxy,
    )
    assert starts == [date(1992, 1, 1)] * len(FRED_SERIES)
    assert [gap.obs_date for gap in summary.skipped] == [bad, also_bad]
    assert summary.skipped[0].gap == pytest.approx(0.02)
    assert summary.skipped[0].tolerance == 0.01

    text = caplog.text
    assert "1990-11-21: skipping obs_date" in text
    assert "2025-11-26: skipping obs_date" in text
    assert "series DGS10=8.400000 DGS2=7.500000 T10Y2Y=0.920000" in text
    assert "observed T10Y2Y=0.920000" in text
    assert "expected DGS10-DGS2=0.900000" in text
    assert "tolerance=0.01 (1 bp)" in text
    assert "refresh stopped" not in text

    def dates_of(series_id: str) -> list[date]:
        return [row.obs_date for row in load_series(tmp_path, series_id)]

    assert dates_of("DGS10") == [cached_good, edge, kept]
    assert dates_of("DGS2") == [cached_good, edge, kept]
    assert dates_of("T10Y2Y") == [cached_good, edge, kept]
    assert dates_of("DFII10") == [bad, cached_good, edge, kept, also_bad]
    assert dates_of(DXY_TICKER) == [kept]
    stored_good = next(row for row in load_series(tmp_path, "DGS10") if row.obs_date == cached_good)
    assert stored_good.level == 4.10
    assert stored_good.available_at == cached_stamp
    assert (tmp_path / "series" / "DGS2.json").exists()
    assert (tmp_path / "series" / "T10Y2Y.json").exists()


def test_weekend_pull_does_not_fetch_unless_backfill(tmp_path: Path) -> None:
    called: list[str] = []

    def fetch_fred(series_id: str, **kwargs: object) -> list[CachedPrint]:
        called.append(series_id)
        assert kwargs["observation_start"] == FRED_DEFAULT_OBSERVATION_START
        return []

    saturday = datetime(2025, 11, 29, 18, 0, tzinfo=NY)
    with pytest.raises(OutsidePullWindow):
        refresh_once(now=saturday, cache_dir=tmp_path, fetch_fred=fetch_fred, fetch_dxy=lambda: [])
    assert called == []
    refresh_once(
        now=saturday,
        cache_dir=tmp_path,
        backfill=True,
        fetch_fred=fetch_fred,
        fetch_dxy=lambda: [],
    )
    assert called == list(FRED_SERIES)


def test_live_gate_uses_only_dgs10_and_dfii10(tmp_path: Path) -> None:
    days: list[date] = []
    cursor = date(2025, 8, 1)
    while len(days) < 42:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    changes = [1.0, -1.0] * 20 + [3.0]
    dgs10_levels = [4.0]
    for change in changes:
        dgs10_levels.append(dgs10_levels[-1] + change)
    dxy_levels = [100.0]
    for change in changes:
        dxy_levels.append(dxy_levels[-1] + change)

    def rows(series_id: str, levels: list[float]) -> list[CachedPrint]:
        return [_print(series_id, day, level, _stamp(day)) for day, level in zip(days, levels)]

    sharp_book = {
        "DGS10": rows("DGS10", dgs10_levels),
        "DGS2": rows("DGS2", [1.0] * 42),
        "T10Y2Y": rows("T10Y2Y", [level - 1.0 for level in dgs10_levels]),
        "DFII10": rows("DFII10", [2.0] * 42),
        DXY_TICKER: rows(DXY_TICKER, [100.0] * 42),
    }
    summary = refresh_once(
        now=NOW,
        cache_dir=tmp_path,
        fetch_fred=lambda series_id, **kwargs: sharp_book[series_id],
        fetch_dxy=lambda: sharp_book[DXY_TICKER],
    )
    flags = summary.sharp_flags()
    assert flags["DGS10"] is True
    assert flags["DFII10"] is False
    assert flags["DGS2"] is False
    assert summary.gate is True

    quiet_live = {
        "DGS10": rows("DGS10", [4.0] * 42),
        "DGS2": rows("DGS2", [1.0] * 42),
        "T10Y2Y": rows("T10Y2Y", [3.0] * 42),
        "DFII10": rows("DFII10", [2.0] * 42),
        DXY_TICKER: rows(DXY_TICKER, dxy_levels),
    }
    research = refresh_once(
        now=NOW,
        cache_dir=tmp_path / "research",
        fetch_fred=lambda series_id, **kwargs: quiet_live[series_id],
        fetch_dxy=lambda: quiet_live[DXY_TICKER],
    )
    assert research.sharp_flags()[DXY_TICKER] is True
    assert research.sharp_flags()["DGS10"] is False
    assert research.gate is False


def test_cli_observation_start_reaches_fred_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("FRED_API_KEY", FAKE_KEY)
    book = _consistent_book()
    seen = _install_fetchers(monkeypatch, book)
    assert main(["--cache-dir", str(tmp_path / "default"), "--backfill"]) == 0
    for series_id in FRED_SERIES:
        assert seen[series_id]["observation_start"] == date(1992, 1, 1)
    assert "observation_start" not in seen[DXY_TICKER]

    captured: dict[str, dict[str, object]] = {}

    def fetch_fred(series_id: str, *, fetched_at: datetime, observation_start: date) -> list[CachedPrint]:
        captured[series_id] = {"fetched_at": fetched_at, "observation_start": observation_start}
        return book[series_id]

    def fetch_dxy() -> list[CachedPrint]:
        captured[DXY_TICKER] = {}
        return book[DXY_TICKER]

    monkeypatch.setattr("tools.macro.refresh.fetch_observations", fetch_fred)
    monkeypatch.setattr("tools.macro.refresh.fetch_dxy_history", fetch_dxy)
    monkeypatch.setattr("tools.macro.refresh.is_fred_pull_time", lambda when, **kwargs: True)
    assert (
        main(
            [
                "--cache-dir",
                str(tmp_path / "bounded"),
                "--observation-start",
                "1995-06-01",
            ]
        )
        == 0
    )
    assert {series_id: captured[series_id]["observation_start"] for series_id in FRED_SERIES} == {
        series_id: date(1995, 6, 1) for series_id in FRED_SERIES
    }
    assert captured[DXY_TICKER] == {}
    with pytest.raises(SystemExit) as exc:
        main(["--cache-dir", str(tmp_path / "bad-date"), "--observation-start", "21-11-1990"])
    assert exc.value.code == 2
    captured_io = capsys.readouterr()
    assert FAKE_KEY not in captured_io.err
    assert FAKE_KEY not in captured_io.out


def test_cli_success_spread_failure_and_backfill(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("FRED_API_KEY", FAKE_KEY)
    book = _consistent_book()
    seen = _install_fetchers(monkeypatch, book)
    caplog.set_level(logging.WARNING, logger="tools.macro.refresh")
    assert main(["--cache-dir", str(tmp_path / "ok")]) == 0
    captured = capsys.readouterr()
    assert "gate=off" in captured.out
    assert FAKE_KEY not in captured.out
    assert FAKE_KEY not in captured.err
    assert seen["DGS10"]["fetched_at"] == seen["DFII10"]["fetched_at"]
    assert seen["DGS10"]["observation_start"] == FRED_DEFAULT_OBSERVATION_START
    assert "observation_start" not in seen[DXY_TICKER]

    book["T10Y2Y"] = [_print("T10Y2Y", row.obs_date, 0.5, row.available_at) for row in book["DGS10"]]
    _install_fetchers(monkeypatch, book)
    assert main(["--cache-dir", str(tmp_path / "bad")]) == 0
    captured = capsys.readouterr()
    assert "gate=" in captured.out
    assert "refresh stopped" not in captured.err
    assert "refresh stopped" not in caplog.text
    assert "skipping obs_date" in caplog.text
    assert "tolerance=0.01" in caplog.text
    assert "DGS10" in caplog.text and "DGS2" in caplog.text and "T10Y2Y" in caplog.text
    assert FAKE_KEY not in captured.err
    assert FAKE_KEY not in caplog.text
    assert load_series(tmp_path / "bad", "DGS10") == []
    assert load_series(tmp_path / "bad", "DGS2") == []
    assert load_series(tmp_path / "bad", "T10Y2Y") == []
    assert [row.obs_date for row in load_series(tmp_path / "bad", "DFII10")] == [
        date(2025, 11, 24),
        date(2025, 11, 25),
        date(2025, 11, 26),
    ]
    assert load_series(tmp_path / "bad", DXY_TICKER)

    def leak(series_id: str, **kwargs: object) -> list[CachedPrint]:
        raise RuntimeError(f"blew up with {FAKE_KEY}")

    monkeypatch.setattr("tools.macro.refresh.fetch_observations", leak)
    monkeypatch.setattr("tools.macro.refresh.is_fred_pull_time", lambda when, **kwargs: True)
    assert main(["--cache-dir", str(tmp_path / "leak")]) == 1
    captured = capsys.readouterr()
    assert FAKE_KEY not in captured.err
    assert "[redacted]" in captured.err

    called: list[str] = []

    def fetch_fred(series_id: str, **kwargs: object) -> list[CachedPrint]:
        called.append(series_id)
        return []

    monkeypatch.setattr("tools.macro.refresh.fetch_observations", fetch_fred)
    monkeypatch.setattr("tools.macro.refresh.fetch_dxy_history", lambda: [])
    monkeypatch.setattr("tools.macro.refresh.is_fred_pull_time", lambda when, **kwargs: False)
    assert main(["--cache-dir", str(tmp_path / "closed")]) == 1
    assert called == []
    assert main(["--cache-dir", str(tmp_path / "backfill"), "--backfill"]) == 0
    assert called == list(FRED_SERIES)
