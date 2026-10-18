"""Count-bin moments for interval-censored exponential Hawkes inference.

Population formulas: eqs. 7.11 (mean), 7.12 (variance, with beta^2/kappa^2),
7.13 (covariance). The gap in 7.13 is (lag-1)*width when bins `lag` apart
are compared, matching the empirical autocovariance of those bins.
The GMM weight is the inverse of a batch-means covariance, not (g g^T)^{-1}.
"""

from __future__ import annotations

import numpy as np

from tools.hawkes.kernels import ExpKernel, as_exp


def _bin_counts(arrivals, horizon, width) -> np.ndarray:
    edges = np.arange(0.0, float(horizon) + 1e-12, float(width))
    if edges.size < 2:
        raise ValueError("horizon must be larger than the bin width")
    counts, _ = np.histogram(arrivals, bins=edges)
    return counts.astype(np.float64)


def count_bin_moments(arrivals, horizon, width, lag) -> np.ndarray:
    """Empirical mean, variance, and lag-bin autocovariance of counts."""
    counts = _bin_counts(arrivals, horizon, width)
    mean = float(counts.mean())
    var = float(counts.var())
    centred = counts - mean
    auto = float(np.mean(centred[:-lag] * centred[lag:]))
    return np.array([[mean], [var], [auto]], dtype=np.float64)


def exponential_count_moments(kernel, width, lag) -> np.ndarray:
    """Stationary mean, variance, autocovariance of counts (eqs. 7.11–7.13)."""
    model = as_exp(kernel)
    kappa = model.decay - model.jump
    if kappa <= 0:
        raise ValueError("need decay > jump for stationary count moments")
    scale = model.baseline * model.decay / kappa
    mean = scale * width
    var = scale * (
        width * (model.decay**2 / kappa**2)
        + (1.0 - model.decay**2 / kappa**2)
        * ((1.0 - np.exp(-kappa * width)) / kappa)
    )
    gap = (lag - 1) * width
    auto = (
        model.baseline
        * model.decay
        * model.jump
        * (2.0 * model.decay - model.jump)
        * (np.exp(-kappa * width) - 1.0) ** 2
        / (2.0 * kappa**4)
    ) * np.exp(-kappa * gap)
    return np.array([[mean], [var], [auto]], dtype=np.float64)


def _block_covariance(counts: np.ndarray, lag: int, n_blocks: int = 40) -> np.ndarray:
    n = len(counts)
    n_blocks = min(int(n_blocks), max(n // (lag + 8), 4))
    rows = []
    for block in np.array_split(counts, n_blocks):
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


def batch_mean_weights(arrivals, horizon, width, lag, n_blocks: int = 40) -> np.ndarray:
    """W = Vhat^{-1} from non-overlapping batch means of the 3-vector of moments."""
    counts = _bin_counts(arrivals, horizon, width)
    cov = _block_covariance(counts, lag, n_blocks=n_blocks)
    cov = cov + 1e-10 * np.eye(3)
    return np.linalg.inv(cov)
