"""Hawkes process simulation, likelihood, and moment matching.

Corrected rewrite inspired by the sample code accompanying

    Laub, P. J., Lee, Y., and Taimre, T. (2021).
    *The Elements of Hawkes Processes*. Springer.

Chapter and equation numbers below refer to that book. This module is original
code written for the Apollo public repository; it is not a verbatim copy of the
book's ``hawkes.py``.

Sample-code defects fixed here
------------------------------
1. ``exp_moments`` variance used :math:`\\beta/\\kappa` where eq. 7.12 has
   :math:`\\beta^2/\\kappa^2` (with :math:`\\kappa = \\beta - \\alpha`).
2. ``exp_moments`` autocovariance used lag gap ``lag * tau`` while
   ``empirical_moments`` compares bins ``lag`` apart, whose interiors are
   separated by ``(lag - 1) * tau``. Eq. 7.13's :math:`\\delta` is that gap.
3. ``simulate_inverse_compensator`` evaluated the compensator on a full
   ``np.empty`` history; only the filled prefix ``H[:i]`` is valid.
4. ``exp_gmm`` set :math:`W = (g g^\\top)^{-1}`, which has rank 1. The weight
   matrix is the inverse of a sample covariance of the moment vector
   (non-overlapping batch means).

Cross-excitation convention (ch. 3 vs ch. 5)
--------------------------------------------
Chapter 3 writes :math:`\\alpha_{i,j}` as the jump in process *i* caused by an
event of process *j* (row = target). Chapter 5 and this toolkit store a matrix
``A`` with ``A[source, target]``: an event of type ``d`` adds the row
``A[d, :]`` to the intensity vector. Stationarity uses the branching matrix
built from that same convention.

EM vs MLE
---------
The textbook EM stores an ``N x N`` responsibility matrix and is infeasible
for large event counts. Default fitting is the :math:`O(N)` exponential MLE.
"""

from __future__ import annotations

import warnings

import numpy as np
from scipy import stats
from scipy.optimize import brentq, minimize

from tools.hawkes._numba import njit

EPS = 1e-8
EM_N_WARN = 2000


def _as_theta(theta) -> np.ndarray:
    return np.asarray(theta, dtype=np.float64).reshape(-1)


def branching_ratio_exp(theta) -> float:
    """:math:`n = \\alpha/\\beta` (book eq. after Theorem 3.1)."""
    _lam, alpha, beta = _as_theta(theta)
    return float(alpha / beta)


def branching_ratio_power(theta) -> float:
    """:math:`n = \\int_0^\\infty k (c+t)^{-p} dt = k / ((p-1) c^{p-1})` for :math:`p>1`."""
    _lam, k, c, p = _as_theta(theta)
    if p <= 1:
        return np.inf
    return float(k / ((p - 1.0) * c ** (p - 1.0)))


def mean_event_rate(theta, kernel: str = "exp") -> float:
    """Stationary mean intensity :math:`\\lambda / (1-n)` (Asmussen, ch. 3)."""
    th = _as_theta(theta)
    lam = float(th[0])
    n = branching_ratio_exp(th) if kernel == "exp" else branching_ratio_power(th)
    if n >= 1:
        return np.inf
    return lam / (1.0 - n)


def is_stationary_exp(theta) -> bool:
    return branching_ratio_exp(theta) < 1.0


def spectral_radius(matrix: np.ndarray) -> float:
    eig = np.linalg.eigvals(np.asarray(matrix, dtype=np.float64))
    return float(np.max(np.abs(eig)))


# ---------------------------------------------------------------------------
# Intensities and compensators
# ---------------------------------------------------------------------------


def exp_hawkes_intensity(t: float, history, theta) -> float:
    """Exponential kernel :math:`\\lambda + \\sum \\alpha e^{-\\beta(t-t_i)}` (eq. 3.3)."""
    lam, alpha, beta = _as_theta(theta)
    h = np.asarray(history, dtype=np.float64)
    if h.size == 0:
        return float(lam)
    dt = t - h
    dt = dt[dt > 0]
    return float(lam + np.sum(alpha * np.exp(-beta * dt)))


