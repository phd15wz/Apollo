"""Linear Hawkes models for event times.

Implements published formulas from Laub, Lee, and Taimre,
*The Elements of Hawkes Processes* (Springer, 2021). Chapter and equation
numbers are cited on the functions that use them. This package is an
independent implementation of that mathematics, not a port of any sample
code distributed with the book.

NumPy and SciPy only.
"""

from tools.hawkes._limits import MAX_QUADRATIC_EVENTS
from tools.hawkes._stationarity import NonstationaryWarning, matrix_spectral_radius
from tools.hawkes.diagnostics import compensator_increments, ks_unit_exponential
from tools.hawkes.estimate import (
    FitResult,
    fit_exponential_em,
    fit_exponential_gmm,
    fit_exponential_mle,
    fit_powerlaw_mle,
)
from tools.hawkes.kernels import ExpKernel, PowerLawKernel
from tools.hawkes.likelihood import (
    exponential_hessian,
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
from tools.hawkes.multivariate import (
    MarkedExpKernel,
    fit_marked_mle,
    marked_loglik,
    simulate_marked_thinning,
)
from tools.hawkes.simulate import simulate_by_time_change, simulate_thinning

__all__ = [
    "ExpKernel",
    "FitResult",
    "MAX_QUADRATIC_EVENTS",
    "MarkedExpKernel",
    "NonstationaryWarning",
    "PowerLawKernel",
    "batch_mean_weights",
    "compensator_increments",
    "count_bin_moments",
    "exponential_count_moments",
    "exponential_hessian",
    "exponential_loglik",
    "exponential_score",
    "fit_exponential_em",
    "fit_exponential_gmm",
    "fit_exponential_mle",
    "fit_marked_mle",
    "fit_powerlaw_mle",
    "hessian_stderr",
    "ks_unit_exponential",
    "marked_loglik",
    "matrix_spectral_radius",
    "powerlaw_loglik",
    "simulate_by_time_change",
    "simulate_marked_thinning",
    "simulate_thinning",
]
