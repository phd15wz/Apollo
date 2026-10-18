"""Log-likelihood, score, and Hessian for exponential and power-law kernels.

Exponential O(N) form: eqs. 5.7–5.8 (recursive excitation cache).
Power-law: Definition 3.1 likelihood with chunked pairwise sums (no N×N store).
"""

from __future__ import annotations

import numpy as np

from tools.hawkes._limits import CHUNK, MAX_QUADRATIC_EVENTS
from tools.hawkes.kernels import as_exp, as_power


def _excitation_cache(arrivals: np.ndarray, decay: float) -> np.ndarray:
    """R_i = exp(-decay * gap_i) * (1 + R_{i-1}), R_0 = 0."""
    n = arrivals.size
    rec = np.empty(n, dtype=np.float64)
    rec[0] = 0.0
    if n == 1:
        return rec
    fade = np.exp(-decay * np.diff(arrivals))
    for i in range(1, n):
        rec[i] = fade[i - 1] * (1.0 + rec[i - 1])
    return rec


def _weighted_cache(arrivals: np.ndarray, decay: float, power: int) -> np.ndarray:
    n = arrivals.size
    rec = np.empty(n, dtype=np.float64)
    rec[0] = 0.0
    if n == 1:
        return rec
    fade = np.exp(-decay * np.diff(arrivals))
    lagged = arrivals[:-1] ** power
    for i in range(1, n):
        rec[i] = fade[i - 1] * (lagged[i - 1] + rec[i - 1])
    return rec


def exponential_loglik(arrivals, horizon, kernel) -> float:
    model = as_exp(kernel)
    stamp = np.asarray(arrivals, dtype=np.float64).reshape(-1)
    if stamp.size == 0:
        return float(-model.baseline * horizon)
    rec = _excitation_cache(stamp, model.decay)
    return float(
        -model.baseline * horizon
        + np.log(model.baseline + model.jump * rec).sum()
        - (model.jump / model.decay)
        * np.sum(1.0 - np.exp(-model.decay * (horizon - stamp)))
    )


def exponential_score(arrivals, horizon, kernel) -> np.ndarray:
    model = as_exp(kernel)
    stamp = np.asarray(arrivals, dtype=np.float64).reshape(-1)
    n = stamp.size
    if n == 0:
        return np.array([-horizon, 0.0, 0.0], dtype=np.float64)
    r0 = _weighted_cache(stamp, model.decay, 0)
    r1 = _weighted_cache(stamp, model.decay, 1)
    den = model.baseline + model.jump * r0
    lag = stamp * r0 - r1
    remain = horizon - stamp
    fade = np.exp(-model.decay * remain)
    d_base = -horizon + np.sum(1.0 / den)
    d_jump = np.sum((1.0 / model.decay) * (fade - 1.0) + r0 / den)
    d_decay = np.sum(
        -model.jump
        * (
            (1.0 / model.decay) * remain * fade
            + (1.0 / model.decay**2) * (fade - 1.0)
        )
        - (model.jump * lag / den)
    )
    return np.array([d_base, d_jump, d_decay], dtype=np.float64)


def exponential_hessian(arrivals, horizon, kernel) -> np.ndarray:
    model = as_exp(kernel)
    stamp = np.asarray(arrivals, dtype=np.float64).reshape(-1)
    info = np.zeros((3, 3), dtype=np.float64)
    if stamp.size == 0:
        return info
    r0 = _weighted_cache(stamp, model.decay, 0)
    r1 = _weighted_cache(stamp, model.decay, 1)
    r2 = _weighted_cache(stamp, model.decay, 2)
    den = model.baseline + model.jump * r0
    lag = stamp * r0 - r1
    quad = stamp**2 * r0 - 2.0 * stamp * r1 + r2
    remain = horizon - stamp
    fade = np.exp(-model.decay * remain)
    info[1, 1] = np.sum(-(r0 / den) ** 2)
    info[1, 2] = np.sum(
        -(
            (1.0 / model.decay) * remain * fade
            + (1.0 / model.decay**2) * (fade - 1.0)
        )
        + (-lag / den + (model.jump * r0 * lag) / den**2)
    )
    info[2, 2] = np.sum(
        model.jump
        * (
            (1.0 / model.decay) * remain**2 * fade
            + (2.0 / model.decay**2) * remain * fade
            + (2.0 / model.decay**3) * (fade - 1.0)
        )
        + (model.jump * quad / den - (model.jump * lag / den) ** 2)
    )
    info[0, 0] = np.sum(-1.0 / den**2)
    info[0, 1] = np.sum(-r0 / den**2)
    info[0, 2] = np.sum(model.jump * lag / den**2)
    info[1, 0] = info[0, 1]
    info[2, 0] = info[0, 2]
    info[2, 1] = info[1, 2]
    return info


def hessian_stderr(arrivals, horizon, kernel) -> np.ndarray:
    hess = exponential_hessian(arrivals, horizon, kernel)
    try:
        cov = np.linalg.inv(-hess)
    except np.linalg.LinAlgError:
        return np.full(3, np.nan)
    diag = np.diag(cov)
    if np.any(diag < 0):
        return np.full(3, np.nan)
    return np.sqrt(diag)


def powerlaw_loglik(arrivals, horizon, kernel, allow_large: bool = False) -> float:
    model = as_power(kernel)
    stamp = np.asarray(arrivals, dtype=np.float64).reshape(-1)
    n = stamp.size
    if n > MAX_QUADRATIC_EVENTS and not allow_large:
        raise ValueError(
            f"Power-law evaluation is O(N^2) and must not allocate an N×N matrix; "
            f"N={n} exceeds {MAX_QUADRATIC_EVENTS}. "
            "Pass allow_large=True to run a chunked O(N) memory path anyway, "
            "or split the window / use the exponential MLE."
        )
    if n == 0:
        return float(-model.compensator(horizon, stamp))
    rate = np.full(n, model.baseline, dtype=np.float64)
    for start in range(0, n, CHUNK):
        stop = min(start + CHUNK, n)
        wait = stamp[start:stop, None] - stamp[None, :]
        earlier = np.arange(n)[None, :] < np.arange(start, stop)[:, None]
        add = np.zeros_like(wait)
        add[earlier] = model.amplitude / (model.offset + wait[earlier]) ** model.exponent
        rate[start:stop] += add.sum(axis=1)
    return float(np.log(rate).sum() - model.compensator(horizon, stamp))
