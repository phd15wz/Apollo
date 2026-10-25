"""Shared fakes. Unit tests never open a network socket."""

from __future__ import annotations

import faulthandler
import functools
import json
import signal
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

import pytest

from tools.okx.client import HttpResponse, PublicClient, RateLimiter

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


class FakeTransport:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._queues: dict[str, deque[HttpResponse]] = defaultdict(deque)
        self._defaults: dict[str, HttpResponse] = {}

    def add(
        self,
        path: str,
        payload: dict[str, Any] | bytes,
        *,
        status: int = 200,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, Any] | None = None,
        once: bool = False,
    ) -> None:
        if isinstance(payload, bytes):
            body = payload
        else:
            body = json.dumps(payload).encode("utf-8")
        response = HttpResponse(
            status_code=status,
            headers=headers or {"Content-Type": "application/json"},
            body=body,
            url=path,
        )
        key = _key(path, params)
        if once:
            if params is None:
                self._queues[path].append(response)
            else:
                self._queues[key].append(response)
        elif params is None:
            self._defaults[path] = response
        else:
            self._defaults[key] = response

    def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None,
        headers: Mapping[str, str],
        timeout: float,
        body: bytes | None = None,
    ) -> HttpResponse:
        parsed = urlparse(url)
        path = parsed.path
        self.calls.append(
            {
                "method": method,
                "url": url,
                "path": path,
                "params": dict(params or {}),
                "headers": dict(headers),
                "timeout": timeout,
                "body": body,
            }
        )
        key = _key(path, params)
        if self._queues[key]:
            return self._queues[key].popleft()
        if self._queues[path]:
            return self._queues[path].popleft()
        if key in self._defaults:
            return self._defaults[key]
        if path in self._defaults:
            return self._defaults[path]
        raise AssertionError(f"unexpected request {method} {path} params={params}")


def _key(path: str, params: Mapping[str, Any] | None) -> str:
    items = tuple(sorted((str(k), str(v)) for k, v in (params or {}).items()))
    return path + "?" + "&".join(f"{k}={v}" for k, v in items)


def make_client(transport: FakeTransport, **kwargs: Any) -> PublicClient:
    clock = kwargs.pop("clock", None) or FakeClock()
    limiter = kwargs.pop("limiter", None) or RateLimiter(clock=clock, sleep=clock.sleep)
    return PublicClient(
        transport=transport,
        limiter=limiter,
        clock=clock,
        sleep=clock.sleep,
        **kwargs,
    )


def fail_if_hangs(seconds: float = 8.0):
    """Fail a close/shutdown test with TimeoutError instead of hanging the suite."""

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            def _on_alarm(signum: int, frame: object) -> None:
                raise TimeoutError(f"{fn.__name__} exceeded {seconds}s (close/shutdown hang)")

            faulthandler.dump_traceback_later(seconds, exit=False)
            previous = signal.signal(signal.SIGALRM, _on_alarm)
            signal.setitimer(signal.ITIMER_REAL, seconds)
            try:
                return fn(*args, **kwargs)
            finally:
                signal.setitimer(signal.ITIMER_REAL, 0.0)
                signal.signal(signal.SIGALRM, previous)
                faulthandler.cancel_dump_traceback_later()

        return wrapper

    return decorator


@pytest.fixture
def transport() -> FakeTransport:
    return FakeTransport()


@pytest.fixture
def clean_okx_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop private-client env so unit tests do not inherit a developer shell."""
    for name in (
        "OKX_DEMO",
        "OKX_ALLOW_ORDERS",
        "OKX_LIVE_TRADE",
        "OKX_READ_ONLY",
        "OKX_RISK_GATE",
        "OKX_API_KEY",
        "OKX_KEY",
        "OKX_API_SECRET",
        "OKX_SECRET",
        "OKX_SECRET_KEY",
        "OKX_API_PASSPHRASE",
        "OKX_PASSPHRASE",
    ):
        monkeypatch.delenv(name, raising=False)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "okx_live: hits OKX over the network; skipped unless OKX_LIVE=1",
    )
    config.addinivalue_line(
        "markers",
        "okx_demo: hits OKX demo private REST; skipped unless OKX_DEMO=1 and OKX_API_KEY are set",
    )
