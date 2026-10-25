from __future__ import annotations

import pytest

from tools.okx.auth import OkxCredentials, sign
from tools.okx.client import PublicClient, RateLimiter
from tools.okx.errors import (
    LiveTradingDisabled,
    OkxError,
    OkxHttpError,
    OrdersDisabled,
    RiskLimitError,
    UnsafeEndpointError,
)
from tools.okx.guards import PRIVATE_RATE_LIMITS
from tools.okx.private import PrivateClient, open_margins_from_positions
from tools.okx.tests.conftest import FakeClock, FakeTransport, load_fixture

pytestmark = pytest.mark.usefixtures("clean_okx_env")

_TS = "2020-12-08T09:08:57.715Z"
_SECRET = "test-secret"


def _creds() -> OkxCredentials:
    return OkxCredentials("test-key", _SECRET, "test-passphrase")


def _client(transport: FakeTransport, **kwargs: object) -> tuple[PrivateClient, FakeClock]:
    clock = kwargs.pop("clock", None)
    if not isinstance(clock, FakeClock):
        clock = FakeClock()
    timestamp = kwargs.pop("timestamp", None)
    if timestamp is None:
        timestamp = lambda: _TS  # noqa: E731
    limiter = kwargs.pop("limiter", None)
    if limiter is None:
        limiter = RateLimiter(PRIVATE_RATE_LIMITS, clock=clock, sleep=clock.sleep)
    client = PrivateClient(
        credentials=kwargs.pop("credentials", _creds()),
        transport=transport,
        clock=clock,
        sleep=clock.sleep,
        limiter=limiter,
        timestamp=timestamp,
        **kwargs,
    )
    return client, clock


def test_public_client_still_refuses_private_paths() -> None:
    client = PublicClient(
        transport=FakeTransport(),
        clock=FakeClock(),
        sleep=lambda _seconds: None,
        limiter=RateLimiter(clock=FakeClock(), sleep=lambda _seconds: None),
    )
    with pytest.raises(UnsafeEndpointError):
        client.get("/api/v5/account/balance")
    with pytest.raises(UnsafeEndpointError):
        client.get("/api/v5/trade/order")
    with pytest.raises(UnsafeEndpointError):
        client.get("/api/v5/asset/balances")


def test_balance_is_signed_demo_get() -> None:
    transport = FakeTransport()
    transport.add("/api/v5/account/balance", load_fixture("private_balance.json"))
    client, _clock = _client(transport)
    balance = client.balance(ccy="USDT")
    assert balance.total_eq_usd == 100.5
    assert balance.details[0].ccy == "USDT"
    assert balance.details[0].available_bal == 90.25
    call = transport.calls[0]
    assert call["method"] == "GET"
    assert call["url"].endswith("/api/v5/account/balance?ccy=USDT")
    assert call["body"] is None
    assert call["headers"]["OK-ACCESS-SIGN"] == "Yt2R9ATAX6Jz6+ud53rCYenVK09+CEpafaWGadqhybE="
    assert call["headers"]["x-simulated-trading"] == "1"
    assert call["headers"]["OK-ACCESS-TIMESTAMP"] == _TS
    assert "test-secret" not in repr(client)
    assert "test-passphrase" not in repr(client)


def test_positions_and_open_margin_stub() -> None:
    transport = FakeTransport()
    transport.add("/api/v5/account/positions", load_fixture("private_positions.json"))
    client, _clock = _client(transport)
    rows = client.positions(inst_type="SWAP", inst_id="BTC-USDT-SWAP")
    assert rows[0].inst_id == "BTC-USDT-SWAP"
    assert rows[0].margin == 4.25
    assert rows[0].pos == 1
    assert open_margins_from_positions(rows) == {"BTC-USDT-SWAP": 4.25}
    assert transport.calls[0]["url"].endswith(
        "/api/v5/account/positions?instType=SWAP&instId=BTC-USDT-SWAP"
    )


def test_read_only_refuses_place_cancel_and_amend() -> None:
    transport = FakeTransport()
    client, _clock = _client(transport)
    assert client.allow_orders is False
    with pytest.raises(OrdersDisabled):
        client.place_order(
            "BTC-USDT-SWAP",
            side="buy",
            sz="1",
            td_mode="cross",
            ord_type="market",
            margin_usd=1,
        )
    with pytest.raises(OrdersDisabled):
        client.cancel_order("BTC-USDT-SWAP", ord_id="312269865356374016")
    with pytest.raises(OrdersDisabled):
        client.amend_order("BTC-USDT-SWAP", ord_id="312269865356374016", new_px="60001", margin_usd=1)
    assert transport.calls == []


