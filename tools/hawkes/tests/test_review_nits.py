import numpy as np
import pytest

from tools.hawkes import estimate as estimate_mod
from tools.hawkes import multivariate as multivariate_mod
from tools.hawkes import (
    ExpKernel,
    MAX_QUADRATIC_EVENTS,
    MarkedExpKernel,
    NonstationaryWarning,
    PowerLawKernel,
    batch_mean_weights,
    count_bin_moments,
    exponential_count_moments,
    fit_exponential_em,
    fit_exponential_gmm,
    fit_exponential_mle,
    fit_marked_mle,
    fit_powerlaw_mle,
    powerlaw_loglik,
    simulate_marked_thinning,
    simulate_thinning,
)


def test_simulator_refuses_branching_ratio_at_least_one():
    with pytest.raises(ValueError, match="refusing to simulate"):
        simulate_thinning(ExpKernel(1.0, 1.2, 1.0), 5.0, seed=0)
    with pytest.raises(ValueError, match="power-law"):
        simulate_thinning(PowerLawKernel(0.5, 2.0, 1.0, 2.0), 5.0, seed=0)


def test_simulator_horizon_guard_warns_when_allowed():
    with pytest.warns(NonstationaryWarning, match="event cap"):
        arrivals = simulate_thinning(
            ExpKernel(1.0, 1.2, 1.0), 2.0, seed=0, allow_nonstationary=True
        )
    assert arrivals.size >= 1
    assert arrivals[-1] <= 2.0 + 1e-12


def test_fitter_warns_when_fitted_n_at_least_one(monkeypatch):
    arrivals = np.array([0.2, 0.5, 0.9, 1.4])
    horizon = 2.0
    explosive = np.array([1.0, 1.2, 1.0])

    class _Res:
        x = explosive
        fun = 0.0

    monkeypatch.setattr(estimate_mod, "minimize", lambda *a, **k: _Res())
    with pytest.warns(NonstationaryWarning, match="not stationary"):
        hat = fit_exponential_mle(arrivals, horizon, use_score=True)
    assert hat.branching_ratio >= 1.0
    assert any("not stationary" in w for w in hat.warnings)
    with pytest.warns(NonstationaryWarning, match="not stationary"):
        hat2 = fit_exponential_mle(arrivals, horizon, use_score=False)
    assert hat2.branching_ratio >= 1.0
    assert any("not stationary" in w for w in hat2.warnings)


def test_marked_simulator_refuses_spectral_radius_at_least_one():
    model = MarkedExpKernel(
        baseline=np.array([0.4, 0.4]),
        jumps=np.array([[0.8, 0.5], [0.5, 0.8]]),
        decays=np.array([1.0, 1.0]),
    )
    with pytest.raises(ValueError, match="spectral radius"):
        simulate_marked_thinning(model, 5.0, seed=0)


def test_gmm_uses_batch_mean_weights(monkeypatch):
    kernel = ExpKernel(1.0, 0.6, 1.0)
    arrivals = simulate_thinning(kernel, 8000.0, seed=2)
    horizon, width, lag = 8000.0, 1.0, 2
    seen = []
    orig = estimate_mod._gmm_objective

    def spy(vec, width_, lag_, empirical, weight):
        seen.append(np.asarray(weight, dtype=np.float64).copy())
        return orig(vec, width_, lag_, empirical, weight)

    monkeypatch.setattr(estimate_mod, "_gmm_objective", spy)
    estimate_mod.fit_exponential_gmm(
        arrivals, horizon, width=width, lag=lag, n_blocks=30, rounds=1
    )
    assert len(seen) >= 2
    used = seen[-1]
    assert used.shape == (3, 3)
    assert np.linalg.matrix_rank(used) == 3
    assert np.linalg.cond(used) < 1e8

    batch = batch_mean_weights(arrivals, horizon, width, lag, n_blocks=30)
    batch = batch / max(np.max(np.abs(batch)), 1.0)
    np.testing.assert_allclose(used, batch)

    emp = count_bin_moments(arrivals, horizon, width, lag)
    g = emp - exponential_count_moments(kernel, width, lag)
    rank1 = g @ g.T
    assert np.linalg.matrix_rank(rank1) == 1
    with np.errstate(all="ignore"):
        rank1_inv = np.linalg.pinv(rank1)
        rank1_inv = rank1_inv / max(np.max(np.abs(rank1_inv)), 1.0)
    assert np.linalg.norm(used - rank1_inv) > 0.1


def test_powerlaw_refuses_large_event_count_by_default():
    arrivals = np.linspace(0.01, 10.0, MAX_QUADRATIC_EVENTS + 1)
    kernel = PowerLawKernel(0.4, 0.5, 1.0, 2.0)
    with pytest.raises(ValueError, match="N×N|N="):
        powerlaw_loglik(arrivals, 10.0, kernel)
    with pytest.raises(ValueError, match="N×N|N="):
        fit_powerlaw_mle(arrivals, 10.0)


class _FixedOpt:
    def __init__(self, x, success=True, message="OK"):
        self.x = np.asarray(x, dtype=np.float64)
        self.fun = 0.0
        self.success = success
        self.message = message


def _assert_nonstationary_recorded(result, fragment="not stationary"):
    assert result.branching_ratio >= 1.0 or not np.isfinite(result.branching_ratio)
    assert any(fragment in w for w in result.warnings)


