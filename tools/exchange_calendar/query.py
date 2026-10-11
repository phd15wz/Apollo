"""Read the holiday dataset for callers that act on it.

Strategies and reminders must go through ``load_confirmed``. The raw
``holidays.csv`` includes ``unverified`` and ``pending`` rows that a library
disagreed with or that no official source has settled.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
HOLIDAYS_PATH = ROOT / "data" / "exchange_calendar" / "holidays.csv"

CONFIRMED_STATUSES = ("official", "secondary-confirmed")
REJECTED_STATUSES = ("unverified", "pending")
KNOWN_STATUSES = frozenset(CONFIRMED_STATUSES + REJECTED_STATUSES)


def load_confirmed(path: Path | str | None = None, frame: pd.DataFrame | None = None) -> pd.DataFrame:
    """Return only rows whose status is ``official`` or ``secondary-confirmed``.

    ``unverified`` and ``pending`` rows are dropped. An unknown status raises,
    so a new label cannot slip through as confirmed.
    """
    if frame is None:
        source = Path(path) if path is not None else HOLIDAYS_PATH
        frame = pd.read_csv(source, dtype=str, keep_default_na=False)
    statuses = set(frame["status"])
    unknown = sorted(statuses - KNOWN_STATUSES)
    if unknown:
        raise ValueError(f"unknown status values {unknown}")
    return frame[frame["status"].isin(CONFIRMED_STATUSES)].reset_index(drop=True)
