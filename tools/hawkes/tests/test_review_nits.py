import numpy as np
import pytest

from tools.hawkes import hawkes as hawkes_mod
from tools.hawkes.hawkes import (
    POWER_N_MAX,
    HawkesNonStationaryWarning,
    branching_ratio_exp,
    empirical_moments,
    exp_mle,
    exp_mle_with_grad,
    exp_moments,
    gmm_weight_matrix,
    mutual_exp_simulate_by_thinning,
    power_log_likelihood,
    power_mle,
    simulate_exp_by_thinning,
    simulate_power_by_thinning,
)


def test_simulator_refuses_branching_ratio_at_least_one():
    theta = np.array([1.0, 1.2, 1.0])
    with pytest.raises(ValueError, match="refusing to simulate"):
        simulate_exp_by_thinning(theta, 5.0, seed=0)
    with pytest.raises(ValueError, match="power-law"):
        simulate_power_by_thinning(np.array([0.5, 2.0, 1.0, 2.0]), 5.0, seed=0)


def test_simulator_horizon_guard_warns_when_allowed():
    theta = np.array([1.0, 1.2, 1.0])
    with pytest.warns(HawkesNonStationaryWarning, match="event cap"):
        times = simulate_exp_by_thinning(
            theta, 2.0, seed=0, allow_nonstationary=True
        )
    assert times.size >= 1
    assert times[-1] <= 2.0 + 1e-12


def test_fitter_warns_when_fitted_n_at_least_one(monkeypatch):
    times = np.array([0.2, 0.5, 0.9, 1.4])
    T = 2.0
    explosive = np.array([1.0, 1.2, 1.0])

    class _Res:
        x = explosive

    monkeypatch.setattr(hawkes_mod, "minimize", lambda *a, **k: _Res())
    with pytest.warns(HawkesNonStationaryWarning, match="not stationary"):
        hat = exp_mle_with_grad(times, T)
    assert branching_ratio_exp(hat) >= 1.0
    with pytest.warns(HawkesNonStationaryWarning, match="not stationary"):
        hat2 = exp_mle(times, T)
    assert branching_ratio_exp(hat2) >= 1.0


def test_mutual_simulator_refuses_spectral_radius_at_least_one():
    lam = np.array([0.4, 0.4])
    alpha = np.array([[0.8, 0.5], [0.5, 0.8]])
    beta = np.array([1.0, 1.0])
    with pytest.raises(ValueError, match="spectral radius"):
        mutual_exp_simulate_by_thinning((lam, alpha, beta), 5.0, seed=0)


def test_exp_gmm_uses_batch_means_weight(monkeypatch):
    theta = np.array([1.0, 0.6, 1.0])
    times = simulate_exp_by_thinning(theta, 8000.0, seed=2)
    T, tau, lag = 8000.0, 1.0, 2
    weights = []
    orig_loss = hawkes_mod._gmm_loss

    def spy(th, tau_, lag_, emp, weight):
        weights.append(np.asarray(weight, dtype=np.float64).copy())
        return orig_loss(th, tau_, lag_, emp, weight)

    monkeypatch.setattr(hawkes_mod, "_gmm_loss", spy)
    hawkes_mod.exp_gmm(times, T, tau=tau, lag=lag, n_blocks=30, iters=1)
    assert len(weights) >= 2
    used = weights[-1]
    assert used.shape == (3, 3)
    assert np.linalg.matrix_rank(used) == 3
    assert np.linalg.cond(used) < 1e8

    batch = gmm_weight_matrix(times, T, tau, lag, n_blocks=30)
    batch = batch / max(np.max(np.abs(batch)), 1.0)
    np.testing.assert_allclose(used, batch)

    emp = empirical_moments(times, T, tau, lag)
    g = emp - exp_moments(theta, tau, lag)
    rank1 = g @ g.T
    assert np.linalg.matrix_rank(rank1) == 1
    with np.errstate(all="ignore"):
        rank1_inv = np.linalg.pinv(rank1)
        rank1_inv = rank1_inv / max(np.max(np.abs(rank1_inv)), 1.0)
    assert np.linalg.norm(used - rank1_inv) > 0.1


def test_power_law_refuses_large_n_by_default():
    times = np.linspace(0.01, 10.0, POWER_N_MAX + 1)
    with pytest.raises(ValueError, match="N×N|N="):
        power_log_likelihood(times, 10.0, np.array([0.4, 0.5, 1.0, 2.0]))
    with pytest.raises(ValueError, match="N×N|N="):
        power_mle(times, 10.0)