def exp_hawkes_compensator(t: float, history, theta) -> float:
    """:math:`\\Lambda(t) = \\lambda t + \\sum (\\alpha/\\beta)(1-e^{-\\beta(t-t_i)})` (eq. 5.6)."""
    if t <= 0:
        return 0.0
    lam, alpha, beta = _as_theta(theta)
    h = np.asarray(history, dtype=np.float64)
    h = h[h < t]
    lam_int = lam * t
    if h.size == 0:
        return float(lam_int)
    return float(lam_int + np.sum((alpha / beta) * (1.0 - np.exp(-beta * (t - h)))))


@njit
def exp_hawkes_compensators(history, theta):
    """O(N) compensator at each event time (eq. 9.x / Ozaki recursion)."""
    lam, alpha, beta = theta
    n = len(history)
    out = np.empty(n, dtype=np.float64)
    lam_prev = lam
    t_prev = 0.0
    acc = 0.0
    for i in range(n):
        t_i = history[i]
        acc += lam * (t_i - t_prev) + ((lam_prev - lam) / beta) * (
            1.0 - np.exp(-beta * (t_i - t_prev))
        )
        out[i] = acc
        lam_prev = lam + (lam_prev - lam) * np.exp(-beta * (t_i - t_prev)) + alpha
        t_prev = t_i
    return out


@njit
def power_hawkes_intensity(t, history, theta):
    lam, k, c, p = theta
    val = lam
    for t_i in history:
        if t_i < t:
            val += k / (c + (t - t_i)) ** p
    return val


@njit
def power_hawkes_compensator(t, history, theta):
    """Antiderivative of :math:`k(c+s)^{-p}`: :math:`\\frac{k}{p-1}[c^{1-p}-(c+t)^{1-p}]`."""
    lam, k, c, p = theta
    acc = lam * t
    inv = 1.0 / (p - 1.0)
    for t_i in history:
        if t_i < t:
            delta = t - t_i
            acc += k * inv * (c ** (1.0 - p) - (c + delta) ** (1.0 - p))
    return acc


@njit
def power_hawkes_compensators(history, theta):
    n = len(history)
    out = np.empty(n, dtype=np.float64)
    for i in range(n):
        out[i] = power_hawkes_compensator(history[i], history[:i], theta)
    return out


# ---------------------------------------------------------------------------
# Likelihood and MLE (default fitter)
# ---------------------------------------------------------------------------


@njit
def exp_log_likelihood(times, T, theta):
    """Ozaki O(N) exponential log-likelihood (eq. 5.7–5.8)."""
    lam, alpha, beta = theta
    n = len(times)
    if n == 0:
        return -lam * T
    a_rec = 0.0
    ll = -lam * T
    t_prev = times[0]
    ll += np.log(lam) - (alpha / beta) * (1.0 - np.exp(-beta * (T - times[0])))
    for i in range(1, n):
        t_i = times[i]
        a_rec = np.exp(-beta * (t_i - t_prev)) * (1.0 + a_rec)
        ll += np.log(lam + alpha * a_rec) - (alpha / beta) * (
            1.0 - np.exp(-beta * (T - t_i))
        )
        t_prev = t_i
    return ll


@njit
def power_log_likelihood(times, T, theta):
    ll = 0.0
    n = len(times)
    for i in range(n):
        ll += np.log(power_hawkes_intensity(times[i], times[:i], theta))
    ll -= power_hawkes_compensator(T, times, theta)
    return ll


@njit
def ozaki_recursion(times, beta, power):
    n = len(times)
    a_n = np.empty(n, dtype=np.float64)
    a_n[0] = 0.0
    for i in range(1, n):
        a_n[i] = np.exp(-beta * (times[i] - times[i - 1])) * (
            times[i - 1] ** power + a_n[i - 1]
        )
    return a_n


