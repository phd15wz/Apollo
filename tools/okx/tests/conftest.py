"""Shared fakes. Unit tests never open a network socket."""

from __future__ import annotations

import json
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


@pytest.fixture
def transport() -> FakeTransport:
    return FakeTransport()


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "okx_live: hits OKX over the network; skipped unless OKX_LIVE=1",
    )
