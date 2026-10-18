import numpy as np
import pytest
from scipy.integrate import quad

from tools.hawkes import (
    DEFAULT_POWERLAW_START,
    ExpKernel,
    MarkedExpKernel,
    PowerLawKernel,
    compensator_increments,
    exponential_loglik,
    exponential_score,
    fit_exponential_em,
    fit_exponential_mle,
    fit_powerlaw_mle,
    powerlaw_loglik,
    simulate_marked_thinning,
    simulate_thinning,
)

TRUE = ExpKernel(1.0, 0.6, 1.0)


def test_score_matches_finite_difference():
    arrivals = simulate_thinning(TRUE, 1500.0, seed=3)
    stamp = arrivals[:2500] if arrivals.size > 2500 else arrivals
    horizon = float(stamp[-1] + 1.0)
    analytic = exponential_score(stamp, horizon, TRUE)
    eps = 1e-6
    fd = np.empty(3)
    eye = np.eye(3)
    for i in range(3):
        fd[i] = (
            exponential_loglik(stamp, horizon, TRUE.as_vector() + eps * eye[i])
            - exponential_loglik(stamp, horizon, TRUE.as_vector() - eps * eye[i])
        ) / (2 * eps)
    np.testing.assert_allclose(analytic, fd, rtol=1e-4, atol=2e-3)


def test_mle_recovers_simulated_parameters():
    horizon = 4000.0
    arrivals = simulate_thinning(TRUE, horizon, seed=4)
    result = fit_exponential_mle(
        arrivals, horizon, start=ExpKernel(0.8, 0.4, 1.3), use_score=True
    )
    np.testing.assert_allclose(result.params, TRUE.as_vector(), rtol=0.12, atol=0.12)
    assert result.stderr is not None
    assert np.all(np.isfinite(result.stderr))
    assert np.all(result.stderr > 0)


def test_thinning_event_rate_matches_baseline_over_one_minus_n():
    horizon = 8000.0
    arrivals = simulate_thinning(TRUE, horizon, seed=5)
    rate = arrivals.size / horizon
    theory = TRUE.mean_intensity()
    assert abs(rate - theory) / theory < 0.04
    assert abs(theory - 2.5) < 1e-12


def test_powerlaw_compensator_matches_numerical_integral():
    kernel = PowerLawKernel(0.4, 1.1, 0.8, 1.7)
    arrivals = np.array([0.2, 0.9, 1.4, 2.2])
    horizon = 3.0
    analytic = kernel.compensator(horizon, arrivals)
    numeric = kernel.baseline * horizon
    for stamp in arrivals:
        piece, _ = quad(
            lambda s, amp=kernel.amplitude, off=kernel.offset, exp=kernel.exponent: amp
            / (off + s) ** exp,
            0.0,
            horizon - stamp,
        )
        numeric += piece
    assert abs(analytic - numeric) < 1e-6


def test_powerlaw_mle_default_start_is_stationary_and_moves():
    start = PowerLawKernel.from_vector(DEFAULT_POWERLAW_START)
    assert start.branching_ratio() < 1.0
    truth = PowerLawKernel(0.5, 0.4, 1.0, 2.0)
    horizon = 80.0
    arrivals = simulate_thinning(truth, horizon, seed=9)
    fitted = fit_powerlaw_mle(arrivals, horizon)
    assert not np.allclose(fitted.params, DEFAULT_POWERLAW_START)
    start_ll = powerlaw_loglik(arrivals, horizon, start)
    assert fitted.loglik is not None
    assert fitted.loglik > start_ll


def test_powerlaw_thinning_runs_when_stationary():
    kernel = PowerLawKernel(0.5, 0.4, 1.0, 2.0)
    arrivals = simulate_thinning(kernel, 200.0, seed=6)
    assert arrivals.size > 20
    assert arrivals[0] > 0
    assert np.all(np.diff(arrivals) > 0)


def test_compensator_increments_are_roughly_unit_exponential():
    horizon = 2000.0
    arrivals = simulate_thinning(TRUE, horizon, seed=7)
    residual = compensator_increments(arrivals, horizon, TRUE)
    assert abs(residual.mean() - 1.0) < 0.08
    path = TRUE.compensator_path(arrivals)
    assert path[-1] > path[0]


def test_em_refuses_large_event_count_by_default():
    arrivals = np.linspace(0.01, 10.0, 2001)
    with pytest.raises(ValueError, match="O\\(N\\^2\\)"):
        fit_exponential_em(arrivals, 10.0, iters=1)


def test_marked_jumps_are_source_row_target_column():
    model = MarkedExpKernel(
        baseline=np.array([0.4, 0.5]),
        jumps=np.array([[0.2, 0.3], [0.0, 0.15]]),
        decays=np.array([1.0, 1.2]),
    )
    phi = model.branching_matrix()
    assert phi.shape == (2, 2)
    np.testing.assert_allclose(phi[1, 0], 0.3 / 1.2)
    np.testing.assert_allclose(phi[0, 1], 0.0)
    assert model.is_stationary()
    arrivals, marks = simulate_marked_thinning(model, 400.0, seed=8)
    assert arrivals.size > 50
    assert set(np.unique(marks)).issubset({0, 1})