@njit
def deriv_exp_log_likelihood(times, T, theta):
    """Score of the exponential log-likelihood (Ozaki; ch. 5)."""
    lam, alpha, beta = theta
    n = len(times)
    dlam = -T
    dalpha = 0.0
    dbeta = 0.0
    if n == 0:
        out = np.empty(3, dtype=np.float64)
        out[0] = dlam
        out[1] = 0.0
        out[2] = 0.0
        return out
    a0 = ozaki_recursion(times, beta, 0)
    a1 = ozaki_recursion(times, beta, 1)
    for i in range(n):
        t_i = times[i]
        den = lam + alpha * a0[i]
        b_i = t_i * a0[i] - a1[i]
        dalpha += (1.0 / beta) * (np.exp(-beta * (T - t_i)) - 1.0) + a0[i] / den
        dbeta += (
            -alpha
            * (
                (1.0 / beta) * (T - t_i) * np.exp(-beta * (T - t_i))
                + (1.0 / beta**2) * (np.exp(-beta * (T - t_i)) - 1.0)
            )
            - (alpha * b_i / den)
        )
        dlam += 1.0 / den
    out = np.empty(3, dtype=np.float64)
    out[0] = dlam
    out[1] = dalpha
    out[2] = dbeta
    return out


@njit
def hess_exp_log_likelihood(times, T, theta):
    lam, alpha, beta = theta
    n = len(times)
    h = np.zeros((3, 3), dtype=np.float64)
    if n == 0:
        return h
    a0 = ozaki_recursion(times, beta, 0)
    a1 = ozaki_recursion(times, beta, 1)
    a2 = ozaki_recursion(times, beta, 2)
    for i in range(n):
        t_i = times[i]
        den = lam + alpha * a0[i]
        b_i = t_i * a0[i] - a1[i]
        c_i = t_i**2 * a0[i] - 2.0 * t_i * a1[i] + a2[i]
        h[1, 1] += -(a0[i] / den) ** 2
        h[1, 2] += -(
            (1.0 / beta) * (T - t_i) * np.exp(-beta * (T - t_i))
            + (1.0 / beta**2) * (np.exp(-beta * (T - t_i)) - 1.0)
        ) + (-b_i / den + (alpha * a0[i] * b_i) / den**2)
        h[2, 2] += alpha * (
            (1.0 / beta) * (T - t_i) ** 2 * np.exp(-beta * (T - t_i))
            + (2.0 / beta**2) * (T - t_i) * np.exp(-beta * (T - t_i))
            + (2.0 / beta**3) * (np.exp(-beta * (T - t_i)) - 1.0)
        ) + (alpha * c_i / den - (alpha * b_i / den) ** 2)
        h[0, 0] += -1.0 / den**2
        h[0, 1] += -a0[i] / den**2
        h[0, 2] += alpha * b_i / den**2
    h[1, 0] = h[0, 1]
    h[2, 0] = h[0, 2]
    h[2, 1] = h[1, 2]
    return h


def _exp_bounds():
    return ((EPS, None), (EPS, None), (EPS, None))


def exp_mle(times, T, theta_start=None):
    """Default fitter: bounded L-BFGS-B maximisation of the O(N) log-likelihood."""
    t = np.asarray(times, dtype=np.float64)
    if theta_start is None:
        theta_start = np.array([1.0, 0.5, 1.5], dtype=np.float64)
    theta_start = _as_theta(theta_start)

    def loss(th):
        return -exp_log_likelihood(t, float(T), th)

    res = minimize(loss, theta_start, bounds=_exp_bounds(), method="L-BFGS-B")
    return np.asarray(res.x, dtype=np.float64)


def exp_mle_with_grad(times, T, theta_start=None):
    t = np.asarray(times, dtype=np.float64)
    if theta_start is None:
        theta_start = np.array([1.0, 0.5, 1.5], dtype=np.float64)
    theta_start = _as_theta(theta_start)

    def loss(th):
        return -exp_log_likelihood(t, float(T), th)

    def grad(th):
        return -deriv_exp_log_likelihood(t, float(T), th)

    res = minimize(
        loss, theta_start, bounds=_exp_bounds(), jac=grad, method="L-BFGS-B"
    )
    return np.asarray(res.x, dtype=np.float64)


def asymptotic_se(times, T, theta):
    """Hessian-based standard errors from the observed information :math:`-\\nabla^2\\ell`."""
    t = np.asarray(times, dtype=np.float64)
    th = _as_theta(theta)
    hess = hess_exp_log_likelihood(t, float(T), th)
    try:
        cov = np.linalg.inv(-hess)
    except np.linalg.LinAlgError:
        return np.full(3, np.nan)
    diag = np.diag(cov)
    if np.any(diag < 0):
        return np.full(3, np.nan)
    return np.sqrt(diag)


