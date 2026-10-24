"""One refresh of the rates and DXY cache.

Usage from the repository root::

    python -m tools.macro.refresh --cache-dir "$MACRO_DATA_DIR"
    python -m tools.macro.refresh --cache-dir "$MACRO_DATA_DIR" --backfill

Exit status:

- 0: the cache was updated and the summary was printed.
- 1: the pull window, the spread check, or a fetch failed. The cache is left
  as it was when the spread check fails.
- 2: ``FRED_API_KEY`` is missing, or the cache path is inside the repository.

Unit tests inject the FRED and Yahoo callables. The default path does call
those services. Do not point the cache at a directory inside the repo.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Mapping, Sequence
from zoneinfo import ZoneInfo

from tools.macro.availability import is_fred_pull_time, select_visible_indices
from tools.macro.cache import CachedPrint, assert_outside_repo, default_data_dir, load_series, save_series, upsert_print
from tools.macro.fred import FredHttpError, FredResponseError, MissingFredApiKey, fetch_observations, fred_api_key
from tools.macro.series import DXY_TICKER, FRED_SERIES
from tools.macro.transforms import SpreadMismatch, assert_published_spread, live_hard_gate, observation_changes, sharp_move
from tools.macro.yahoo import fetch_dxy_history

NY = ZoneInfo("America/New_York")

FredFetch = Callable[..., Sequence[CachedPrint]]
DxyFetch = Callable[..., Sequence[CachedPrint]]


class OutsidePullWindow(RuntimeError):
    """A normal daily pull ran before 16:15 America/New_York or off the NYSE session."""

    def __init__(self) -> None:
        super().__init__(
            "FRED pull is only valid on an NYSE session at or after 16:15 America/New_York. "
            "Re-run with --backfill for a later manual refresh."
        )


@dataclass(frozen=True)
class SeriesSnapshot:
    """Latest print visible at the decision time, plus that row's sharp flag."""

    series_id: str
    sharp_move: bool
    short_sample: bool
    latest_obs_date: date | None
    latest_level: float | None


@dataclass(frozen=True)
class RefreshSummary:
    """Gate plus one snapshot per locked series. Research flags do not open the gate."""

    gate: bool
    series: tuple[SeriesSnapshot, ...]

    def sharp_flags(self) -> dict[str, bool]:
        return {item.series_id: item.sharp_move for item in self.series}


def _require_now(now: datetime) -> datetime:
    if not isinstance(now, datetime) or now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
        raise ValueError("now must be a timezone-aware datetime")
    return now


def _merge(existing: Sequence[CachedPrint], incoming: Sequence[CachedPrint]) -> list[CachedPrint]:
    """Apply :func:`upsert_print` per row so a replaced level keeps its first ``available_at``."""
    by_date = {row.obs_date: row for row in existing}
    for row in incoming:
        current = by_date.get(row.obs_date)
        merged = upsert_print(() if current is None else (current,), row)
        by_date[row.obs_date] = merged[0]
    return [by_date[key] for key in sorted(by_date)]


def _levels(rows: Sequence[CachedPrint]) -> dict[date, float]:
    return {row.obs_date: row.level for row in rows}


def _assert_stored_spread(merged: Mapping[str, Sequence[CachedPrint]]) -> None:
    """Stop before any write when the published slope disagrees by more than 1 bp."""
    dgs2 = _levels(merged["DGS2"])
    dgs10 = _levels(merged["DGS10"])
    published = _levels(merged["T10Y2Y"])
    dates = sorted(set(dgs2) & set(dgs10) & set(published))
    assert_published_spread(
        dates,
        [dgs10[day] for day in dates],
        [dgs2[day] for day in dates],
        [published[day] for day in dates],
    )


def _snapshot(series_id: str, rows: Sequence[CachedPrint], now: datetime) -> SeriesSnapshot:
    visible_at = select_visible_indices([row.available_at for row in rows], now)
    if visible_at.size == 0:
        return SeriesSnapshot(series_id, False, False, None, None)
    visible = [rows[int(index)] for index in visible_at]
    changes = observation_changes([row.level for row in visible])
    flags = sharp_move(changes.change_1d)
    latest = visible[-1]
    return SeriesSnapshot(
        series_id,
        bool(flags.sharp_move[-1]),
        bool(flags.short_sample[-1]),
        latest.obs_date,
        latest.level,
    )