def test_all_fitters_record_nonstationary_on_fit_result(monkeypatch):
    arrivals = np.array([0.2, 0.5, 0.9, 1.4])
    horizon = 20.0
    explosive_exp = np.array([1.0, 1.2, 1.0])
    explosive_pl = np.array([1.0, 2.0, 1.0, 2.0])  # n = 2
    start_marked = MarkedExpKernel(
        baseline=np.array([0.4, 0.4]),
        jumps=np.array([[0.2, 0.1], [0.1, 0.2]]),
        decays=np.array([1.0, 1.0]),
    )
    explosive_marked = MarkedExpKernel(
        baseline=np.array([0.4, 0.4]),
        jumps=np.array([[0.8, 0.5], [0.5, 0.8]]),
        decays=np.array([1.0, 1.0]),
    )

    monkeypatch.setattr(
        estimate_mod, "minimize", lambda *a, **k: _FixedOpt(explosive_exp)
    )
    with pytest.warns(NonstationaryWarning, match="not stationary"):
        mle = fit_exponential_mle(arrivals, horizon)
    _assert_nonstationary_recorded(mle)

    with pytest.warns(NonstationaryWarning, match="not stationary"):
        gmm = fit_exponential_gmm(arrivals, horizon, rounds=0)
    _assert_nonstationary_recorded(gmm)

    monkeypatch.setattr(
        estimate_mod, "minimize", lambda *a, **k: _FixedOpt(explosive_pl)
    )
    with pytest.warns(NonstationaryWarning, match="power-law"):
        power = fit_powerlaw_mle(arrivals, horizon)
    _assert_nonstationary_recorded(power)

    with pytest.warns(NonstationaryWarning, match="not stationary"):
        em = fit_exponential_em(
            arrivals, horizon, start=ExpKernel(1.0, 1.2, 1.0), iters=0
        )
    _assert_nonstationary_recorded(em)

    monkeypatch.setattr(
        multivariate_mod,
        "minimize",
        lambda *a, **k: _FixedOpt(explosive_marked.as_vector()),
    )
    with pytest.warns(NonstationaryWarning, match="spectral radius"):
        marked = fit_marked_mle(arrivals, np.array([0, 1, 0, 1]), horizon, start_marked)
    _assert_nonstationary_recorded(marked, fragment="spectral radius")


def test_fitters_record_optimizer_nonconvergence(monkeypatch):
    arrivals = np.array([0.2, 0.5, 0.9, 1.4])
    horizon = 20.0
    stationary_exp = np.array([1.0, 0.5, 1.5])
    stationary_pl = np.array([1.0, 0.5, 1.0, 2.0])
    start_marked = MarkedExpKernel(
        baseline=np.array([0.4, 0.4]),
        jumps=np.array([[0.2, 0.1], [0.1, 0.2]]),
        decays=np.array([1.0, 1.0]),
    )

    monkeypatch.setattr(
        estimate_mod,
        "minimize",
        lambda *a, **k: _FixedOpt(stationary_exp, success=False, message="MAXITER"),
    )
    mle = fit_exponential_mle(arrivals, horizon)
    assert any("did not converge" in w and "MAXITER" in w for w in mle.warnings)
    gmm = fit_exponential_gmm(arrivals, horizon, rounds=0)
    assert any("did not converge" in w for w in gmm.warnings)

    monkeypatch.setattr(
        estimate_mod,
        "minimize",
        lambda *a, **k: _FixedOpt(stationary_pl, success=False, message="ABNORMAL"),
    )
    power = fit_powerlaw_mle(arrivals, horizon)
    assert any("did not converge" in w and "ABNORMAL" in w for w in power.warnings)

    monkeypatch.setattr(
        multivariate_mod,
        "minimize",
        lambda *a, **k: _FixedOpt(
            start_marked.as_vector(), success=False, message="MAXITER"
        ),
    )
    marked = fit_marked_mle(arrivals, np.array([0, 1, 0, 1]), horizon, start_marked)
    assert any("did not converge" in w for w in marked.warnings)


def test_fitters_record_nonstationary_and_nonconvergence_together(monkeypatch):
    arrivals = np.array([0.2, 0.5, 0.9, 1.4])
    explosive = np.array([1.0, 1.2, 1.0])
    monkeypatch.setattr(
        estimate_mod,
        "minimize",
        lambda *a, **k: _FixedOpt(explosive, success=False, message="MAXITER"),
    )
    with pytest.warns(NonstationaryWarning, match="not stationary"):
        hat = fit_exponential_mle(arrivals, 2.0)
    assert any("not stationary" in w for w in hat.warnings)
    assert any("did not converge" in w for w in hat.warnings)


def test_stationary_converged_mle_has_empty_warnings():
    horizon = 400.0
    arrivals = simulate_thinning(ExpKernel(1.0, 0.6, 1.0), horizon, seed=11)
    hat = fit_exponential_mle(arrivals, horizon)
    assert hat.branching_ratio < 1.0
    assert hat.warnings == ()


def test_em_records_large_n_warning_when_allowed():
    arrivals = np.linspace(0.01, 10.0, MAX_QUADRATIC_EVENTS + 1)
    with pytest.warns(RuntimeWarning, match="O\\(N\\^2\\)"):
        hat = fit_exponential_em(
            arrivals, 10.0, start=ExpKernel(1.0, 0.5, 1.5), iters=0, allow_large=True
        )
    assert any("O(N^2)" in w for w in hat.warnings)