def power_mle(times, T, theta_start=None):
    t = np.asarray(times, dtype=np.float64)
    if theta_start is None:
        theta_start = np.array([1.0, 1.0, 1.0, 2.0], dtype=np.float64)
    bounds = ((EPS, None), (EPS, None), (EPS, None), (1.0 + EPS, 20.0))

    def loss(th):
        n = branching_ratio_power(th)
        if not np.isfinite(n) or n >= 0.999:
            return 1e12
        return -power_log_likelihood(t, float(T), th)

    res = minimize(loss, _as_theta(theta_start), bounds=bounds, method="L-BFGS-B")
    return np.asarray(res.x, dtype=np.float64)


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------


@njit
def simulate_exp_by_thinning(theta, T, seed=-1):
    """Ogata thinning for an exponential Hawkes process on ``[0, T]``."""
    if seed >= 0:
        np.random.seed(seed)
    lam, alpha, beta = theta
    lamx = lam
    times = []
    t = 0.0
    while True:
        m = lamx
        dt = np.random.exponential(1.0) / m
        t = t + dt
        if t > T:
            break
        lamx = lam + (lamx - lam) * np.exp(-beta * dt)
        u = m * np.random.random()
        if u > lamx:
            continue
        times.append(t)
        lamx = lamx + alpha
    out = np.empty(len(times), dtype=np.float64)
    for i in range(len(times)):
        out[i] = times[i]
    return out


@njit
def simulate_power_by_thinning(theta, T, seed=-1):
    if seed >= 0:
        np.random.seed(seed)
    lam, k, c, p = theta
    jump = k / (c**p)
    lamx = lam
    times = []
    t = 0.0
    hist = np.empty(0, dtype=np.float64)
    while True:
        m = lamx
        dt = np.random.exponential(1.0) / m
        t = t + dt
        if t > T:
            break
        lamx = power_hawkes_intensity(t, hist, theta)
        u = m * np.random.random()
        if u > lamx:
            continue
        times.append(t)
        new_hist = np.empty(hist.size + 1, dtype=np.float64)
        for i in range(hist.size):
            new_hist[i] = hist[i]
        new_hist[hist.size] = t
        hist = new_hist
        lamx = lamx + jump
    out = np.empty(len(times), dtype=np.float64)
    for i in range(len(times)):
        out[i] = times[i]
    return out


def simulate_inverse_compensator(theta, n_events, compensator=None, seed=None):
    """Invert the compensator to obtain ``n_events`` arrival times (ch. 4).

    Uses only the filled prefix of the history at each step. The book's sample
    called ``Lambda(t_prev, H, theta)`` on the entire ``np.empty`` buffer.
    """
    if seed is not None:
        np.random.seed(int(seed))
    th = _as_theta(theta)
    if compensator is None:
        compensator = exp_hawkes_compensator
    history = np.empty(n_events, dtype=np.float64)
    target = -np.log(np.random.random())
    history[0] = _invert_compensator(compensator, th, 0.0, history[:0], target)
    t_prev = history[0]
    for i in range(1, n_events):
        increment = -np.log(np.random.random())
        filled = history[:i]
        lam_prev = compensator(t_prev, filled, th)
        history[i] = _invert_compensator(
            compensator, th, t_prev, filled, lam_prev + increment
        )
        t_prev = history[i]
    return history


def _invert_compensator(compensator, theta, t_prev, history, target):
    def f(t):
        return compensator(t, history, theta) - target

    lo = t_prev + 1e-12
    hi = max(t_prev + 1.0, t_prev + 1e-6)
    for _ in range(60):
        if f(hi) >= 0:
            break
        hi = t_prev + (hi - t_prev) * 2.0
    return float(brentq(f, lo, hi, xtol=1e-10, maxiter=100))


# ---------------------------------------------------------------------------
# Moments and GMM
# ---------------------------------------------------------------------------


def _count_bins(times, T, tau):
    edges = np.arange(0.0, float(T) + 1e-12, float(tau))
    if edges.size < 2:
        raise ValueError("T must be larger than tau")
    counts, _ = np.histogram(times, bins=edges)
    return counts.astype(np.float64)


