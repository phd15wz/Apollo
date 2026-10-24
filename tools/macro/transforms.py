"""Pure transforms for the rates and DXY feed.

No network and no secrets. Observation windows count available prints, not
calendar days. ``sharp_move`` uses the population standard deviation (divide
by N) of signed 1-day changes. Callers pass rows that are already visible:
``available_at <= decision time``. See ``docs/macro-rates-dxy.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Mapping, Sequence

import numpy as np
from numpy.typing import ArrayLike

from tools.macro.series import (
    LIVE_HARD_GATE_SERIES,
    SHARP_PRIOR_MAX,
    SHARP_PRIOR_MIN,
    SHARP_SIGMA,
    SPREAD_TOLERANCE_PCT,
)

# Binary noise is far smaller than a basis point. A true excess still fails.
_SPREAD_SLACK = 1e-9


@dataclass(frozen=True)
class ObservationChanges:
    """1-day and 5-day changes aligned to ``levels`` (NaN where undefined)."""

    change_1d: np.ndarray
    change_5d: np.ndarray


@dataclass(frozen=True)
class SharpMove:
    """Per-row sharp-move flag. ``short_sample`` is set when a finite change has fewer than 40 priors."""

    sharp_move: np.ndarray
    short_sample: np.ndarray
    sigma: np.ndarray


class SpreadMismatch(RuntimeError):
    """Published ``T10Y2Y`` disagrees with ``DGS10 - DGS2`` by more than 1 bp.

    The refresh must stop. The recomputed difference is not a stored series.
    """

    def __init__(self, obs_date: date, published: float, recomputed: float) -> None:
        self.obs_date = obs_date
        self.published = float(published)
        self.recomputed = float(recomputed)
        self.gap = abs(self.published - self.recomputed)
        super().__init__(
            f"{obs_date.isoformat()}: |T10Y2Y - (DGS10 - DGS2)| = {self.gap:.6f} "
            f"exceeds {SPREAD_TOLERANCE_PCT} (1 bp); refresh stopped"
        )


def _as_1d(values: ArrayLike, name: str) -> np.ndarray:
    array = np.asarray(values, dtype=np.float64)
    if array.ndim != 1:
        raise ValueError(f"{name} must be a 1-d series")
    return array


def _lag_change(levels: np.ndarray, lag: int) -> np.ndarray:
    """Level minus the print ``lag`` finite observations earlier."""
    out = np.full(levels.shape, np.nan, dtype=np.float64)
    finite = np.flatnonzero(np.isfinite(levels))
    if finite.size <= lag:
        return out
    current = finite[lag:]
    previous = finite[:-lag]
    out[current] = levels[current] - levels[previous]
    return out


def observation_changes(levels: ArrayLike) -> ObservationChanges:
    """1-day and 5-day changes by observation count.

    ``levels`` is in ``obs_date`` order. NaN entries are missing prints and do
    not count as observations, so a holiday slot does not shrink the window.
    The caller must already have dropped rows that were not available yet.
    """
    values = _as_1d(levels, "levels")
    return ObservationChanges(change_1d=_lag_change(values, 1), change_5d=_lag_change(values, 5))


def sharp_move(
    change_1d: ArrayLike,
    *,
    prior_max: int = SHARP_PRIOR_MAX,
    prior_min: int = SHARP_PRIOR_MIN,
    n_sigma: float = SHARP_SIGMA,
) -> SharpMove:
    """Flag |1-day change| > ``n_sigma`` * σ of the preceding signed changes.

    σ is the population standard deviation (divide by N) of at most the last
    ``prior_max`` preceding finite changes, and only when at least
    ``prior_min`` of them exist. The current change is not in its own window.
    It joins the window on the next print. Fewer than ``prior_min`` priors
    leaves ``sharp_move`` false and records ``short_sample``. Non-finite
    changes are skipped and are not a short sample.

    Both signs count. The threshold is strict: a change equal to 2σ is not a
    sharp move.
    """
    changes = _as_1d(change_1d, "change_1d")
    if prior_min < 2:
        raise ValueError("prior_min must be at least 2")
    if prior_max < prior_min:
        raise ValueError("prior_max must be >= prior_min")
    if n_sigma <= 0:
        raise ValueError("n_sigma must be positive")

    flags = np.zeros(changes.shape, dtype=bool)
    short = np.zeros(changes.shape, dtype=bool)
    sigma = np.full(changes.shape, np.nan, dtype=np.float64)
    finite_positions: list[int] = []
    for index, value in enumerate(changes):
        if not np.isfinite(value):
            continue
        preceding = finite_positions[-prior_max:]
        if len(preceding) < prior_min:
            short[index] = True
        else:
            window = changes[np.asarray(preceding, dtype=int)]
            scale = float(np.std(window, ddof=0))
            sigma[index] = scale
            flags[index] = abs(float(value)) > n_sigma * scale
        finite_positions.append(index)
    return SharpMove(sharp_move=flags, short_sample=short, sigma=sigma)


def live_hard_gate(flags: Mapping[str, bool]) -> bool:
    """True when ``DGS10`` or ``DFII10`` is sharp.

    Research series are ignored, including a sharp dollar index. Pass flags
    from rows already visible at the decision time.
    """
    return any(bool(flags.get(series_id)) for series_id in LIVE_HARD_GATE_SERIES)


def _as_date(value: date | datetime | str | np.datetime64) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, np.datetime64):
        return date.fromisoformat(str(value)[:10])
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    raise TypeError(f"cannot read an observation date from {value!r}")


def assert_published_spread(
    obs_dates: Sequence[date | datetime | str | np.datetime64],
    dgs10: ArrayLike,
    dgs2: ArrayLike,
    t10y2y: ArrayLike,
    *,
    tolerance: float = SPREAD_TOLERANCE_PCT,
) -> None:
    """Require ``|T10Y2Y - (DGS10 - DGS2)| <= tolerance`` on complete dates.

    Dates missing any of the three levels are skipped. The first failure
    raises :class:`SpreadMismatch` and stops the refresh. This does not write
    ``DGS10 - DGS2`` back as the slope.
    """
    if tolerance < 0:
        raise ValueError("tolerance must be non-negative")
    ten_year = _as_1d(dgs10, "dgs10")
    two_year = _as_1d(dgs2, "dgs2")
    published = _as_1d(t10y2y, "t10y2y")
    if not (len(obs_dates) == ten_year.size == two_year.size == published.size):
        raise ValueError("obs_dates, DGS10, DGS2, and T10Y2Y must have the same length")
    for index, raw_date in enumerate(obs_dates):
        if not (
            np.isfinite(ten_year[index])
            and np.isfinite(two_year[index])
            and np.isfinite(published[index])
        ):
            continue
        recomputed = float(ten_year[index] - two_year[index])
        gap = abs(float(published[index]) - recomputed)
        if gap > tolerance + _SPREAD_SLACK:
            raise SpreadMismatch(_as_date(raw_date), float(published[index]), recomputed)
