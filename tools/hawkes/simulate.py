"""Simulation of linear Hawkes processes on a finite horizon (ch. 4).

Thinning follows Ogata. Time-change inversion uses only the filled prefix of
the arrival array at each step (a full uninitialised buffer is invalid).
"""

from __future__ import annotations

import numpy as np
from scipy.optimize import brentq

from tools.hawkes._stationarity import require_or_cap
from tools.hawkes.kernels import ExpKernel, PowerLawKernel, as_exp, as_power


def simulate_thinning(
    kernel,
    horizon: float,
    seed: int = -1,
    allow_nonstationary: bool = False,
    max_events: int | None = None,
):
    """Ogata thinning on ``[0, horizon]``. Accepts ExpKernel or PowerLawKernel.

    Non-stationary kernels (branching ratio >= 1) raise ``ValueError`` unless
    ``allow_nonstationary=True``. In that mode a finite event cap still applies.
    Hitting the cap raises ``RuntimeError``; the function does **not** return a
    truncated path. Default cap is ``max(2000, 100 * horizon * max(baseline, 1))``.
    Pass a larger ``max_events`` to raise it.
    """
    if seed >= 0:
        np.random.seed(int(seed))
    if isinstance(kernel, PowerLawKernel) or (
        not isinstance(kernel, ExpKernel) and np.asarray(kernel).size == 4
    ):
        return _thin_power(
            as_power(kernel), horizon, allow_nonstationary, max_events
        )
    return _thin_exp(as_exp(kernel), horizon, allow_nonstationary, max_events)


def _thin_exp(
    model: ExpKernel,
    horizon: float,
    allow_nonstationary: bool,
    max_events: int | None,
) -> np.ndarray:
    cap = require_or_cap(
        model.branching_ratio(),
        model.baseline,
        horizon,
        allow_nonstationary=allow_nonstationary,
        max_events=max_events,
    )
    rate = float(model.baseline)
    arrivals = []
    t = 0.0
    while True:
        bound = rate
        wait = np.random.exponential(1.0) / bound
        t += wait
        if t > horizon:
            break
        rate = model.baseline + (rate - model.baseline) * np.exp(-model.decay * wait)
        if bound * np.random.random() > rate:
            continue
        arrivals.append(t)
        rate += model.jump
        if cap is not None and len(arrivals) >= cap:
            raise RuntimeError(
                f"non-stationary Hawkes exceeded event cap {cap} before T={horizon}"
            )
    return np.asarray(arrivals, dtype=np.float64)


def _thin_power(
    model: PowerLawKernel,
    horizon: float,
    allow_nonstationary: bool,
    max_events: int | None,
) -> np.ndarray:
    cap = require_or_cap(
        model.branching_ratio(),
        model.baseline,
        horizon,
        allow_nonstationary=allow_nonstationary,
        kind="power-law branching ratio",
        max_events=max_events,
    )
    bump = model.amplitude / (model.offset**model.exponent)
    rate = float(model.baseline)
    arrivals = []
    t = 0.0
    hist = np.empty(0, dtype=np.float64)
    while True:
        bound = rate
        wait = np.random.exponential(1.0) / bound
        t += wait
        if t > horizon:
            break
        rate = model.intensity(t, hist)
        if bound * np.random.random() > rate:
            continue
        arrivals.append(t)
        hist = np.asarray(arrivals, dtype=np.float64)
        rate += bump
        if cap is not None and hist.size >= cap:
            raise RuntimeError(
                f"non-stationary Hawkes exceeded event cap {cap} before T={horizon}"
            )
    return hist


def simulate_by_time_change(
    kernel,
    n_events: int,
    seed=None,
    allow_nonstationary: bool = False,
) -> np.ndarray:
    """Invert Lambda until ``n_events`` arrivals exist. Uses history[:i] only.

    Non-stationary kernels raise ``ValueError`` unless
    ``allow_nonstationary=True``. This path asks for a fixed event count, so it
    has no horizon event cap; thinning simulators still raise ``RuntimeError``
    (and do not return a truncated path) if their cap is hit. Raise that cap
    with ``max_events`` on ``simulate_thinning`` / ``simulate_marked_thinning``.
    """
    if seed is not None:
        np.random.seed(int(seed))
    model = as_exp(kernel)
    require_or_cap(
        model.branching_ratio(),
        model.baseline,
        1.0,
        allow_nonstationary=allow_nonstationary,
    )
    arrivals = np.empty(n_events, dtype=np.float64)
    target = -np.log(np.random.random())
    arrivals[0] = _root_compensator(model, 0.0, arrivals[:0], target)
    prev = arrivals[0]
    for i in range(1, n_events):
        step = -np.log(np.random.random())
        filled = arrivals[:i]
        at_prev = model.compensator(prev, filled)
        arrivals[i] = _root_compensator(model, prev, filled, at_prev + step)
        prev = arrivals[i]
    return arrivals


def _root_compensator(model: ExpKernel, t_prev: float, filled, target: float) -> float:
    def residual(t):
        return model.compensator(t, filled) - target

    lo = t_prev + 1e-12
    hi = max(t_prev + 1.0, t_prev + 1e-6)
    for _ in range(60):
        if residual(hi) >= 0:
            break
        hi = t_prev + (hi - t_prev) * 2.0
    return float(brentq(residual, lo, hi, xtol=1e-10, maxiter=100))
