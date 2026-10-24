"""refresh_once with injected FRED and Yahoo clients. No live HTTP."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tools.macro.cache import CachedPrint, load_series, save_series
from tools.macro.refresh import OutsidePullWindow, main, refresh_once
from tools.macro.series import DXY_TICKER, FRED_SERIES
from tools.macro.transforms import SpreadMismatch

NY = ZoneInfo("America/New_York")
FAKE_KEY = "test-fred-key"
NOW = datetime(2025, 11, 26, 17, 0, tzinfo=NY)


def _print(series_id: str, obs: date, level: float, available: datetime) -> CachedPrint:
    if series_id == DXY_TICKER:
        return CachedPrint(series_id, obs, level, available, "yahoo", ticker=DXY_TICKER)
    return CachedPrint(series_id, obs, level, available, "fred")


def _stamp(day: date, hour: int = 9, minute: int = 30) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=NY)


def _install_fetchers(monkeypatch: pytest.MonkeyPatch, book: dict[str, list[CachedPrint]]) -> dict[str, datetime]:
    seen: dict[str, datetime] = {}

    def fetch_fred(series_id: str, *, fetched_at: datetime, **kwargs: object) -> list[CachedPrint]:
        seen[series_id] = fetched_at
        return book[series_id]

    def fetch_dxy() -> list[CachedPrint]:
        seen[DXY_TICKER] = NOW
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


def test_spread_mismatch_aborts_without_writing(tmp_path: Path) -> None:
    kept = _print("DGS10", date(2025, 11, 25), 4.25, _stamp(date(2025, 11, 26)))
    save_series(tmp_path, "DGS10", [kept])
    book = _consistent_book()
    book["T10Y2Y"] = [
        _print("T10Y2Y", row.obs_date, 0.5, row.available_at) for row in book["DGS10"]
    ]
    with pytest.raises(SpreadMismatch, match="refresh stopped"):
        refresh_once(
            now=NOW,
            cache_dir=tmp_path,
            fetch_fred=lambda series_id, **kwargs: book[series_id],
            fetch_dxy=lambda: book[DXY_TICKER],
        )
    assert load_series(tmp_path, "DGS10")[0].level == 4.25
    assert not (tmp_path / "series" / "DGS2.json").exists()
    assert not (tmp_path / "series" / "T10Y2Y.json").exists()


def test_weekend_pull_does_not_fetch_unless_backfill(tmp_path: Path) -> None:
    called: list[str] = []

    def fetch_fred(series_id: str, **kwargs: object) -> list[CachedPrint]:
        called.append(series_id)
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


def test_cli_success_spread_failure_and_backfill(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("FRED_API_KEY", FAKE_KEY)
    book = _consistent_book()
    seen = _install_fetchers(monkeypatch, book)
    assert main(["--cache-dir", str(tmp_path / "ok")]) == 0
    captured = capsys.readouterr()
    assert "gate=off" in captured.out
    assert FAKE_KEY not in captured.out
    assert FAKE_KEY not in captured.err
    assert seen["DGS10"] == seen["DFII10"]

    book["T10Y2Y"] = [_print("T10Y2Y", row.obs_date, 0.5, row.available_at) for row in book["DGS10"]]
    _install_fetchers(monkeypatch, book)
    assert main(["--cache-dir", str(tmp_path / "bad")]) == 1
    captured = capsys.readouterr()
    assert "refresh stopped" in captured.err
    assert FAKE_KEY not in captured.err
    assert not (tmp_path / "bad" / "series").exists()

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
