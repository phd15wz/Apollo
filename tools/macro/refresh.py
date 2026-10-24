"""CLI scaffold for one macro refresh.

Usage from the repository root::

    python -m tools.macro.refresh
    python -m tools.macro.refresh --cache-dir /tmp/apollo-macro

Exit status:

- 2: ``FRED_API_KEY`` is missing, or the cache path is inside the repository.
- 3: the key and the cache path are acceptable, and the HTTP refresh is still
  the TODO below.

The command does not contact FRED or Yahoo.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import NoReturn, Sequence
from zoneinfo import ZoneInfo

from tools.macro.cache import assert_outside_repo, default_data_dir
from tools.macro.fred import MissingFredApiKey, fred_api_key

NY = ZoneInfo("America/New_York")


def refresh_once(*, now: datetime, cache_dir: Path) -> NoReturn:
    """Run one refresh. HTTP is not implemented.

    TODO(Isaac), in this function:

    1. On a normal daily pull, require
       :func:`tools.macro.availability.is_fred_pull_time` (NYSE session,
       at or after 16:15 America/New_York). A manual backfill may run later.
       Never move ``available_at`` backward: a fetch after the next cash
       open keeps the fetch time (:func:`tools.macro.availability.fred_available_at`).
    2. :func:`tools.macro.fred.fetch_observations` for ``DGS2``, ``DGS10``,
       ``T10Y2Y``, and ``DFII10``.
    3. :func:`tools.macro.yahoo.fetch_dxy_history` for ``DX-Y.NYB``.
    4. :func:`tools.macro.transforms.assert_published_spread` where ``DGS2``,
       ``DGS10``, and ``T10Y2Y`` all have a level. Let
       :class:`tools.macro.transforms.SpreadMismatch` abort the refresh.
       Do not store ``DGS10 - DGS2`` as the slope.
    5. :func:`tools.macro.cache.upsert_print` so a replaced level keeps the
       original ``available_at``.
    6. For a decision at ``now``, keep rows with
       :func:`tools.macro.availability.select_visible_indices`, then
       :func:`tools.macro.transforms.observation_changes` and
       :func:`tools.macro.transforms.sharp_move`.
    7. :func:`tools.macro.transforms.live_hard_gate` from ``DGS10`` and
       ``DFII10`` only. Research flags are stored and do not open the gate.
    """
    if not isinstance(now, datetime) or now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
        raise ValueError("now must be a timezone-aware datetime")
    root = assert_outside_repo(cache_dir)
    raise NotImplementedError(
        "Macro HTTP refresh is not implemented. "
        "Fill tools/macro/fred.py and tools/macro/yahoo.py. "
        f"Cache directory: {root}. Spec: docs/macro-rates-dxy.md."
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Check the key and the cache path, then stop before any HTTP."""
    parser = argparse.ArgumentParser(
        prog="python -m tools.macro.refresh",
        description=(
            "Refresh the rates and DXY cache (scaffold). "
            "Live FRED and Yahoo HTTP are not implemented."
        ),
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="Override MACRO_DATA_DIR. Must sit outside the repository.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        fred_api_key()
        root = args.cache_dir if args.cache_dir is not None else default_data_dir()
        refresh_once(now=datetime.now(NY), cache_dir=root)
    except MissingFredApiKey as exc:
        print(exc, file=sys.stderr)
        return 2
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    except NotImplementedError as exc:
        print(exc, file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
