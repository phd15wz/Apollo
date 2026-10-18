"""Linear Hawkes kernels.

Formulas: Laub, Lee, and Taimre, *The Elements of Hawkes Processes* (2021),
Definition 3.1 and eqs. 3.3–3.4 (intensity), eq. 5.6 (exponential compensator).
Independent implementation for Apollo; NumPy only.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from tools.hawkes._limits import CHUNK, MAX_QUADRATIC_EVENTS


def _history_before(arrivals, t: float) -> np.ndarray:
    stamp = np.asarray(arrivals, dtype=np.float64).reshape(-1)
    if stamp.size == 0:
        return stamp
    return stamp[stamp < t]


def _reject_huge(n_events: int, allow_large: bool) -> None:
    if n_events > MAX_QUADRATIC_EVENTS and not allow_large:
        raise ValueError(
            f"Power-law evaluation is O(N^2) and must not allocate an N×N matrix; "
            f"N={n_events} exceeds {MAX_QUADRATIC_EVENTS}. "
            "Pass allow_large=True to run a chunked O(N) memory path anyway, "
            "or split the window / use the exponential MLE."
        )


@dataclass(frozen=True)
class ExpKernel:
    """mu(s) = jump * exp(-decay * s), plus a constant baseline (eq. 3.3)."""

    baseline: float
    jump: float
    decay: float

    @classmethod
    def from_vector(cls, values) -> ExpKernel:
        baseline, jump, decay = np.asarray(values, dtype=np.float64).reshape(-1)
        return cls(float(baseline), float(jump), float(decay))

    def as_vector(self) -> np.ndarray:
        return np.array([self.baseline, self.jump, self.decay], dtype=np.float64)

    def branching_ratio(self) -> float:
        return float(self.jump / self.decay)

    def is_stationary(self) -> bool:
        return self.branching_ratio() < 1.0

    def mean_intensity(self) -> float:
        ratio = self.branching_ratio()
        if ratio >= 1.0:
            return np.inf
        return float(self.baseline / (1.0 - ratio))

    def intensity(self, t: float, arrivals) -> float:
        past = _history_before(arrivals, t)
        if past.size == 0:
            return float(self.baseline)
        return float(
            self.baseline + np.sum(self.jump * np.exp(-self.decay * (t - past)))
        )

    def compensator(self, t: float, arrivals) -> float:
        if t <= 0:
            return 0.0
        past = _history_before(arrivals, t)
        if past.size == 0:
            return float(self.baseline * t)
        mass = (self.jump / self.decay) * (1.0 - np.exp(-self.decay * (t - past)))
        return float(self.baseline * t + np.sum(mass))

    def compensator_path(self, arrivals) -> np.ndarray:
        stamp = np.asarray(arrivals, dtype=np.float64).reshape(-1)
        n = stamp.size
        if n == 0:
            return stamp.copy()
        gaps = np.diff(stamp, prepend=0.0)
        fade = np.exp(-self.decay * gaps)
        path = np.empty(n, dtype=np.float64)
        current = self.baseline
        total = 0.0
        for i in range(n):
            total += self.baseline * gaps[i] + (
                (current - self.baseline) / self.decay
            ) * (1.0 - fade[i])
            path[i] = total
            current = self.baseline + (current - self.baseline) * fade[i] + self.jump
        return path


@dataclass(frozen=True)
class PowerLawKernel:
    """mu(s) = amplitude / (offset + s)^exponent (eq. 3.4), exponent > 1."""

    baseline: float
    amplitude: float
    offset: float
    exponent: float

    @classmethod
    def from_vector(cls, values) -> PowerLawKernel:
        baseline, amplitude, offset, exponent = np.asarray(
            values, dtype=np.float64
        ).reshape(-1)
        return cls(float(baseline), float(amplitude), float(offset), float(exponent))

    def as_vector(self) -> np.ndarray:
        return np.array(
            [self.baseline, self.amplitude, self.offset, self.exponent],
            dtype=np.float64,
        )

    def branching_ratio(self) -> float:
        if self.exponent <= 1.0:
            return np.inf
        return float(
            self.amplitude
            / ((self.exponent - 1.0) * self.offset ** (self.exponent - 1.0))
        )

    def is_stationary(self) -> bool:
        return self.branching_ratio() < 1.0

    def mean_intensity(self) -> float:
        ratio = self.branching_ratio()
        if ratio >= 1.0:
            return np.inf
        return float(self.baseline / (1.0 - ratio))

    def intensity(self, t: float, arrivals) -> float:
        past = _history_before(arrivals, t)
        if past.size == 0:
            return float(self.baseline)
        return float(
            self.baseline
            + np.sum(self.amplitude / (self.offset + (t - past)) ** self.exponent)
        )

    def compensator(self, t: float, arrivals) -> float:
        past = _history_before(arrivals, t)
        if past.size == 0:
            return float(self.baseline * t)
        wait = t - past
        integral = (self.amplitude / (self.exponent - 1.0)) * (
            self.offset ** (1.0 - self.exponent)
            - (self.offset + wait) ** (1.0 - self.exponent)
        )
        return float(self.baseline * t + np.sum(integral))

    def compensator_path(self, arrivals, allow_large: bool = False) -> np.ndarray:
        stamp = np.asarray(arrivals, dtype=np.float64).reshape(-1)
        n = stamp.size
        _reject_huge(n, allow_large)
        if n == 0:
            return stamp.copy()
        path = self.baseline * stamp.copy()
        factor = self.amplitude / (self.exponent - 1.0)
        offset_pow = self.offset ** (1.0 - self.exponent)
        for start in range(0, n, CHUNK):
            stop = min(start + CHUNK, n)
            wait = stamp[start:stop, None] - stamp[None, :]
            earlier = np.arange(n)[None, :] < np.arange(start, stop)[:, None]
            add = np.zeros_like(wait)
            add[earlier] = factor * (
                offset_pow - (self.offset + wait[earlier]) ** (1.0 - self.exponent)
            )
            path[start:stop] += add.sum(axis=1)
        return path


def as_exp(kernel) -> ExpKernel:
    if isinstance(kernel, ExpKernel):
        return kernel
    return ExpKernel.from_vector(kernel)


def as_power(kernel) -> PowerLawKernel:
    if isinstance(kernel, PowerLawKernel):
        return kernel
    return PowerLawKernel.from_vector(kernel)
