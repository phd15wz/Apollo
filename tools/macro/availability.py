"""NYSE session clock for FRED ``available_at``.

Full closures come from ``load_confirmed()`` only. Early-close sessions still
count as business days. Weekend dates and full closures are not pull days and
are not "next session" dates. See ``docs/macro-rates-dxy.md``.
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import AbstractSet, Sequence
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from tools.exchange_calendar import load_confirmed
from tools.macro.series import (
    FRED_PULL_HOUR,
    FRED_PULL_MINUTE,
    NYSE_CASH_OPEN_HOUR,
    NYSE_CASH_OPEN_MINUTE,
)

NY = ZoneInfo("America/New_York")

# Whole-day closures in the exchange-calendar schema. early_close, late_open,
# and typhoon_partial still trade, so they stay sessions.
_FULL_CLOSURE_TYPES = frozenset({"full_closure", "typhoon_closure", "weather_closure"})

_PULL_AFTER = time(FRED_PULL_HOUR, FRED_PULL_MINUTE)
_SESSION_HORIZON_DAYS = 366


def as_new_york(when: datetime) -> datetime:
    """Convert an aware timestamp to America/New_York."""
    if not isinstance(when, datetime):
        raise TypeError("timestamp must be a datetime")
    if when.tzinfo is None or when.tzinfo.utcoffset(when) is None:
        raise ValueError("timestamp must be timezone-aware")
    return when.astimezone(NY)


def nyse_full_closure_dates(frame: pd.DataFrame | None = None) -> set[date]:
    """Confirmed NYSE full-closure dates.

    ``unverified`` and ``pending`` rows are dropped by ``load_confirmed``.
    Early closes are not closures.
    """
    confirmed = load_confirmed(frame=frame, exchange="NYSE")
    dates: set[date] = set()
    for raw_date, event_type in zip(confirmed["date"], confirmed["event_type"], strict=True):
        if event_type in _FULL_CLOSURE_TYPES:
            dates.add(date.fromisoformat(str(raw_date)))
    return dates


def is_nyse_session(day: date, closures: AbstractSet[date]) -> bool:
    """True for a weekday that is not a confirmed full closure.

    ``closures`` must be full-closure dates only. An early-close date passed
    in that set would be treated as shut.
    """
    if isinstance(day, datetime) or not isinstance(day, date):
        raise TypeError("day must be a datetime.date")
    return day.weekday() < 5 and day not in closures


def next_nyse_session(after: date, closures: AbstractSet[date]) -> date:
    """The next NYSE session strictly after ``after``."""
    if isinstance(after, datetime) or not isinstance(after, date):
        raise TypeError("after must be a datetime.date")
    day = after
    for _ in range(_SESSION_HORIZON_DAYS):
        day = date.fromordinal(day.toordinal() + 1)
        if is_nyse_session(day, closures):
            return day
    raise LookupError(f"no NYSE session within {_SESSION_HORIZON_DAYS} days after {after.isoformat()}")


def _closures(closures: AbstractSet[date] | None) -> AbstractSet[date]:
    if closures is None:
        return nyse_full_closure_dates()
    return closures


def is_fred_pull_time(when: datetime, *, closures: AbstractSet[date] | None = None) -> bool:
    """True on an NYSE session at or after 16:15 America/New_York."""
    local = as_new_york(when)
    if not is_nyse_session(local.date(), _closures(closures)):
        return False
    return (local.hour, local.minute, local.second, local.microsecond) >= (
        _PULL_AFTER.hour,
        _PULL_AFTER.minute,
        0,
        0,
    )


def fred_scheduled_open(obs_date: date, closures: AbstractSet[date]) -> datetime:
    """09:30 America/New_York on the next NYSE session after ``obs_date``."""
    session = next_nyse_session(obs_date, closures)
    return datetime(
        session.year,
        session.month,
        session.day,
        NYSE_CASH_OPEN_HOUR,
        NYSE_CASH_OPEN_MINUTE,
        tzinfo=NY,
    )


def fred_available_at(
    obs_date: date,
    fetched_at: datetime,
    *,
    closures: AbstractSet[date] | None = None,
) -> datetime:
    """When a FRED print for ``obs_date`` may first be read.

    The scheduled stamp is 09:30 America/New_York on the next NYSE session.
    A print first fetched after that open keeps ``available_at`` at the fetch
    time, so a late pull is not stamped back to the open.
    """
    if isinstance(obs_date, datetime) or not isinstance(obs_date, date):
        raise TypeError("obs_date must be a datetime.date")
    closed = _closures(closures)
    scheduled = fred_scheduled_open(obs_date, closed)
    fetched = as_new_york(fetched_at)
    if fetched > scheduled:
        return fetched
    return scheduled


def dxy_available_at(bar_close: datetime) -> datetime:
    """Yahoo bar close expressed in America/New_York. Research only."""
    return as_new_york(bar_close)


def select_visible_indices(available_at: Sequence[datetime], decision_at: datetime) -> np.ndarray:
    """Indices whose ``available_at`` is at or before ``decision_at``.

    Order is preserved. Changes and σ run on this subset, in ``obs_date`` order.
    """
    decision = as_new_york(decision_at)
    keep = [index for index, stamp in enumerate(available_at) if as_new_york(stamp) <= decision]
    return np.asarray(keep, dtype=int)