def test_env_go_flag_and_read_only_kill_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OKX_ALLOW_ORDERS", "1")
    client, _clock = _client(FakeTransport())
    assert client.allow_orders is True
    monkeypatch.setenv("OKX_READ_ONLY", "1")
    blocked, _clock = _client(FakeTransport(), allow_orders=True)
    assert blocked.allow_orders is False


def test_unknown_okx_demo_value_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OKX_DEMO", "live")
    with pytest.raises(Exception, match="OKX_DEMO"):
        _client(FakeTransport())


def test_live_balance_and_positions_omit_simulated_header() -> None:
    transport = FakeTransport()
    transport.add("/api/v5/account/balance", load_fixture("private_balance.json"))
    transport.add("/api/v5/account/positions", load_fixture("private_positions.json"))
    client, _clock = _client(transport, demo=False)
    assert client.demo is False
    assert "demo=False" in repr(client)
    balance = client.balance(ccy="USDT")
    assert balance.total_eq_usd == 100.5
    live_balance = transport.calls[0]
    assert live_balance["method"] == "GET"
    assert live_balance["headers"]["OK-ACCESS-SIGN"] == "Yt2R9ATAX6Jz6+ud53rCYenVK09+CEpafaWGadqhybE="
    assert "x-simulated-trading" not in live_balance["headers"]
    rows = client.positions(inst_type="SWAP", inst_id="BTC-USDT-SWAP")
    assert rows[0].inst_id == "BTC-USDT-SWAP"
    live_positions = transport.calls[1]
    assert live_positions["method"] == "GET"
    assert "x-simulated-trading" not in live_positions["headers"]
    assert "OK-ACCESS-SIGN" in live_positions["headers"]


def test_live_writes_and_other_reads_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = FakeTransport()
    client, _clock = _client(transport, demo=False, allow_orders=True)
    assert client.allow_orders is True
    order = dict(
        inst_id="BTC-USDT-SWAP",
        side="buy",
        sz="1",
        td_mode="cross",
        ord_type="market",
        margin_usd=1,
    )
    with pytest.raises(LiveTradingDisabled, match="Live Trade"):
        client.place_order(**order)
    with pytest.raises(LiveTradingDisabled, match="Live Trade"):
        client.cancel_order("BTC-USDT-SWAP", ord_id="312269865356374016")
    with pytest.raises(LiveTradingDisabled, match="Live Trade"):
        client.amend_order("BTC-USDT-SWAP", ord_id="312269865356374016", new_px="60001", margin_usd=1)
    with pytest.raises(LiveTradingDisabled, match="balance, positions, and funding"):
        client.get_order("BTC-USDT-SWAP", ord_id="312269865356374016")
    assert transport.calls == []

    monkeypatch.setenv("OKX_ALLOW_ORDERS", "1")
    monkeypatch.setenv("OKX_DEMO", "0")
    flagged, _clock = _client(FakeTransport(), demo=False)
    assert flagged.demo is False
    assert flagged.allow_orders is True
    with pytest.raises(LiveTradingDisabled):
        flagged.place_order(**order)

    locked, _clock = _client(FakeTransport(), demo=False, allow_orders=False)
    assert locked.allow_orders is False
    with pytest.raises(LiveTradingDisabled, match="Live Trade"):
        locked.place_order(**order)
    with pytest.raises(LiveTradingDisabled, match="Live Trade"):
        locked.cancel_order("BTC-USDT-SWAP", ord_id="312269865356374016")


