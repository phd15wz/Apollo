import numpy as np
import pytest
from scipy.integrate import quad

from tools.hawkes.hawkes import (
    asymptotic_se,
    deriv_exp_log_likelihood,
    exp_em,
    exp_hawkes_compensators,
    exp_log_likelihood,
    exp_mle_with_grad,
    mean_event_rate,
    mutual_branching_matrix,
    mutual_exp_simulate_by_thinning,
    mutual_is_stationary,
    power_hawkes_compensator,
    simulate_exp_by_thinning,
    simulate_power_by_thinning,
    time_rescaled_residuals,
)

THETA = np.array([1.0, 0.6, 1.0])


def test_score_matches_finite_difference():
    times = simulate_exp_by_thinning(THETA, 1500.0, seed=3)
    t = times[:2500] if times.size > 2500 else times
    T = float(t[-1] + 1.0)
    analytic = deriv_exp_log_likelihood(t, T, THETA)
    eps = 1e-6
    fd = np.empty(3)
    for i in range(3):
        e = np.zeros(3)
        e[i] = eps
        fd[i] = (
            exp_log_likelihood(t, T, THETA + e)
            - exp_log_likelihood(t, T, THETA - e)
        ) / (2 * eps)
    np.testing.assert_allclose(analytic, fd, rtol=1e-4, atol=2e-3)


def test_mle_recovers_simulated_parameters():
    T = 4000.0
    times = simulate_exp_by_thinning(THETA, T, seed=4)
    hat = exp_mle_with_grad(times, T, theta_start=np.array([0.8, 0.4, 1.3]))
    np.testing.assert_allclose(hat, THETA, rtol=0.12, atol=0.12)
    se = asymptotic_se(times, T, hat)
    assert np.all(np.isfinite(se))
    assert np.all(se > 0)


def test_thinning_event_rate_matches_lambda_over_one_minus_n():
    T = 8000.0
    times = simulate_exp_by_thinning(THETA, T, seed=5)
    rate = times.size / T
    theory = mean_event_rate(THETA)
    assert abs(rate - theory) / theory < 0.04
    assert abs(theory - 2.5) < 1e-12


def test_power_compensator_matches_numerical_integral():
    theta = np.array([0.4, 1.1, 0.8, 1.7])
    times = np.array([0.2, 0.9, 1.4, 2.2])
    T = 3.0
    analytic = power_hawkes_compensator(T, times, theta)
    lam, k, c, p = theta
    numeric = lam * T
    for t_i in times:
        piece, _ = quad(lambda s, kk=k, cc=c, pp=p: kk / (cc + s) ** pp, 0.0, T - t_i)
        numeric += piece
    assert abs(analytic - numeric) < 1e-6


def test_power_thinning_runs_and_is_stationary():
    theta = np.array([0.5, 0.4, 1.0, 2.0])
    times = simulate_power_by_thinning(theta, 200.0, seed=6)
    assert times.size > 20
    assert times[0] > 0
    assert np.all(np.diff(times) > 0)


def test_compensator_residuals_are_roughly_unit_exponential():
    T = 2000.0
    times = simulate_exp_by_thinning(THETA, T, seed=7)
    residuals = time_rescaled_residuals(times, T, THETA)
    assert abs(residuals.mean() - 1.0) < 0.08
    stamps = exp_hawkes_compensators(times, THETA)
    assert stamps[-1] > stamps[0]


def test_em_refuses_large_n_by_default():
    times = np.linspace(0.01, 10.0, 2001)
    with pytest.raises(ValueError, match="O\\(N\\^2\\)"):
        exp_em(times, 10.0, iters=1)


def test_mutual_index_convention_source_row_target_col():
    lam = np.array([0.4, 0.5])
    # Event of type 0 jumps type-1 intensity by 0.3, not the reverse.
    alpha = np.array([[0.2, 0.3], [0.0, 0.15]])
    beta = np.array([1.0, 1.2])
    theta = (lam, alpha, beta)
    phi = mutual_branching_matrix(theta)
    assert phi.shape == (2, 2)
    # Phi[target, source] = A[source, target] / beta[target]
    np.testing.assert_allclose(phi[1, 0], 0.3 / 1.2)
    np.testing.assert_allclose(phi[0, 1], 0.0)
    assert mutual_is_stationary(theta)
    times, ids = mutual_exp_simulate_by_thinning(theta, 400.0, seed=8)
    assert times.size > 50
    assert set(np.unique(ids)).issubset({0, 1})
