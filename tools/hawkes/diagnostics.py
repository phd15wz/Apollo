"""Goodness-of-fit via the random time change (Theorem 9.2)."""

from __future__ import annotations

import numpy as np
from scipy import stats

from tools.hawkes.kernels import ExpKernel, PowerLawKernel, as_exp, as_power


def compensator_increments(arrivals, horizon, kernel, allow_large: bool = False) -> np.ndarray:
    stamp = np.asarray(arrivals, dtype=np.float64)
    if isinstance(kernel, PowerLawKernel) or (
        not isinstance(kernel, ExpKernel) and np.asarray(kernel).reshape(-1).size == 4
    ):
        model = as_power(kernel)
        path = model.compensator_path(stamp, allow_large=allow_large)
        end = model.compensator(float(horizon), stamp)
    else:
        model = as_exp(kernel)
        path = model.compensator_path(stamp)
        end = model.compensator(float(horizon), stamp)
    stamps = np.concatenate([np.array([0.0]), path, np.array([end])])
    return np.diff(stamps)


def ks_unit_exponential(increments) -> tuple[float, float]:
    values = np.asarray(increments, dtype=np.float64)
    values = values[np.isfinite(values) & (values >= 0)]
    result = stats.kstest(values, "expon", args=(0, 1))
    return float(result.statistic), float(result.pvalue)
