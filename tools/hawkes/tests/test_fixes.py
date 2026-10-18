"""Formulas that disagree with a widely circulated incorrect sample implementation."""

import numpy as np
import pytest
from scipy.optimize import fsolve

from tools.hawkes import (
    ExpKernel,
    batch_mean_weights,
    count_bin_moments,
    exponential_count_moments,
    fit_exponential_gmm,
    simulate_by_time_change,
    simulate_thinning,
)

TRUE = ExpKernel(1.0, 0.6, 1.0)


def _incorrect_count_moments(baseline, jump, decay, width, lag):
    """Variance uses decay/kappa; autocov uses lag*width (both disagree with 7.12–7.13)."""
    kappa = decay - jump
    gap = lag * width
    mean = (baseline * decay / kappa) * width
    var = (baseline * decay / kappa) * (
        width * (decay / kappa)
        + (1 - decay / kappa) * ((1 - np.exp(-kappa * width)) / kappa)
    )
    auto = (
        baseline
        * decay
        * jump
        * (2 * decay - jump)
        * (np.exp(-kappa * width) - 1) ** 2
        / (2 * kappa**4)
    ) * np.exp(-kappa * gap)
    return np.array([[mean], [var], [auto]])


def _naive_compensator(t, arrivals, baseline, jump, decay):
    """Sums every slot of ``arrivals``, including unfilled entries."""
    if t <= 0:
        return 0.0
    total = baseline * t
    for stamp in arrivals:
        total += (jump / decay) * (1.0 - np.exp(-decay * (t - stamp)))
    return total


def _time_change_on_full_buffer(baseline, jump, decay, n_events, seed):
    np.random.seed(seed)
    arrivals = np.zeros(n_events, dtype=np.float64)
    target = -np.log(np.random.random())
    arrivals[0] = fsolve(
        lambda x: _naive_compensator(x[0], arrivals[:0], baseline, jump, decay) - target,
        1.0,
    )[0]
    prev = arrivals[0]
    for i in range(1, n_events):
        step = -np.log(np.random.random())
        at_prev = _naive_compensator(prev, arrivals, baseline, jump, decay)
        arrivals[i] = fsolve(
            lambda x, prev_val=at_prev, filled=i: _naive_compensator(
                x[0], arrivals[:filled], baseline, jump, decay
            )
            - prev_val
            - step,
            prev + 1.0,
        )[0]
        prev = arrivals[i]
    return arrivals


def test_count_variance_matches_eq_712_not_decay_over_kappa():
    width = 1.0
    ours = float(exponential_count_moments(TRUE, width, lag=5)[1, 0])
    wrong = float(_incorrect_count_moments(1.0, 0.6, 1.0, width, lag=5)[1, 0])
    kappa = TRUE.decay - TRUE.jump
    eq_712 = (TRUE.baseline * TRUE.decay / kappa) * (
        width * (TRUE.decay**2 / kappa**2)
        + (1 - TRUE.decay**2 / kappa**2) * ((1 - np.exp(-kappa * width)) / kappa)
    )
    assert ours == eq_712
    assert abs(wrong - eq_712) > 1.0

    arrivals = simulate_thinning(TRUE, 40000.0, seed=0)
    counts = np.histogram(arrivals, bins=np.arange(0.0, 40000.0 + 1e-12, width))[0]
    emp = float(counts.var())
    assert abs(emp - eq_712) < 0.25
    assert abs(emp - wrong) > 0.8


def test_count_autocov_uses_interior_gap_not_lag_times_width():
    width, lag = 1.0, 2
    ours = float(exponential_count_moments(TRUE, width, lag)[2, 0])
    wrong = float(_incorrect_count_moments(1.0, 0.6, 1.0, width, lag)[2, 0])
    kappa = TRUE.decay - TRUE.jump
    prefactor = TRUE.jump * TRUE.decay * TRUE.baseline * (2 * TRUE.decay - TRUE.jump) / (
        2 * kappa**2
    )
    gap_formula = (
        prefactor
        * (1 - np.exp(-kappa * width)) ** 2
        / kappa**2
        * np.exp(-kappa * (lag - 1) * width)
    )
    assert abs(ours - gap_formula) < 1e-12
    assert abs(wrong - gap_formula) > 0.3

    arrivals = simulate_thinning(TRUE, 40000.0, seed=1)
    emp = float(count_bin_moments(arrivals, 40000.0, width, lag)[2, 0])
    assert abs(emp - gap_formula) < 0.15
    assert abs(emp - wrong) > 0.2


@pytest.mark.filterwarnings("ignore:The iteration is not making good progress")
def test_time_change_simulation_uses_filled_prefix_only():
    n_events = 120
    naive_rates = []
    ours_rates = []
    for seed in range(6):
        with np.errstate(over="ignore", invalid="ignore"):
            naive = _time_change_on_full_buffer(1.0, 0.6, 1.0, n_events, seed)
        naive_rates.append(n_events / naive[-1])
        ours = simulate_by_time_change(TRUE, n_events, seed=seed)
        ours_rates.append(n_events / ours[-1])
    assert np.mean(naive_rates) < 0.5
    assert abs(np.mean(ours_rates) - 2.5) < 0.35


def test_gmm_weight_is_full_rank():
    arrivals = simulate_thinning(TRUE, 20000.0, seed=2)
    horizon, width, lag = 20000.0, 1.0, 2
    emp = count_bin_moments(arrivals, horizon, width, lag)
    g = emp - exponential_count_moments(TRUE, width, lag)
    outer = g @ g.T
    assert np.linalg.matrix_rank(outer) == 1
    assert np.linalg.cond(outer) > 1e10

    weight = batch_mean_weights(arrivals, horizon, width, lag, n_blocks=40)
    assert np.linalg.matrix_rank(weight) == 3
    assert np.linalg.cond(weight) < 1e8

    fitted = fit_exponential_gmm(
        arrivals, horizon, width=width, lag=lag, n_blocks=30, rounds=1
    )
    vec = fitted.params
    assert vec[0] > 0 and vec[1] > 0 and vec[2] > vec[1]
