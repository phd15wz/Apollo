"""Optional Numba JIT. Falls back to pure Python/NumPy if Numba is absent."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

F = TypeVar("F", bound=Callable[..., Any])

try:
    from numba import njit as _numba_njit
except ImportError:  # pragma: no cover - exercised when Numba is not installed

    def njit(*args: Any, **kwargs: Any) -> Any:
        def deco(fn: F) -> F:
            return fn

        if args and callable(args[0]) and not kwargs:
            return args[0]
        return deco

else:

    def njit(*args: Any, **kwargs: Any) -> Any:
        kwargs.pop("nogil", None)
        kwargs.pop("parallel", None)
        kwargs.setdefault("cache", False)
        return _numba_njit(*args, **kwargs)
