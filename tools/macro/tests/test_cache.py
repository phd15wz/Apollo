"""Cache path, vintage available_at, and the refusal to write inside the repo."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tools.macro.cache import (
    CachedPrint,
    assert_outside_repo,
    default_data_dir,
    load_series,
    save_series,
    upsert_print,
)
from tools.macro.series import DXY_TICKER

NY = ZoneInfo("America/New_York")
REPO = Path(__file__).resolve().parents[3]


def _fred(obs: date, level: float, available: datetime) -> CachedPrint:
    return CachedPrint(
        series_id="DGS10",
        obs_date=obs,
        level=level,
        available_at=available,
        source="fred",
    )


def test_default_dir_follows_macro_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MACRO_DATA_DIR", str(tmp_path / "custom"))
    assert default_data_dir() == (tmp_path / "custom").resolve()

    monkeypatch.setenv("MACRO_DATA_DIR", "  ")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert default_data_dir() == (tmp_path / ".cache" / "apollo" / "macro").resolve()


def test_upsert_keeps_the_first_available_at() -> None:
    first_at = datetime(2025, 11, 28, 9, 30, tzinfo=NY)
    later = datetime(2025, 12, 2, 11, 0, tzinfo=NY)
    original = _fred(date(2025, 11, 26), 4.25, first_at)
    revised = _fred(date(2025, 11, 26), 4.33, later)
    merged = upsert_print([original], revised)
    assert len(merged) == 1
    assert merged[0].level == 4.33
    assert merged[0].available_at == first_at
    assert merged[0].source == "fred"
    assert merged[0].ticker is None


def test_roundtrip_outside_the_repo(tmp_path: Path) -> None:
    stamp = datetime(2025, 11, 28, 9, 30, tzinfo=NY)
    row = _fred(date(2025, 11, 26), 4.25, stamp)
    path = save_series(tmp_path, "DGS10", [row])
    assert path == (tmp_path / "series" / "DGS10.json").resolve()
    assert not path.with_suffix(".json.tmp").exists()
    loaded = load_series(tmp_path, "DGS10")
    assert loaded[0].level == 4.25
    assert loaded[0].obs_date == date(2025, 11, 26)
    assert loaded[0].available_at == stamp
    assert load_series(tmp_path, "DGS2") == []


def test_dxy_row_stores_the_locked_ticker(tmp_path: Path) -> None:
    stamp = datetime(2025, 11, 26, 16, 0, tzinfo=NY)
    row = CachedPrint(DXY_TICKER, date(2025, 11, 26), 104.2, stamp, "yahoo", ticker=DXY_TICKER)
    save_series(tmp_path, DXY_TICKER, [row])
    loaded = load_series(tmp_path, DXY_TICKER)
    assert loaded[0].ticker == "DX-Y.NYB"
    assert loaded[0].source == "yahoo"


def test_fred_row_rejects_a_yahoo_ticker() -> None:
    stamp = datetime(2025, 11, 28, 9, 30, tzinfo=NY)
    with pytest.raises(ValueError, match="Yahoo ticker"):
        upsert_print([], CachedPrint("DGS10", date(2025, 11, 26), 4.25, stamp, "fred", ticker="DX-Y.NYB"))


def test_cache_inside_the_repo_is_refused(tmp_path: Path) -> None:
    inside = REPO / "tools" / "macro" / "cache"
    with pytest.raises(ValueError, match="outside the repo"):
        assert_outside_repo(inside)
    stamp = datetime(2025, 11, 28, 9, 30, tzinfo=NY)
    with pytest.raises(ValueError, match="outside the repo"):
        save_series(inside, "DGS10", [_fred(date(2025, 11, 26), 4.25, stamp)])
    assert not (inside / "series" / "DGS10.json").exists()
    # tmp_path is the legal place for a test cache.
    save_series(tmp_path, "DFII10", [
        CachedPrint("DFII10", date(2025, 11, 26), 2.1, stamp, "fred"),
    ])
