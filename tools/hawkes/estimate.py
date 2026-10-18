"""Parameter estimation: MLE (default), GMM, and EM.

MLE uses the O(N) exponential likelihood (eqs. 5.7–5.8). EM is O(N^2) and
capped. GMM uses eqs. 7.11–7.13 with a full-rank batch-means weight matrix.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import warnings

import numpy as np
from scipy.optimize import minimize

from tools.hawkes._limits import MAX_QUADRATIC_EVENTS, POS_EPS
from tools.hawkes._stationarity import warn_if_explosive
from tools.hawkes.kernels import ExpKernel, PowerLawKernel, as_exp, as_power
from tools.hawkes.likelihood import (
    exponential_loglik,
    exponential_score,
    hessian_stderr,
    powerlaw_loglik,
)
from tools.hawkes.moments import (
    batch_mean_weights,
    count_bin_moments,
    exponential_count_moments,
)


@dataclass
class FitResult:
    kernel: object
    loglik: float | None
    branching_ratio: float
    stderr: np.ndarray | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def params(self) -> np.ndarray:
        return self.kernel.as_vector()


def _exp_bounds():
    return ((POS_EPS, None), (POS_EPS, None), (POS_EPS, None))


def fit_exponential_mle(
    arrivals,
    horizon,
    start=None,
    use_score: bool = False,
) -> FitResult:
    stamp = np.asarray(arrivals, dtype=np.float64)
    x0 = (
        as_exp(start).as_vector()
        if start is not None
        else np.array([1.0, 0.5, 1.5], dtype=np.float64)
    )

    def loss(vec):
        return -exponential_loglik(stamp, float(horizon), vec)

    kwargs = {"method": "L-BFGS-B", "bounds": _exp_bounds()}
    if use_score:
        kwargs["jac"] = lambda vec: -exponential_score(stamp, float(horizon), vec)
    opt = minimize(loss, x0, **kwargs)
    hat = ExpKernel.from_vector(opt.x)
    warn_if_explosive(hat.branching_ratio())
    se = hessian_stderr(stamp, horizon, hat) if use_score else None
    return FitResult(
        kernel=hat,
        loglik=float(-opt.fun),
        branching_ratio=hat.branching_ratio(),
        stderr=se,
    )


# Stationary default: n = 0.5 / ((2-1) * 1^{1}) = 0.5. The previous [1,1,1,2]
# start had n = 1, so the loss barrier at n >= 0.999 never moved.
DEFAULT_POWERLAW_START = np.array([1.0, 0.5, 1.0, 2.0], dtype=np.float64)


def fit_powerlaw_mle(arrivals, horizon, start=None, allow_large: bool = False) -> FitResult:
    stamp = np.asarray(arrivals, dtype=np.float64)
    n = stamp.size
    if n > MAX_QUADRATIC_EVENTS and not allow_large:
        raise ValueError(
            f"Power-law evaluation is O(N^2) and must not allocate an N×N matrix; "
            f"N={n} exceeds {MAX_QUADRATIC_EVENTS}. "
            "Pass allow_large=True to run a chunked O(N) memory path anyway, "
            "or split the window / use the exponential MLE."
        )
    x0 = as_power(start).as_vector() if start is not None else DEFAULT_POWERLAW_START.copy()
    bounds = ((POS_EPS, None), (POS_EPS, None), (POS_EPS, None), (1.0 + POS_EPS, 20.0))

    def loss(vec):
        ratio = PowerLawKernel.from_vector(vec).branching_ratio()
        if not np.isfinite(ratio) or ratio >= 0.999:
            return 1e12
        return -powerlaw_loglik(stamp, float(horizon), vec, allow_large=allow_large)

    opt = minimize(loss, x0, bounds=bounds, method="L-BFGS-B")
    hat = PowerLawKernel.from_vector(opt.x)
    warn_if_explosive(hat.branching_ratio(), label="power-law branching ratio")
    return FitResult(
        kernel=hat,
        loglik=float(-opt.fun),
        branching_ratio=hat.branching_ratio(),
    )


def _gmm_objective(vec, width, lag, empirical, weight):
    if min(vec) <= 0 or vec[1] >= vec[2]:
        return 1e12
    gap = empirical - exponential_count_moments(vec, width, lag)
    return float((gap.T @ weight @ gap)[0, 0])


def fit_exponential_gmm(
    arrivals,
    horizon,
    width: float = 1.0,
    lag: int = 2,
    start=None,
    n_blocks: int = 40,
    rounds: int = 2,
) -> FitResult:
    stamp = np.asarray(arrivals, dtype=np.float64)
    empirical = count_bin_moments(stamp, horizon, width, lag)
    x0 = (
        as_exp(start).as_vector()
        if start is not None
        else np.array([1.0, 0.3, 1.5], dtype=np.float64)
    )
    weight = np.eye(3)
    vec = x0
    for _ in range(rounds + 1):
        opt = minimize(
            _gmm_objective,
            vec,
            args=(width, lag, empirical, weight),
            bounds=_exp_bounds(),
            method="Nelder-Mead",
            options={"maxiter": 4000, "xatol": 1e-6, "fatol": 1e-10},
        )
        vec = np.asarray(opt.x, dtype=np.float64)
        weight = batch_mean_weights(stamp, horizon, width, lag, n_blocks=n_blocks)
        weight = weight / max(np.max(np.abs(weight)), 1.0)
    hat = ExpKernel.from_vector(vec)
    warn_if_explosive(hat.branching_ratio())
    return FitResult(
        kernel=hat,
        loglik=None,
        branching_ratio=hat.branching_ratio(),
    )


def fit_exponential_em(
    arrivals,
    horizon,
    start=None,
    iters: int = 50,
    allow_large: bool = False,
) -> FitResult:
    stamp = np.asarray(arrivals, dtype=np.float64)
    n = stamp.size
    if n > MAX_QUADRATIC_EVENTS and not allow_large:
        raise ValueError(
            f"EM is O(N^2); N={n} exceeds {MAX_QUADRATIC_EVENTS}. "
            "Pass allow_large=True to run anyway, or use fit_exponential_mle."
        )
    if n > MAX_QUADRATIC_EVENTS:
        warnings.warn(
            "Exponential EM is O(N^2) and is not the default fitter.",
            RuntimeWarning,
            stacklevel=2,
        )
    vec = (
        as_exp(start).as_vector().copy()
        if start is not None
        else np.array([1.0, 0.5, 1.5], dtype=np.float64)
    )
    for _ in range(iters):
        vec = _em_step(stamp, float(horizon), vec)
    hat = ExpKernel.from_vector(vec)
    warn_if_explosive(hat.branching_ratio())
    return FitResult(
        kernel=hat,
        loglik=exponential_loglik(stamp, horizon, hat),
        branching_ratio=hat.branching_ratio(),
    )


def _em_step(arrivals: np.ndarray, horizon: float, vec: np.ndarray) -> np.ndarray:
    baseline, jump, decay = vec
    n = arrivals.size
    immigrant = 1.0 if n else 0.0
    offspring = 0.0
    lag_mass = 0.0
    for i in range(1, n):
        waits = arrivals[i] - arrivals[:i]
        masses = jump * np.exp(-decay * waits)
        total = baseline + masses.sum()
        immigrant += baseline / total
        probs = masses / total
        offspring += probs.sum()
        lag_mass += np.dot(waits, probs)
    fade = np.exp(-decay * (horizon - arrivals))
    denom = np.sum(1.0 - fade)
    numer = np.sum((1.0 - fade) / decay - (horizon - arrivals) * fade)
    baseline = immigrant / horizon
    jump = decay * offspring / denom
    decay = jump * numer / lag_mass
    return np.array([baseline, jump, decay], dtype=np.float64)