def empirical_moments(times, T, tau, lag):
    """Mean, variance, and lag-bin autocovariance of counts in windows of length ``tau``.

    Bins ``i`` and ``i+lag`` have a gap of ``(lag-1)*tau`` between window interiors.
    """
    count = _count_bins(times, T, tau)
    mean = float(count.mean())
    var = float(count.var())
    centred = count - mean
    auto = float(np.mean(centred[:-lag] * centred[lag:]))
    return np.array([[mean], [var], [auto]], dtype=np.float64)


def exp_moments(theta, tau, lag):
    """Limiting mean, variance, and autocovariance of counts (eqs. 7.11–7.13).

    Variance uses :math:`\\beta^2/\\kappa^2` as in eq. 7.12, not the sample-code
    :math:`\\beta/\\kappa`. Autocovariance uses gap :math:`\\delta=(lag-1)\\tau`
    to match ``empirical_moments``.
    """
    lam, alpha, beta = _as_theta(theta)
    kappa = beta - alpha
    if kappa <= 0:
        raise ValueError("need beta > alpha for stationary moments")
    mean = (lam * beta / kappa) * tau
    var = (lam * beta / kappa) * (
        tau * (beta**2 / kappa**2)
        + (1.0 - beta**2 / kappa**2) * ((1.0 - np.exp(-kappa * tau)) / kappa)
    )
    delta = (lag - 1) * tau
    auto = (
        lam
        * beta
        * alpha
        * (2.0 * beta - alpha)
        * (np.exp(-kappa * tau) - 1.0) ** 2
        / (2.0 * kappa**4)
    ) * np.exp(-kappa * delta)
    return np.array([[mean], [var], [auto]], dtype=np.float64)


