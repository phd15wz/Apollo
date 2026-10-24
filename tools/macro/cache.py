"""Latest-only cache outside the git tree.

Path: ``$MACRO_DATA_DIR`` when set, otherwise ``~/.cache/apollo/macro``.
A later refresh may replace a level and must keep the ``available_at``
written when that print was first stored. This module does not fetch.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path
from typing import Sequence

from tools.macro.series import ALL_SERIES, DXY_TICKER, FRED_SERIES, SERIES_ROLE, SOURCES

DATA_DIR_ENV = "MACRO_DATA_DIR"
SCHEMA_VERSION = 1

_REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class CachedPrint:
    """One stored print. ``available_at`` is timezone-aware."""

    series_id: str
    obs_date: date
    level: float
    available_at: datetime
    source: str
    ticker: str | None = None


def default_data_dir() -> Path:
    """Cache root. ``MACRO_DATA_DIR`` overrides ``~/.cache/apollo/macro``.

    Unlike the OKX cache, ``XDG_CACHE_HOME`` is not consulted. The spec names
    this path exactly.
    """
    env = os.environ.get(DATA_DIR_ENV, "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return Path.home().resolve() / ".cache" / "apollo" / "macro"


def assert_outside_repo(path: Path) -> Path:
    """Resolve ``path`` and reject anything inside the repository."""
    resolved = path.expanduser().resolve()
    if resolved == _REPO_ROOT or _REPO_ROOT in resolved.parents:
        raise ValueError(f"macro cache must live outside the repo: {resolved}")
    return resolved


def series_cache_path(root: Path, series_id: str) -> Path:
    """JSON path for one locked series, guaranteed to stay under ``root``."""
    if series_id not in ALL_SERIES:
        known = ", ".join(ALL_SERIES)
        raise ValueError(f"unknown series {series_id!r}; locked series: {known}")
    root_resolved = assert_outside_repo(root)
    dest = (root_resolved / "series" / f"{series_id}.json").resolve()
    if not dest.is_relative_to(root_resolved):
        raise ValueError(f"refusing path outside cache dir: {dest}")
    return dest


def _require_date(value: date) -> date:
    if isinstance(value, datetime) or not isinstance(value, date):
        raise TypeError("obs_date must be a datetime.date")
    return value


def validate_print(row: CachedPrint) -> None:
    """Reject a print that does not match the locked series rules."""
    if row.series_id not in ALL_SERIES:
        raise ValueError(f"unknown series {row.series_id!r}")
    _require_date(row.obs_date)
    if isinstance(row.level, bool) or not isinstance(row.level, (int, float)):
        raise TypeError("level must be a finite number")
    if row.level != row.level or row.level in (float("inf"), float("-inf")):
        raise ValueError("level must be finite")
    if not isinstance(row.available_at, datetime) or row.available_at.tzinfo is None:
        raise ValueError("available_at must be a timezone-aware datetime")
    if row.available_at.tzinfo.utcoffset(row.available_at) is None:
        raise ValueError("available_at must be a timezone-aware datetime")
    if row.source not in SOURCES:
        raise ValueError(f"unknown source {row.source!r}")
    if row.series_id in FRED_SERIES:
        if row.source != "fred":
            raise ValueError(f"{row.series_id} is a FRED series")
        if row.ticker is not None:
            raise ValueError("FRED rows do not store a Yahoo ticker")
    if row.series_id == DXY_TICKER:
        if row.source != "yahoo":
            raise ValueError("DX-Y.NYB is the Yahoo research series")
        if row.ticker != DXY_TICKER:
            raise ValueError("DXY rows must store ticker DX-Y.NYB")
    if SERIES_ROLE[row.series_id] not in {"live", "research"}:
        raise ValueError(f"series {row.series_id} has no role")


def upsert_print(rows: Sequence[CachedPrint], new: CachedPrint) -> list[CachedPrint]:
    """Insert ``new`` or replace its level, keeping the original ``available_at``."""
    validate_print(new)
    by_date: dict[date, CachedPrint] = {}
    for row in rows:
        validate_print(row)
        if row.series_id != new.series_id:
            raise ValueError(f"cache mix {row.series_id} and {new.series_id}")
        if row.obs_date in by_date:
            raise ValueError(f"duplicate obs_date {row.obs_date.isoformat()}")
        by_date[row.obs_date] = row
    current = by_date.get(new.obs_date)
    if current is None:
        by_date[new.obs_date] = new
    else:
        by_date[new.obs_date] = replace(current, level=float(new.level))
    return [by_date[key] for key in sorted(by_date)]


def _row_to_json(row: CachedPrint) -> dict[str, object]:
    return {
        "obs_date": row.obs_date.isoformat(),
        "level": row.level,
        "available_at": row.available_at.isoformat(),
        "source": row.source,
        "ticker": row.ticker,
    }


def _row_from_json(series_id: str, payload: dict[str, object]) -> CachedPrint:
    raw_stamp = str(payload["available_at"])
    stamp = datetime.fromisoformat(raw_stamp.replace("Z", "+00:00"))
    row = CachedPrint(
        series_id=series_id,
        obs_date=date.fromisoformat(str(payload["obs_date"])),
        level=float(payload["level"]),  # type: ignore[arg-type]
        available_at=stamp,
        source=str(payload["source"]),
        ticker=None if payload.get("ticker") is None else str(payload["ticker"]),
    )
    validate_print(row)
    return row


def save_series(root: Path, series_id: str, rows: Sequence[CachedPrint]) -> Path:
    """Atomically write one series. Refuses a cache inside the repository."""
    path = series_cache_path(root, series_id)
    checked: list[CachedPrint] = []
    seen: set[date] = set()
    for row in rows:
        validate_print(row)
        if row.series_id != series_id:
            raise ValueError(f"row series {row.series_id} does not match {series_id}")
        if row.obs_date in seen:
            raise ValueError(f"duplicate obs_date {row.obs_date.isoformat()}")
        seen.add(row.obs_date)
        checked.append(row)
    checked.sort(key=lambda row: row.obs_date)
    payload = {
        "schema": SCHEMA_VERSION,
        "series_id": series_id,
        "rows": [_row_to_json(row) for row in checked],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return path


def load_series(root: Path, series_id: str) -> list[CachedPrint]:
    """Load one series. A missing file is an empty cache, not an error."""
    path = series_cache_path(root, series_id)
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != SCHEMA_VERSION:
        raise ValueError(f"unsupported macro cache schema {payload.get('schema')!r}")
    if payload.get("series_id") != series_id:
        raise ValueError(f"cache file series {payload.get('series_id')!r} does not match {series_id}")
    return [_row_from_json(series_id, row) for row in payload["rows"]]
