"""One refresh of the rates and DXY cache.

Usage from the repository root::

    python -m tools.macro.refresh --cache-dir "$MACRO_DATA_DIR"
    python -m tools.macro.refresh --cache-dir "$MACRO_DATA_DIR" --backfill
    python -m tools.macro.refresh --cache-dir "$MACRO_DATA_DIR" --backfill --observation-start 1992-01-01

Exit status:

- 0: the cache was updated and the summary was printed. A date outside the
  1 bp published-spread band is skipped and logged; it does not fail the run.
- 1: the pull window or a fetch failed.
- 2: ``FRED_API_KEY`` is missing, or the cache path is inside the repository.

Unit tests inject the FRED and Yahoo callables. The default path does call
those services. Do not point the cache at a directory inside the repo.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Sequence
from zoneinfo import ZoneInfo

from tools.macro.availability import is_fred_pull_time, select_visible_indices
from tools.macro.cache import CachedPrint, assert_outside_repo, default_data_dir, load_series, save_series, upsert_print
from tools.macro.fred import FredHttpError, FredResponseError, MissingFredApiKey, fetch_observations, fred_api_key
from tools.macro.series import DXY_TICKER, FRED_DEFAULT_OBSERVATION_START, FRED_SERIES, SPREAD_SERIES
from tools.macro.transforms import (
    SpreadGap,
    SpreadMismatch,
    format_spread_skip,
    live_hard_gate,
    observation_changes,
    sharp_move,
    spread_gaps,
)
from tools.macro.yahoo import fetch_dxy_history

_LOG = logging.getLogger(__name__)

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
    """Gate plus one snapshot per locked series. Research flags do not open the gate.

    ``skipped`` lists observation dates left out of ``DGS10``, ``DGS2``, and
    ``T10Y2Y`` because the published spread missed the 1 bp band. ``DFII10``
    and ``DX-Y.NYB`` are not part of that check.
    """

    gate: bool
    series: tuple[SeriesSnapshot, ...]
    skipped: tuple[SpreadGap, ...] = ()

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


def _resolve_observation_start(value: date | None) -> date:
    """FRED window start. ``None`` is the 1992-01-01 default."""
    if value is None:
        return FRED_DEFAULT_OBSERVATION_START
    # datetime is a date subclass; a timestamp is not an observation bound.
    if isinstance(value, datetime) or not isinstance(value, date):
        raise TypeError("observation_start must be a datetime.date")
    return value


def _drop_obs_dates(rows: Sequence[CachedPrint], banned: set[date]) -> list[CachedPrint]:
    return [row for row in rows if row.obs_date not in banned]


def _skip_spread_gaps(merged: dict[str, list[CachedPrint]]) -> tuple[SpreadGap, ...]:
    """Drop observation dates outside the 1 bp band and log each one.

    The band is unchanged. A single bad date does not abort the refresh.
    Only ``DGS10``, ``DGS2``, and ``T10Y2Y`` lose that ``obs_date``.
    """
    dgs2 = _levels(merged["DGS2"])
    dgs10 = _levels(merged["DGS10"])
    published = _levels(merged["T10Y2Y"])
    dates = sorted(set(dgs2) & set(dgs10) & set(published))
    gaps = spread_gaps(
        dates,
        [dgs10[day] for day in dates],
        [dgs2[day] for day in dates],
        [published[day] for day in dates],
    )
    for gap in gaps:
        _LOG.warning("%s", format_spread_skip(gap))
    if not gaps:
        return ()
    banned = {gap.obs_date for gap in gaps}
    for series_id in SPREAD_SERIES:
        merged[series_id] = _drop_obs_dates(merged[series_id], banned)
    return tuple(gaps)


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
    observation_start: date | None = None,
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
       ``T10Y2Y``, and ``DFII10``, starting at ``observation_start``
       (default :data:`tools.macro.series.FRED_DEFAULT_OBSERVATION_START`,
       1992-01-01).
    3. :func:`tools.macro.yahoo.fetch_dxy_history` for ``DX-Y.NYB``. The
       Yahoo pull stays on full history and ignores ``observation_start``.
    4. Where ``DGS2``, ``DGS10``, and ``T10Y2Y`` all have a level,
       :func:`tools.macro.transforms.spread_gaps` applies the 1 bp band.
       Each date outside the band is logged and dropped from those three
       series. The rest of the book is still written. ``DGS10 - DGS2`` is
       not stored. :class:`SpreadMismatch` is the hard failure for a direct
       :func:`tools.macro.transforms.assert_published_spread` call; this
       refresh does not abort on one bad date.
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
    start = _resolve_observation_start(observation_start)

    fred_fetch = fetch_fred if fetch_fred is not None else fetch_observations
    dxy_fetch = fetch_dxy if fetch_dxy is not None else fetch_dxy_history
    fetched: dict[str, Sequence[CachedPrint]] = {
        series_id: list(fred_fetch(series_id, fetched_at=clock, observation_start=start))
        for series_id in FRED_SERIES
    }
    fetched[DXY_TICKER] = list(dxy_fetch())

    merged = {
        series_id: _merge(load_series(root, series_id), rows) for series_id, rows in fetched.items()
    }
    skipped = _skip_spread_gaps(merged)
    for series_id, rows in merged.items():
        save_series(root, series_id, rows)

    snapshots = tuple(_snapshot(series_id, merged[series_id], clock) for series_id in (*FRED_SERIES, DXY_TICKER))
    gate = live_hard_gate({item.series_id: item.sharp_move for item in snapshots})
    return RefreshSummary(gate=gate, series=snapshots, skipped=skipped)


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


def _iso_date(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("observation-start must be YYYY-MM-DD") from exc


def _ensure_cli_logging() -> None:
    """Show skip warnings on stderr when the process has no log handler yet."""
    if logging.getLogger().handlers:
        return
    logging.basicConfig(
        level=logging.WARNING,
        stream=sys.stderr,
        format="%(levelname)s %(name)s %(message)s",
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
    parser.add_argument(
        "--observation-start",
        type=_iso_date,
        default=FRED_DEFAULT_OBSERVATION_START,
        help=(
            "First obs_date requested from FRED, YYYY-MM-DD. "
            f"Default: {FRED_DEFAULT_OBSERVATION_START.isoformat()}. "
            "Does not bound DX-Y.NYB."
        ),
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    _ensure_cli_logging()
    try:
        fred_api_key()
        root = args.cache_dir if args.cache_dir is not None else default_data_dir()
        summary = refresh_once(
            now=datetime.now(NY),
            cache_dir=root,
            backfill=args.backfill,
            observation_start=args.observation_start,
        )
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
