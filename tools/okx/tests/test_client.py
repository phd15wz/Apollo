from __future__ import annotations

import inspect

import pytest

from tools.okx.client import PublicClient
from tools.okx.errors import OkxError, OkxHttpError, UnsafeEndpointError
from tools.okx.tests.conftest import FakeClock, FakeTransport, load_fixture, make_client


def test_constructor_has_no_key_parameters() -> None:
    names = set(inspect.signature(PublicClient.__init__).parameters)
    forbidden = {
        "api_key",
        "apikey",
        "api_secret",
        "secret",
        "secret_key",
        "passphrase",
        "password",
        "signature",
        "sign",
    }
    assert names.isdisjoint(forbidden)


def test_sites_and_custom_base_url() -> None:
    clock = FakeClock()
    transport = FakeTransport()
    client = make_client(transport, site="eea", clock=clock)
    assert client.base_url == "https://eea.okx.com"
    us = make_client(transport, site="us", clock=clock)
    assert us.base_url == "https://us.okx.com"
    custom = make_client(transport, base_url="https://www.okx.com", clock=clock)
    assert custom.base_url == "https://www.okx.com"
    with pytest.raises(ValueError):
        PublicClient(site="cn")
    with pytest.raises(ValueError, match="non-https"):
        PublicClient(base_url="http://www.okx.com")
    with pytest.raises(ValueError, match="refusing host"):
        PublicClient(base_url="https://example.com")
    override = PublicClient(
        base_url="https://127.0.0.1",
        allow_unofficial_hosts=True,
        transport=transport,
        clock=clock,
        sleep=clock.sleep,
    )
    assert override.base_url == "https://127.0.0.1"


def test_okx_business_error() -> None:
    transport = FakeTransport()
    transport.add("/api/v5/market/ticker", load_fixture("error_business.json"))
    client = make_client(transport)
    with pytest.raises(OkxError, match="51000"):
        client.ticker("BTC-USDT-SWAP")


def test_retry_on_http_429_then_success() -> None:
    transport = FakeTransport()
    transport.add(
        "/api/v5/market/ticker",
        {"code": "0", "data": []},
        status=429,
        headers={"Retry-After": "0"},
        once=True,
    )
    transport.add("/api/v5/market/ticker", load_fixture("ticker.json"), once=True)
    client = make_client(transport, max_retries=2)
    ticker = client.ticker("BTC-USDT-SWAP")
    assert ticker.inst_id == "BTC-USDT-SWAP"
    assert ticker.ts_ms == 1597026383085
    assert len(transport.calls) == 2


def test_retry_on_5xx_then_json_rate_limit_code() -> None:
    transport = FakeTransport()
    transport.add("/api/v5/market/ticker", b"oops", status=503, once=True)
    transport.add("/api/v5/market/ticker", load_fixture("error_50011.json"), once=True)
    transport.add("/api/v5/market/ticker", load_fixture("ticker.json"), once=True)
    client = make_client(transport, max_retries=3)
    ticker = client.ticker("BTC-USDT-SWAP")
    assert ticker.last == 9999.99
    assert len(transport.calls) == 3


def test_http_error_when_retries_exhausted() -> None:
    transport = FakeTransport()
    transport.add("/api/v5/market/ticker", b"down", status=502, once=True)
    client = make_client(transport, max_retries=0)
    with pytest.raises(OkxHttpError, match="502"):
        client.ticker("BTC-USDT-SWAP")


def test_rate_limiter_waits_after_documented_burst() -> None:
    clock = FakeClock()
    from tools.okx.client import RateLimiter
    from tools.okx.endpoints import TICKER

    limiter = RateLimiter({TICKER: (2, 2.0)}, clock=clock, sleep=clock.sleep)
    limiter.acquire(TICKER)
    limiter.acquire(TICKER)
    limiter.acquire(TICKER)
    assert clock.sleeps
    assert clock.sleeps[0] == pytest.approx(2.0)


def test_refuses_private_and_unknown_paths() -> None:
    client = make_client(FakeTransport())
    with pytest.raises(UnsafeEndpointError):
        client.get("/api/v5/account/balance")
    with pytest.raises(UnsafeEndpointError):
        client.get("/api/v5/trade/order")
    with pytest.raises(UnsafeEndpointError):
        client.get("/api/v5/asset/currencies")
    with pytest.raises(UnsafeEndpointError):
        client.get("/api/v5/market/not-a-real-endpoint")


def test_get_only_no_auth_headers() -> None:
    transport = FakeTransport()
    transport.add("/api/v5/market/ticker", load_fixture("ticker.json"))
    client = make_client(transport)
    client.ticker("BTC-USDT-SWAP")
    call = transport.calls[0]
    assert call["method"] == "GET"
    header_names = {name.lower() for name in call["headers"]}
    assert "ok-access-key" not in header_names
    assert "ok-access-sign" not in header_names
    assert "ok-access-passphrase" not in header_names
    assert "ok-access-timestamp" not in header_names