def test_place_cancel_amend_and_status() -> None:
    transport = FakeTransport()
    ack = load_fixture("private_order_ack.json")
    transport.add("/api/v5/trade/order", ack, once=True)
    transport.add("/api/v5/trade/cancel-order", ack)
    transport.add("/api/v5/trade/amend-order", ack)
    transport.add("/api/v5/trade/order", load_fixture("private_order.json"))
    transport.add("/api/v5/trade/orders-history", load_fixture("private_orders_history.json"))
    client, _clock = _client(transport, allow_orders=True)

    placed = client.place_order(
        "BTC-USDT-SWAP",
        side="buy",
        sz="1",
        td_mode="cross",
        ord_type="limit",
        px="60000",
        margin_usd=4,
    )
    assert placed.ord_id == "312269865356374016"
    assert placed.s_code == "0"
    place_call = transport.calls[0]
    assert place_call["method"] == "POST"
    assert place_call["path"] == "/api/v5/trade/order"
    assert place_call["headers"]["OK-ACCESS-SIGN"] == "Jdf+JbmfeRAdVIlIw5+3ulieVquVb5Gakq+wNSGitQI="
    assert place_call["headers"]["x-simulated-trading"] == "1"
    assert place_call["body"] == (
        b'{"instId":"BTC-USDT-SWAP","ordType":"limit","px":"60000",'
        b'"side":"buy","sz":"1","tdMode":"cross"}'
    )

    cancelled = client.cancel_order("BTC-USDT-SWAP", ord_id="312269865356374016")
    assert cancelled.ord_id == placed.ord_id
    assert transport.calls[1]["path"] == "/api/v5/trade/cancel-order"
    assert b"ordId" in transport.calls[1]["body"]

    amended = client.amend_order(
        "BTC-USDT-SWAP",
        ord_id="312269865356374016",
        new_sz="1",
        margin_usd=4,
    )
    assert amended.s_code == "0"
    assert transport.calls[2]["path"] == "/api/v5/trade/amend-order"

    order = client.get_order("BTC-USDT-SWAP", ord_id="312269865356374016")
    assert order.state == "live"
    assert order.px == "60000"
    assert "ordId=312269865356374016" in transport.calls[3]["url"]

    history = client.order_history("SWAP", inst_id="BTC-USDT-SWAP", limit=10)
    assert history[0].state == "filled"
    assert "/api/v5/trade/orders-history?" in transport.calls[4]["url"]


def test_margin_cap_blocks_before_http() -> None:
    transport = FakeTransport()
    transport.add("/api/v5/trade/order", load_fixture("private_order_ack.json"))
    client, _clock = _client(transport, allow_orders=True)
    with pytest.raises(RiskLimitError):
        client.place_order(
            "BTC-USDT-SWAP",
            side="buy",
            sz="1",
            td_mode="cross",
            ord_type="market",
        )
    with pytest.raises(RiskLimitError):
        client.place_order(
            "BTC-USDT-SWAP",
            side="buy",
            sz="1",
            td_mode="cross",
            ord_type="market",
            margin_usd=11,
        )
    gated, _clock = _client(transport, allow_orders=True, risk_gate=True)
    with pytest.raises(RiskLimitError):
        gated.place_order(
            "BTC-USDT-SWAP",
            side="buy",
            sz="1",
            td_mode="cross",
            ord_type="market",
            margin_usd=6,
        )
    assert transport.calls == []
    placed = client.place_order(
        "NVDA-USDT-SWAP",
        side="buy",
        sz="1",
        td_mode="cross",
        ord_type="market",
        margin_usd=4,
        open_margins={"AMD-USDT-SWAP": 6},
    )
    assert placed.ord_id
    with pytest.raises(RiskLimitError, match="chips"):
        client.place_order(
            "NVDA-USDT-SWAP",
            side="buy",
            sz="1",
            td_mode="cross",
            ord_type="market",
            margin_usd=5,
            open_margins={"AMD-USDT-SWAP": 6},
        )


def test_reduce_only_skips_margin_requirement() -> None:
    transport = FakeTransport()
    transport.add("/api/v5/trade/order", load_fixture("private_order_ack.json"))
    client, _clock = _client(transport, allow_orders=True)
    ack = client.place_order(
        "BTC-USDT-SWAP",
        side="sell",
        sz="1",
        td_mode="cross",
        ord_type="market",
        reduce_only=True,
    )
    assert ack.s_code == "0"
    assert b'"reduceOnly":true' in transport.calls[0]["body"]


def test_exchange_scode_is_an_error() -> None:
    transport = FakeTransport()
    transport.add("/api/v5/trade/order", load_fixture("private_order_reject.json"))
    client, _clock = _client(transport, allow_orders=True)
    with pytest.raises(OkxError, match="51008"):
        client.place_order(
            "BTC-USDT-SWAP",
            side="buy",
            sz="1",
            td_mode="cross",
            ord_type="market",
            margin_usd=1,
        )


def test_private_retry_then_balance() -> None:
    transport = FakeTransport()
    transport.add(
        "/api/v5/account/balance",
        {"code": "0", "data": []},
        status=429,
        headers={"Retry-After": "0"},
        once=True,
    )
    transport.add("/api/v5/account/balance", load_fixture("private_balance.json"), once=True)
    client, _clock = _client(transport, max_retries=2)
    balance = client.balance()
    assert balance.details[0].ccy == "USDT"
    assert len(transport.calls) == 2