def refresh_once(
    *,
    now: datetime,
    cache_dir: Path,
    backfill: bool = False,
    fetch_fred: FredFetch | None = None,
    fetch_dxy: DxyFetch | None = None,
) -> RefreshSummary:
    """Refresh every locked series and return the gate at ``now``.

    1. On a normal daily pull, require
       :func:`tools.macro.availability.is_fred_pull_time` (NYSE session,
       at or after 16:15 America/New_York). ``backfill=True`` is the manual
       run that may happen later. ``available_at`` still comes from
       :func:`tools.macro.availability.fred_available_at` on the FRED fetch,
       and :func:`tools.macro.cache.upsert_print` keeps the first stamp, so
       a later pull does not move it backward.
    2. :func:`tools.macro.fred.fetch_observations` for ``DGS2``, ``DGS10``,
       ``T10Y2Y``, and ``DFII10``.
    3. :func:`tools.macro.yahoo.fetch_dxy_history` for ``DX-Y.NYB``.
    4. :func:`tools.macro.transforms.assert_published_spread` where ``DGS2``,
       ``DGS10``, and ``T10Y2Y`` all have a level. :class:`SpreadMismatch`
       aborts before the cache is written. ``DGS10 - DGS2`` is not stored.
    5. :func:`tools.macro.cache.upsert_print` so a replaced level keeps the
       original ``available_at``.
    6. For a decision at ``now``, keep rows with
       :func:`tools.macro.availability.select_visible_indices`, then
       :func:`tools.macro.transforms.observation_changes` and
       :func:`tools.macro.transforms.sharp_move`.
    7. :func:`tools.macro.transforms.live_hard_gate` from ``DGS10`` and
       ``DFII10`` only. Research flags are on the summary and do not open
       the gate.

    ``fetch_fred`` and ``fetch_dxy`` replace the live clients in tests.
    """
    clock = _require_now(now)
    root = assert_outside_repo(cache_dir)
    if not backfill and not is_fred_pull_time(clock):
        raise OutsidePullWindow()

    fred_fetch = fetch_fred if fetch_fred is not None else fetch_observations
    dxy_fetch = fetch_dxy if fetch_dxy is not None else fetch_dxy_history
    fetched: dict[str, Sequence[CachedPrint]] = {
        series_id: list(fred_fetch(series_id, fetched_at=clock)) for series_id in FRED_SERIES
    }
    fetched[DXY_TICKER] = list(dxy_fetch())

    merged = {
        series_id: _merge(load_series(root, series_id), rows) for series_id, rows in fetched.items()
    }
    _assert_stored_spread(merged)
    for series_id, rows in merged.items():
        save_series(root, series_id, rows)

    snapshots = tuple(_snapshot(series_id, merged[series_id], clock) for series_id in (*FRED_SERIES, DXY_TICKER))
    gate = live_hard_gate({item.series_id: item.sharp_move for item in snapshots})
    return RefreshSummary(gate=gate, series=snapshots)


def _redact(text: str) -> str:
    key = os.environ.get("FRED_API_KEY", "").strip()
    if key and key in text:
        return text.replace(key, "[redacted]")
    return text


def _print_summary(summary: RefreshSummary) -> None:
    print(f"gate={'on' if summary.gate else 'off'}")
    for item in summary.series:
        level = "na" if item.latest_level is None else f"{item.latest_level:.4f}"
        obs = "na" if item.latest_obs_date is None else item.latest_obs_date.isoformat()
        print(
            f"{item.series_id} obs_date={obs} level={level} "
            f"sharp={str(item.sharp_move).lower()} short_sample={str(item.short_sample).lower()}"
        )


def main(argv: Sequence[str] | None = None) -> int:
    """Refresh the cache. Missing key and a repo cache path exit 2."""
    parser = argparse.ArgumentParser(
        prog="python -m tools.macro.refresh",
        description="Refresh the rates and DXY cache.",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="Override MACRO_DATA_DIR. Must sit outside the repository.",
    )
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="Manual run outside the 16:15 America/New_York window. Does not move available_at backward.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        fred_api_key()
        root = args.cache_dir if args.cache_dir is not None else default_data_dir()
        summary = refresh_once(now=datetime.now(NY), cache_dir=root, backfill=args.backfill)
    except MissingFredApiKey as exc:
        print(exc, file=sys.stderr)
        return 2
    except ValueError as exc:
        print(_redact(str(exc)), file=sys.stderr)
        return 2
    except (OutsidePullWindow, SpreadMismatch, FredHttpError, FredResponseError) as exc:
        print(_redact(str(exc)), file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"{type(exc).__name__}: {_redact(str(exc))}", file=sys.stderr)
        return 1
    _print_summary(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
