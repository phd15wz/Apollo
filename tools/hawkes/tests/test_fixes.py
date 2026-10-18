"""Regression tests for defects in the book's sample ``hawkes.py``."""

import numpy as np
import pytest
from scipy.optimize import fsolve

from tools.hawkes.hawkes import (
    empirical_moments,
    exp_gmm,
    exp_moments,
    gmm_weight_matrix,
    simulate_exp_by_thinning,
    simulate_inverse_compensator,
)

THETA = np.array([1.0, 0.6, 1.0])


def _orig_exp_moments(theta, tau, lag):
    """Variance and autocov as in the book's sample (incorrect)."""
    lam, alpha, beta = theta
    kappa = beta - alpha
    delta = lag * tau
    mean = (lam * beta / kappa) * tau
    var = (lam * beta / kappa) * (
        tau * (beta / kappa)
        + (1 - beta / kappa) * ((1 - np.exp(-kappa * tau)) / kappa)
    )
    auto = (
        lam
        * beta
        * alpha
        * (2 * beta - alpha)
        * (np.exp(-kappa * tau) - 1) ** 2
        / (2 * kappa**4)
    ) * np.exp(-kappa * delta)
    return np.array([[mean], [var], [auto]])


def _book_compensator_no_slice(t, history, theta):
    """Book sample: sums every entry of ``history``, including uninitialised ones."""
    if t <= 0:
        return 0.0
    lam, alpha, beta = theta
    acc = lam * t
    for t_i in history:
        acc += (alpha / beta) * (1.0 - np.exp(-beta * (t - t_i)))
    return acc


def _orig_inverse_compensator(theta, n_events, seed):
    """Sample-code inverse compensator: Lambda(t_prev, full empty buffer)."""
    np.random.seed(seed)
    # Sample code used np.empty; zeros reproduce the usual uninitialised buffer.
    history = np.zeros(n_events, dtype=np.float64)
    target = -np.log(np.random.random())
    history[0] = fsolve(
        lambda x: _book_compensator_no_slice(x[0], history[:0], theta) - target,
        1.0,
    )[0]
    t_prev = history[0]
    for i in range(1, n_events):
        increment = -np.log(np.random.random())
        lam_i = _book_compensator_no_slice(t_prev, history, theta)
        history[i] = fsolve(
            lambda x, prev=lam_i, filled=i: _book_compensator_no_slice(
                x[0], history[:filled], theta
            )
            - prev
            - increment,
            t_prev + 1.0,
        )[0]
        t_prev = history[i]
    return history


def test_exp_moments_variance_matches_eq_712_not_sample_code():
    tau = 1.0
    ours = float(exp_moments(THETA, tau, lag=5)[1, 0])
    orig = float(_orig_exp_moments(THETA, tau, lag=5)[1, 0])
    lam, alpha, beta = THETA
    kappa = beta - alpha
    eq_712 = (lam * beta / kappa) * (
        tau * (beta**2 / kappa**2)
        + (1 - beta**2 / kappa**2) * ((1 - np.exp(-kappa * tau)) / kappa)
    )
    assert ours == eq_712
    assert abs(orig - eq_712) > 1.0

    times = simulate_exp_by_thinning(THETA, 40000.0, seed=0)
    counts = np.histogram(times, bins=np.arange(0.0, 40000.0 + 1e-12, tau))[0]
    emp = float(counts.var())
    assert abs(emp - eq_712) < 0.25
    assert abs(emp - orig) > 0.8


def test_exp_moments_autocov_uses_gap_not_lag_times_tau():
    tau, lag = 1.0, 2
    ours = float(exp_moments(THETA, tau, lag)[2, 0])
    orig = float(_orig_exp_moments(THETA, tau, lag)[2, 0])
    lam, alpha, beta = THETA
    kappa = beta - alpha
    c = alpha * beta * lam * (2 * beta - alpha) / (2 * kappa**2)
    gap_corrected = (
        c
        * (1 - np.exp(-kappa * tau)) ** 2
        / kappa**2
        * np.exp(-kappa * (lag - 1) * tau)
    )
    assert abs(ours - gap_corrected) < 1e-12
    assert abs(orig - gap_corrected) > 0.3

    times = simulate_exp_by_thinning(THETA, 40000.0, seed=1)
    emp = float(empirical_moments(times, 40000.0, tau, lag)[2, 0])
    assert abs(emp - gap_corrected) < 0.15
    assert abs(emp - orig) > 0.2


@pytest.mark.filterwarnings("ignore:The iteration is not making good progress")
def test_inverse_compensator_uses_filled_history_only():
    n_events = 120
    orig_rates = []
    fixed_rates = []
    for seed in range(6):
        with np.errstate(over="ignore", invalid="ignore"):
            orig = _orig_inverse_compensator(THETA, n_events, seed)
        orig_rates.append(n_events / orig[-1])
        fixed = simulate_inverse_compensator(THETA, n_events, seed=seed)
        fixed_rates.append(n_events / fixed[-1])
    theory = 2.5
    assert np.mean(orig_rates) < 0.5
    assert abs(np.mean(fixed_rates) - theory) < 0.35


def test_gmm_weight_matrix_is_full_rank():
    times = simulate_exp_by_thinning(THETA, 20000.0, seed=2)
    T = 20000.0
    tau, lag = 1.0, 2
    emp = empirical_moments(times, T, tau, lag)
    g = emp - exp_moments(THETA, tau, lag)
    s = g @ g.T
    assert np.linalg.matrix_rank(s) == 1
    assert np.linalg.cond(s) > 1e10

    weight = gmm_weight_matrix(times, T, tau, lag, n_blocks=40)
    assert np.linalg.matrix_rank(weight) == 3
    assert np.linalg.cond(weight) < 1e8

    fitted = exp_gmm(times, T, tau=tau, lag=lag, n_blocks=30, iters=1)
    assert fitted[0] > 0 and fitted[1] > 0 and fitted[2] > fitted[1]
