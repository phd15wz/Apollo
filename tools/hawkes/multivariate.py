"""Marked / mutually exciting exponential Hawkes (Definition 3.3, ch. 5 indexing).

Chapter 3 writes alpha_{i,j} as the jump in process i (target) from type j.
This package stores jumps[source, target]: a type-d event adds jumps[d, :].
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import minimize

from tools.hawkes._stationarity import (
    matrix_spectral_radius,
    require_or_cap,
    warn_if_explosive,
)
from tools.hawkes.estimate import FitResult


@dataclass(frozen=True)
class MarkedExpKernel:
    baseline: np.ndarray
    jumps: np.ndarray
    decays: np.ndarray

    def __post_init__(self):
        object.__setattr__(self, "baseline", np.asarray(self.baseline, dtype=np.float64))
        object.__setattr__(self, "jumps", np.asarray(self.jumps, dtype=np.float64))
        object.__setattr__(self, "decays", np.asarray(self.decays, dtype=np.float64))

    @property
    def dim(self) -> int:
        return int(self.baseline.size)

    def as_vector(self) -> np.ndarray:
        return np.hstack([self.baseline.ravel(), self.jumps.ravel(), self.decays.ravel()])

    @classmethod
    def from_vector(cls, flat, dim: int) -> MarkedExpKernel:
        flat = np.asarray(flat, dtype=np.float64)
        baseline = flat[:dim]
        jumps = flat[dim : dim + dim * dim].reshape((dim, dim))
        decays = flat[dim + dim * dim :]
        return cls(baseline, jumps, decays)

    def branching_matrix(self) -> np.ndarray:
        # Phi[target, source] = jumps[source, target] / decays[target]
        return self.jumps.T / self.decays[:, None]

    def is_stationary(self) -> bool:
        return matrix_spectral_radius(self.branching_matrix()) < 1.0

    def intensity(self, t, arrivals, marks) -> np.ndarray:
        arrivals = np.asarray(arrivals, dtype=np.float64)
        marks = np.asarray(marks, dtype=np.int64)
        past = arrivals < t
        if not np.any(past):
            return self.baseline.copy()
        wait = t - arrivals[past]
        contrib = self.jumps[marks[past]] * np.exp(-self.decays * wait[:, None])
        return self.baseline + contrib.sum(axis=0)


def marked_loglik(arrivals, marks, horizon, model: MarkedExpKernel) -> float:
    arrivals = np.asarray(arrivals, dtype=np.float64)
    marks = np.asarray(marks, dtype=np.int64)
    if np.min(model.baseline) <= 0 or np.min(model.jumps) < 0 or np.min(model.decays) <= 0:
        return -np.inf
    logp = 0.0
    rate = model.baseline.copy()
    prev = 0.0
    for i in range(arrivals.size):
        rate = model.baseline + (rate - model.baseline) * np.exp(
            -model.decays * (arrivals[i] - prev)
        )
        logp += np.log(rate[marks[i]])
        rate = rate + model.jumps[marks[i]]
        prev = arrivals[i]
    remain = horizon - arrivals
    logp -= float(np.sum(model.baseline * horizon))
    logp -= float(
        np.sum(
            (model.jumps[marks] / model.decays)
            * (1.0 - np.exp(-model.decays * remain[:, None]))
        )
    )
    return logp


def simulate_marked_thinning(
    model: MarkedExpKernel,
    horizon: float,
    seed=None,
    allow_nonstationary: bool = False,
):
    if seed is not None:
        np.random.seed(int(seed))
    rho = matrix_spectral_radius(model.branching_matrix())
    cap = require_or_cap(
        rho,
        float(np.max(model.baseline)),
        horizon,
        allow_nonstationary=allow_nonstationary,
        kind="spectral radius",
    )
    rate = model.baseline.copy()
    arrivals = []
    marks = []
    t = 0.0
    while True:
        total = float(rate.sum())
        wait = np.random.exponential(1.0) / total
        t += wait
        if t > horizon:
            break
        rate = model.baseline + (rate - model.baseline) * np.exp(-model.decays * wait)
        draw = total * np.random.random()
        if draw > float(rate.sum()):
            continue
        k = int(np.searchsorted(np.cumsum(rate), draw))
        k = min(k, model.dim - 1)
        arrivals.append(t)
        marks.append(k)
        rate = rate + model.jumps[k]
        if cap is not None and len(arrivals) >= cap:
            raise RuntimeError(
                f"non-stationary Hawkes exceeded event cap {cap} before T={horizon}"
            )
    return np.asarray(arrivals, dtype=np.float64), np.asarray(marks, dtype=np.int64)


def fit_marked_mle(arrivals, marks, horizon, start: MarkedExpKernel) -> FitResult:
    stamp = np.asarray(arrivals, dtype=np.float64)
    ids = np.asarray(marks, dtype=np.int64)
    dim = start.dim
    x0 = start.as_vector()

    def loss(flat):
        trial = MarkedExpKernel.from_vector(flat, dim)
        return -marked_loglik(stamp, ids, float(horizon), trial)

    opt = minimize(loss, x0, method="Nelder-Mead", options={"maxiter": 4000})
    hat = MarkedExpKernel.from_vector(opt.x, dim)
    rho = matrix_spectral_radius(hat.branching_matrix())
    warn_if_explosive(rho, label="spectral radius")
    return FitResult(
        kernel=hat,
        loglik=float(-opt.fun),
        branching_ratio=rho,
    )
