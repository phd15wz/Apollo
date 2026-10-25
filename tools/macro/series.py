"""Locked series, units, and thresholds for the rates and DXY feed.

The rules live in ``docs/macro-rates-dxy.md``. This module only names them
so the rest of the package cannot drift.
"""

from __future__ import annotations

from datetime import date

# FRED series ids. Levels are percent, as FRED publishes them (4.25, not 0.0425).
FRED_SERIES: tuple[str, ...] = ("DGS2", "DGS10", "T10Y2Y", "DFII10")

# The only dollar-index ticker. Research pulls and the cache share this string.
DXY_TICKER = "DX-Y.NYB"

ALL_SERIES: tuple[str, ...] = FRED_SERIES + (DXY_TICKER,)

# Live hard gate. Either series tripping sharp_move opens the gate.
LIVE_HARD_GATE_SERIES: tuple[str, ...] = ("DGS10", "DFII10")

# Pulled and flagged. They never open the live gate.
RESEARCH_SERIES: tuple[str, ...] = ("DGS2", "T10Y2Y", DXY_TICKER)

SERIES_ROLE: dict[str, str] = {
    "DGS2": "research",
    "DGS10": "live",
    "T10Y2Y": "research",
    "DFII10": "live",
    DXY_TICKER: "research",
}

# |T10Y2Y - (DGS10 - DGS2)| must be within 1 basis point, in percent units.
SPREAD_TOLERANCE_PCT = 0.01

# The three series that enter the published-spread check, in log order.
SPREAD_SERIES: tuple[str, ...] = ("DGS10", "DGS2", "T10Y2Y")

# Default first obs_date on a FRED refresh. Callers override with
# --observation-start. DX-Y.NYB stays on the full Yahoo history.
FRED_DEFAULT_OBSERVATION_START = date(1992, 1, 1)

# sharp_move: population σ of the preceding signed 1-day changes.
SHARP_PRIOR_MAX = 60
SHARP_PRIOR_MIN = 40
SHARP_SIGMA = 2.0

# H.15 pull window and the cash-open stamp, America/New_York.
FRED_PULL_HOUR = 16
FRED_PULL_MINUTE = 15
NYSE_CASH_OPEN_HOUR = 9
NYSE_CASH_OPEN_MINUTE = 30

FRED_SOURCE = "fred"
YAHOO_SOURCE = "yahoo"
SOURCES: frozenset[str] = frozenset({FRED_SOURCE, YAHOO_SOURCE})