def _block_moment_covariance(counts, lag, n_blocks=40):
    """Sample covariance of (mean, var, autocov) across non-overlapping blocks."""
    n = len(counts)
    n_blocks = min(int(n_blocks), max(n // (lag + 8), 4))
    blocks = np.array_split(counts, n_blocks)
    rows = []
    for block in blocks:
        if block.size <= lag + 2:
            continue
        mean = float(block.mean())
        var = float(block.var())
        centred = block - mean
        auto = float(np.mean(centred[:-lag] * centred[lag:]))
        rows.append((mean, var, auto))
    mat = np.asarray(rows, dtype=np.float64)
    if mat.shape[0] < 4:
        return np.cov(mat.T) if mat.shape[0] > 1 else np.eye(3)
    return np.cov(mat.T, ddof=1) / mat.shape[0]


def gmm_weight_matrix(times, T, tau, lag, n_blocks=40):
    """:math:`W = \\hat{V}^{-1}` from batch-means covariance of the moment vector.

    Replaces the sample-code :math:`W=(gg^\\top)^{-1}` (rank 1).
    """
    counts = _count_bins(times, T, tau)
    v = _block_moment_covariance(counts, lag, n_blocks=n_blocks)
    v = v + 1e-10 * np.eye(3)
    return np.linalg.inv(v)


def _gmm_loss(theta, tau, lag, emp, weight):
    lam, alpha, beta = theta
    if min(theta) <= 0 or alpha >= beta:
        return 1e12
    g = emp - exp_moments(theta, tau, lag)
    return float((g.T @ weight @ g)[0, 0])


def exp_gmm(
    times,
    T,
    tau=1.0,
    lag=2,
    theta_start=None,
    n_blocks=40,
    iters=2,
):
    """Two-step GMM using a full-rank batch-means weight matrix."""
    t = np.asarray(times, dtype=np.float64)
    emp = empirical_moments(t, T, tau, lag)
    if theta_start is None:
        theta_start = np.array([1.0, 0.3, 1.5], dtype=np.float64)
    bounds = ((EPS, None), (EPS, None), (EPS, None))
    weight = np.eye(3)
    theta = _as_theta(theta_start)
    for _ in range(iters + 1):
        res = minimize(
            _gmm_loss,
            theta,
            args=(tau, lag, emp, weight),
            bounds=bounds,
            method="Nelder-Mead",
            options={"maxiter": 4000, "xatol": 1e-6, "fatol": 1e-10},
        )
        theta = np.asarray(res.x, dtype=np.float64)
        weight = gmm_weight_matrix(t, T, tau, lag, n_blocks=n_blocks)
        weight = weight / max(np.max(np.abs(weight)), 1.0)
    return theta


# ---------------------------------------------------------------------------
# EM (not the default fitter)
# ---------------------------------------------------------------------------


def exp_em(times, T, theta_start=None, iters=50, allow_large=False):
    """Exponential EM (ch. 6). O(N^2) time.

    The textbook implementation materialises an N×N responsibility matrix.
    This version streams rows so peak extra memory is O(N), but runtime is
    still quadratic. Use ``exp_mle`` / ``exp_mle_with_grad`` for large N.
    """
    t = np.asarray(times, dtype=np.float64)
    n = t.size
    if n > EM_N_WARN and not allow_large:
        raise ValueError(
            f"EM is O(N^2); N={n} exceeds {EM_N_WARN}. "
            "Pass allow_large=True to run anyway, or use exp_mle."
        )
    if n > EM_N_WARN:
        warnings.warn(
            "Exponential EM is O(N^2) and is not the default fitter.",
            RuntimeWarning,
            stacklevel=2,
        )
    if theta_start is None:
        theta_start = np.array([1.0, 0.5, 1.5], dtype=np.float64)
    theta = _as_theta(theta_start).copy()
    for _ in range(iters):
        theta = _exp_em_iter(t, float(T), theta)
    return theta


@njit
def _exp_em_iter(times, T, theta):
    lam, alpha, beta = theta
    n = len(times)
    resp0_sum = 0.0
    resp_off_sum = 0.0
    time_weighted = 0.0
    for i in range(n):
        if i == 0:
            resp0_sum += 1.0
            continue
        row0 = lam
        row_sum = lam
        # unnormalised offspring masses stored on the fly
        # first pass: row sum
        for j in range(i):
            mass = alpha * np.exp(-beta * (times[i] - times[j]))
            row_sum += mass
        resp0_sum += row0 / row_sum
        for j in range(i):
            mass = alpha * np.exp(-beta * (times[i] - times[j])) / row_sum
            resp_off_sum += mass
            time_weighted += (times[i] - times[j]) * mass
    lam = resp0_sum / T
    denom_alpha = 0.0
    numer_beta = 0.0
    for i in range(n):
        e = np.exp(-beta * (T - times[i]))
        denom_alpha += 1.0 - e
        numer_beta += (1.0 - e) / beta - (T - times[i]) * e
    alpha = beta * resp_off_sum / denom_alpha
    beta = alpha * numer_beta / time_weighted
    out = np.empty(3, dtype=np.float64)
    out[0] = lam
    out[1] = alpha
    out[2] = beta
    return out


# ---------------------------------------------------------------------------
# Goodness of fit
# ---------------------------------------------------------------------------


def time_rescaled_residuals(times, T, theta, kernel="exp"):
    """Compensator increments; unit-rate Poisson iff the model is correct (Thm 9.2)."""
    t = np.asarray(times, dtype=np.float64)
    th = _as_theta(theta)
    if kernel == "exp":
        lam = exp_hawkes_compensators(t, th)
        lam_T = exp_hawkes_compensator(float(T), t, th)
    else:
        lam = power_hawkes_compensators(t, th)
        lam_T = power_hawkes_compensator(float(T), t, th)
    stamps = np.concatenate([np.array([0.0]), lam, np.array([lam_T])])
    return np.diff(stamps)


def ks_exponential_residuals(residuals):
    """Kolmogorov–Smirnov test of residuals against Exp(1)."""
    r = np.asarray(residuals, dtype=np.float64)
    r = r[np.isfinite(r) & (r >= 0)]
    result = stats.kstest(r, "expon", args=(0, 1))
    return float(result.statistic), float(result.pvalue)


# ---------------------------------------------------------------------------
# Multivariate exponential Hawkes (ch. 5 indexing)
# ---------------------------------------------------------------------------


def flatten_theta(theta):
    lam, alpha, beta = theta
    return np.hstack([np.asarray(lam).ravel(), np.asarray(alpha).ravel(), np.asarray(beta).ravel()])


def unflatten_theta(flat, m):
    flat = np.asarray(flat, dtype=np.float64)
    lam = flat[:m]
    alpha = flat[m : m + m * m].reshape((m, m))
    beta = flat[m + m * m :]
    return lam, alpha, beta


def mutual_branching_matrix(theta):
    """:math:`\\Phi_{k,j} = A_{j,k} / \\beta_k` (expected type-k offspring of a type-j event).

    ``A[source, target]`` follows chapter 5, opposite to chapter 3's
    :math:`\\alpha_{i,j}` (target, source).
    """
    lam, alpha, beta = theta
    beta = np.asarray(beta, dtype=np.float64)
    alpha = np.asarray(alpha, dtype=np.float64)
    # Phi[target, source] = A[source, target] / beta[target]
    return (alpha.T) / beta[:, None]


def mutual_is_stationary(theta) -> bool:
    return spectral_radius(mutual_branching_matrix(theta)) < 1.0


@njit
def mutual_exp_hawkes_intensity(t, times, ids, theta_lam, theta_alpha, theta_beta):
    m = len(theta_lam)
    lamx = theta_lam.copy()
    n = len(times)
    for i in range(n):
        t_i = times[i]
        if t_i >= t:
            break
        d_i = ids[i]
        for k in range(m):
            lamx[k] = lamx[k] + theta_alpha[d_i, k] * np.exp(-theta_beta[k] * (t - t_i))
    return lamx


def mutual_exp_simulate_by_thinning(theta, T, seed=None):
    if seed is not None:
        np.random.seed(int(seed))
    lam, alpha, beta = theta
    lam = np.asarray(lam, dtype=np.float64).copy()
    alpha = np.asarray(alpha, dtype=np.float64)
    beta = np.asarray(beta, dtype=np.float64)
    m = lam.size
    lamx = lam.copy()
    times = []
    ids = []
    t = 0.0
    while True:
        total = float(np.sum(lamx))
        dt = np.random.exponential(1.0) / total
        t += dt
        if t > T:
            break
        lamx = lam + (lamx - lam) * np.exp(-beta * dt)
        u = total * np.random.random()
        if u > float(np.sum(lamx)):
            continue
        cdf = 0.0
        for k in range(m):
            cdf += lamx[k]
            if u < cdf:
                times.append(t)
                ids.append(k)
                lamx = lamx + alpha[k]
                break
    return np.asarray(times, dtype=np.float64), np.asarray(ids, dtype=np.int64)


@njit
def mutual_exp_log_likelihood(times, ids, T, lam, alpha, beta):
    if np.min(lam) <= 0 or np.min(alpha) < 0 or np.min(beta) <= 0:
        return -np.inf
    n = len(times)
    m = len(lam)
    ll = 0.0
    lamx = lam.copy()
    t_prev = 0.0
    for i in range(n):
        t_i = times[i]
        d_i = ids[i]
        for k in range(m):
            lamx[k] = lam[k] + (lamx[k] - lam[k]) * np.exp(-beta[k] * (t_i - t_prev))
        ll += np.log(lamx[d_i])
        for k in range(m):
            lamx[k] = lamx[k] + alpha[d_i, k]
        t_prev = t_i
    # compensator to T
    for i in range(n):
        t_i = times[i]
        d_i = ids[i]
        for k in range(m):
            ll -= (alpha[d_i, k] / beta[k]) * (1.0 - np.exp(-beta[k] * (T - t_i)))
    for k in range(m):
        ll -= lam[k] * T
    return ll


def mutual_exp_mle(times, ids, T, theta_start):
    t = np.asarray(times, dtype=np.float64)
    d = np.asarray(ids, dtype=np.int64)
    m = len(theta_start[0])
    x0 = flatten_theta(theta_start)

    def loss(flat):
        lam, alpha, beta = unflatten_theta(flat, m)
        return -mutual_exp_log_likelihood(t, d, float(T), lam, alpha, beta)

    res = minimize(loss, x0, method="Nelder-Mead", options={"maxiter": 4000})
    return unflatten_theta(res.x, m), float(-res.fun)