def test_http_error_does_not_include_the_secret() -> None:
    transport = FakeTransport()
    transport.add("/api/v5/account/balance", b"nope", status=401)
    client, _clock = _client(transport, max_retries=0)
    with pytest.raises(OkxHttpError) as exc:
        client.balance()
    assert _SECRET not in str(exc.value)


def test_withdraw_helper_never_hits_transport() -> None:
    transport = FakeTransport()
    client, _clock = _client(transport, allow_orders=True)
    for method, path in (
        ("POST", "/api/v5/asset/withdrawal"),
        ("POST", "/api/v5/asset/transfer"),
        ("POST", "/api/v5/asset/balances"),
        ("GET", "/api/v5/asset/bills"),
        ("GET", "/api/v5/asset/currencies"),
        ("GET", "/api/v5/account/max-withdrawal"),
    ):
        with pytest.raises(UnsafeEndpointError):
            client._request(method, path)
    live, _clock = _client(FakeTransport(), demo=False, allow_orders=True)
    for method, path in (
        ("POST", "/api/v5/asset/balances"),
        ("GET", "/api/v5/asset/deposit-address"),
        ("GET", "/api/v5/asset/asset-valuation"),
    ):
        with pytest.raises(UnsafeEndpointError):
            live._request(method, path)
    assert transport.calls == []


def test_price_only_amend_does_not_require_margin() -> None:
    transport = FakeTransport()
    transport.add("/api/v5/trade/amend-order", load_fixture("private_order_ack.json"))
    client, _clock = _client(transport, allow_orders=True)
    ack = client.amend_order("BTC-USDT-SWAP", cl_ord_id="demo1", new_px="60100")
    assert ack.ord_id
    assert transport.calls[0]["path"] == "/api/v5/trade/amend-order"


def test_funding_balance_is_signed_get_on_demo_and_live() -> None:
    transport = FakeTransport()
    transport.add("/api/v5/asset/balances", load_fixture("private_funding_balance.json"))
    demo, _clock = _client(transport)
    rows = demo.funding(ccy="USDT")
    assert [(row.ccy, row.bal, row.avail_bal, row.frozen_bal) for row in rows] == [
        ("USDT", 25.5, 20.0, 5.5),
        ("BTC", 0.01, 0.01, 0.0),
    ]
    demo_call = transport.calls[0]
    assert demo_call["method"] == "GET"
    assert demo_call["url"].endswith("/api/v5/asset/balances?ccy=USDT")
    assert demo_call["body"] is None
    assert demo_call["headers"]["OK-ACCESS-SIGN"] == sign(
        _SECRET, _TS, "GET", "/api/v5/asset/balances?ccy=USDT", ""
    )
    assert demo_call["headers"]["x-simulated-trading"] == "1"

    live_transport = FakeTransport()
    live_transport.add("/api/v5/asset/balances", load_fixture("private_funding_balance.json"))
    live, _clock = _client(live_transport, demo=False)
    assert live.funding()[0].ccy == "USDT"
    live_call = live_transport.calls[0]
    assert live_call["method"] == "GET"
    assert live_call["url"].endswith("/api/v5/asset/balances")
    assert "x-simulated-trading" not in live_call["headers"]
    assert live_call["headers"]["OK-ACCESS-SIGN"] == sign(
        _SECRET, _TS, "GET", "/api/v5/asset/balances", ""
    )

    with pytest.raises(ValueError, match="invalid ccy"):
        live.funding(ccy="usdt")
    many = ",".join(f"C{i:02d}" for i in range(21))
    with pytest.raises(ValueError, match="at most 20"):
        live.funding(ccy=many)
    assert len(live_transport.calls) == 1


def test_funding_rate_limit_waits() -> None:
    clock = FakeClock()
    transport = FakeTransport()
    transport.add("/api/v5/asset/balances", load_fixture("private_funding_balance.json"))
    client, _clock = _client(transport, clock=clock, demo=False)
    for _ in range(6):
        client.funding()
    client.funding()
    assert clock.sleeps
    assert clock.sleeps[0] == pytest.approx(1.0)
    assert "x-simulated-trading" not in transport.calls[-1]["headers"]


def test_balance_rate_limit_waits() -> None:
    clock = FakeClock()
    transport = FakeTransport()
    transport.add("/api/v5/account/balance", load_fixture("private_balance.json"))
    client, _clock = _client(transport, clock=clock)
    for _ in range(10):
        client.balance()
    client.balance()
    assert clock.sleeps
    assert clock.sleeps[0] == pytest.approx(2.0)
