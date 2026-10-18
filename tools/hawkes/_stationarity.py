"""Stationarity checks for linear Hawkes models (Theorem 3.1)."""

from __future__ import annotations

import warnings

import numpy as np


class NonstationaryWarning(UserWarning):
    """Fitted or requested branching ratio / spectral radius is >= 1."""


def matrix_spectral_radius(matrix) -> float:
    vals = np.linalg.eigvals(np.asarray(matrix, dtype=np.float64))
    return float(np.max(np.abs(vals)))


def warn_if_explosive(
    ratio: float,
    label: str = "branching ratio",
    *,
    stacklevel: int = 3,
) -> str | None:
    if not np.isfinite(ratio) or ratio >= 1.0:
        msg = (
            f"Fitted Hawkes {label} is {ratio:.4g} >= 1; the process is not stationary."
        )
        warnings.warn(msg, NonstationaryWarning, stacklevel=stacklevel)
        return msg
    return None


def horizon_event_cap(baseline: float, horizon: float) -> int:
    return int(np.ceil(max(2000.0, 100.0 * float(horizon) * max(float(baseline), 1.0))))


def require_or_cap(
    ratio: float,
    baseline: float,
    horizon: float,
    *,
    allow_nonstationary: bool,
    kind: str = "branching ratio",
    max_events: int | None = None,
) -> int | None:
    """Return None if stationary; otherwise raise, or warn and return an event cap."""
    if np.isfinite(ratio) and ratio < 1.0:
        return None
    msg = f"Hawkes {kind} is {ratio:.4g} >= 1"
    if not allow_nonstationary:
        raise ValueError(
            f"{msg}; refusing to simulate an explosive process. "
            "Pass allow_nonstationary=True to run with a finite-horizon event cap."
        )
    warnings.warn(
        f"{msg}; simulating with a horizon event cap.",
        NonstationaryWarning,
        stacklevel=3,
    )
    if max_events is not None:
        return int(max_events)
    return horizon_event_cap(baseline, horizon)
