"""Yahoo Finance daily closes for the research dollar index.

The ticker is locked to ``DX-Y.NYB``. ``yfinance`` is imported inside the
download helper, not at module import. This module returns prints. It does
not write page HTML or a bulk dump. A Yahoo print never opens the live hard
gate. See ``docs/macro-rates-dxy.md``.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo

import pandas as pd

from tools.macro.availability import dxy_available_at
from tools.macro.cache import CachedPrint
from tools.macro.series import DXY_TICKER, YAHOO_SOURCE

NY = ZoneInfo("America/New_York")

History = Callable[[], Any]


def _yahoo_daily_frame() -> Any:
    """Download daily ``DX-Y.NYB`` bars. The import stays inside this function.

    yfinance hides download errors by default. Turn that off for this call so
    a failed pull raises instead of looking like an empty history.
    """
    import yfinance as yf

    hidden = yf.config.debug.hide_exceptions
    yf.config.debug.hide_exceptions = False
    try:
        return yf.Ticker(DXY_TICKER).history(
            period="max",
            interval="1d",
            auto_adjust=False,
            actions=False,
            timeout=30,
        )
    finally:
        yf.config.debug.hide_exceptions = hidden


def _bar_close(stamp: object) -> datetime:
    if isinstance(stamp, datetime):
        when = stamp
    elif hasattr(stamp, "to_pydatetime"):
        when = stamp.to_pydatetime()
    else:
        raise TypeError("Yahoo bar timestamp is missing")
    if not isinstance(when, datetime):
        raise TypeError("Yahoo bar timestamp is missing")
    if when.tzinfo is None or when.tzinfo.utcoffset(when) is None:
        # Daily bars sometimes arrive as a naive session timestamp. Treat that
        # clock as America/New_York, which is the exchange zone for DX-Y.NYB.
        when = when.replace(tzinfo=NY)
    return when


def _prints_from_frame(frame: Any) -> list[CachedPrint]:
    if frame is None or len(frame) == 0:
        return []
    if "Close" not in getattr(frame, "columns", ()):
        raise ValueError("Yahoo history is missing Close")
    by_date: dict = {}
    for stamp, value in frame["Close"].items():
        if pd.isna(value):
            continue
        level = float(value)
        if not math.isfinite(level):
            continue
        available = dxy_available_at(_bar_close(stamp))
        obs_date = available.date()
        row = CachedPrint(
            series_id=DXY_TICKER,
            obs_date=obs_date,
            level=level,
            available_at=available,
            source=YAHOO_SOURCE,
            ticker=DXY_TICKER,
        )
        current = by_date.get(obs_date)
        if current is None or row.available_at >= current.available_at:
            by_date[obs_date] = row
    return [by_date[key] for key in sorted(by_date)]


def fetch_dxy_history(*, history: History | None = None) -> list[CachedPrint]:
    """Return daily ``DX-Y.NYB`` closes as cache prints.

    ``history`` replaces the ``yfinance`` download in tests. It must return a
    frame with a ``Close`` column and a bar-close index. ``available_at`` is
    that timestamp in America/New_York. The ticker field is ``DX-Y.NYB``.
    """
    frame = _yahoo_daily_frame() if history is None else history()
    return _prints_from_frame(frame)
