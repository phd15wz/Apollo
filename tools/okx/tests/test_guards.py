from __future__ import annotations

import pytest

from tools.okx.errors import LiveTradingDisabled, OrdersDisabled, RiskLimitError, UnsafeEndpointError
from tools.okx.guards import (
    ALLOWED_PRIVATE,
    LIVE_TRADE_ENABLED,
    LIVE_TRADE_PATHS,
    SET_LEVERAGE_PATH,
    CapBook,
    CapGroup,
    assert_order_margin,
    assert_private_request,
    load_cap_book,
    assert_live_read_only,
    assert_live_smoke_cancel,
    assert_live_smoke_place,
    assert_live_trade_payload,
    margin_ceiling,
    require_cli_demo,
    resolve_allow_orders,
    resolve_demo,
    resolve_live_trade,
    resolve_risk_gate,
)

pytestmark = pytest.mark.usefixtures("clean_okx_env")


def _book() -> CapBook:
    chips = CapGroup(
        name="chips",
        shared_cap=True,
        inst_ids=frozenset({"NVDA-USDT-SWAP", "AMD-USDT-SWAP"}),
    )
    platforms = CapGroup(
        name="platforms",
        shared_cap=False,
        inst_ids=frozenset({"SPCX-USDT-SWAP", "GOOGL-USDT-SWAP"}),
    )
    return CapBook(
        groups=(chips, platforms),
        inst_groups={
            "NVDA-USDT-SWAP": ("chips",),
            "AMD-USDT-SWAP": ("chips",),
            "SPCX-USDT-SWAP": ("platforms",),
            "GOOGL-USDT-SWAP": ("platforms",),
        },
    )


def test_live_trade_constant_is_a_tripwire(monkeypatch: pytest.MonkeyPatch) -> None:
    assert LIVE_TRADE_ENABLED is False
    assert resolve_demo(None) is True
    assert resolve_demo(True) is True
    assert resolve_demo(False) is False
    monkeypatch.setenv("OKX_DEMO", "0")
    assert resolve_demo(None) is False
    assert resolve_demo(False) is False
    with pytest.raises(LiveTradingDisabled):
        resolve_demo(True)
    monkeypatch.setenv("OKX_DEMO", "1")
    assert resolve_demo(True) is True
    assert resolve_demo(None) is True
    with pytest.raises(LiveTradingDisabled):
        resolve_demo(False)
    monkeypatch.setattr("tools.okx.guards.LIVE_TRADE_ENABLED", True)
    monkeypatch.setenv("OKX_LIVE_TRADE", "1")
    with pytest.raises(LiveTradingDisabled):
        resolve_demo(True)
    with pytest.raises(LiveTradingDisabled):
        resolve_demo(False)


def test_live_reads_are_balance_positions_and_funding() -> None:
    assert_live_read_only(True, "POST", "/api/v5/trade/order")
    assert_live_read_only(False, "GET", "/api/v5/account/balance")
    assert_live_read_only(False, "GET", "/api/v5/account/positions")
    assert_live_read_only(False, "GET", "/api/v5/asset/balances")
    with pytest.raises(LiveTradingDisabled):
        assert_live_read_only(False, "POST", "/api/v5/trade/order")
    with pytest.raises(LiveTradingDisabled):
        assert_live_read_only(False, "POST", "/api/v5/trade/cancel-order")
    with pytest.raises(LiveTradingDisabled):
        assert_live_read_only(False, "POST", "/api/v5/trade/amend-order")
    with pytest.raises(LiveTradingDisabled):
        assert_live_read_only(False, "GET", "/api/v5/trade/order")
    with pytest.raises(LiveTradingDisabled):
        assert_live_read_only(False, "POST", "/api/v5/asset/balances")
    with pytest.raises(LiveTradingDisabled):
        assert_live_read_only(False, "GET", "/api/v5/asset/bills")


def test_live_trade_opt_in_is_place_and_cancel_only(monkeypatch: pytest.MonkeyPatch) -> None:
    assert resolve_live_trade(None) is False
    assert resolve_live_trade(False) is False
    assert resolve_live_trade(True) is True
    monkeypatch.setenv("OKX_LIVE_TRADE", "1")
    assert resolve_live_trade(None) is True
    assert resolve_live_trade(False) is False
    monkeypatch.setenv("OKX_LIVE_TRADE", "0")
    assert resolve_live_trade(None) is False

    assert_live_read_only(False, "POST", "/api/v5/trade/order", live_trade=True)
    assert_live_read_only(False, "POST", "/api/v5/trade/cancel-order", live_trade=True)
    assert_live_read_only(False, "GET", "/api/v5/asset/balances", live_trade=True)
    assert_live_read_only(False, "GET", "/api/v5/account/balance", live_trade=False)
    with pytest.raises(LiveTradingDisabled, match="Live Trade"):
        assert_live_read_only(False, "POST", "/api/v5/trade/order", live_trade=False)
    with pytest.raises(LiveTradingDisabled, match="Live Trade"):
        assert_live_read_only(False, "POST", "/api/v5/trade/amend-order", live_trade=True)
    with pytest.raises(LiveTradingDisabled, match="Live Trade"):
        assert_live_read_only(False, "GET", "/api/v5/trade/order", live_trade=True)
    with pytest.raises(LiveTradingDisabled):
        assert_live_read_only(False, "POST", "/api/v5/asset/balances", live_trade=True)
    assert_live_read_only(False, "POST", SET_LEVERAGE_PATH, live_trade=True)
    with pytest.raises(LiveTradingDisabled, match="Live Trade"):
        assert_live_read_only(False, "POST", SET_LEVERAGE_PATH, live_trade=False)
    with pytest.raises(LiveTradingDisabled, match="Live Trade"):
        assert_live_read_only(False, "POST", "/api/v5/account/set-position-mode", live_trade=True)

    assert_live_smoke_place(
        inst_id="BTC-USDT",
        side="buy",
        sz="0.001",
        td_mode="cash",
        ord_type="limit",
        px="40000",
    )
    assert_live_smoke_place(
        inst_id="BTC-USDT",
        side="buy",
        sz="0.0010",
        td_mode="cash",
        ord_type="limit",
        px="10000",
    )
    assert_live_smoke_cancel("BTC-USDT")
    assert_live_smoke_place(
        inst_id="BTC-USDT-SWAP",
        side="buy",
        sz="0.01",
        td_mode="isolated",
        ord_type="limit",
        px="40000",
    )
    assert_live_smoke_place(
        inst_id="BTC-USDT-SWAP",
        side="buy",
        sz="0.010",
        td_mode="isolated",
        ord_type="limit",
        px="10000",
    )
    assert_live_smoke_cancel("BTC-USDT-SWAP")
    for kwargs in (
        {"inst_id": "BTC-USDT-SWAP", "td_mode": "cross", "ord_type": "limit", "sz": "0.01", "px": "40000", "side": "buy"},
        {"inst_id": "BTC-USDT-SWAP", "td_mode": "cross", "ord_type": "market", "sz": "0.01", "px": "1", "side": "buy"},
        {"inst_id": "BTC-USDT-SWAP", "td_mode": "cash", "ord_type": "limit", "sz": "0.01", "px": "40000", "side": "buy"},
        {"inst_id": "BTC-USDT-SWAP", "td_mode": "isolated", "ord_type": "limit", "sz": "1", "px": "40000", "side": "buy"},
        {"inst_id": "BTC-USDT-SWAP", "td_mode": "isolated", "ord_type": "limit", "sz": "1.0", "px": "40000", "side": "buy"},
        {"inst_id": "BTC-USDT-SWAP", "td_mode": "isolated", "ord_type": "limit", "sz": "2", "px": "40000", "side": "buy"},
        {"inst_id": "BTC-USDT-SWAP", "td_mode": "isolated", "ord_type": "limit", "sz": "0.01", "px": "40001", "side": "buy"},
        {"inst_id": "BTC-USDT-SWAP", "td_mode": "isolated", "ord_type": "limit", "sz": "0.01", "px": "40000", "side": "sell"},
        {"inst_id": "ETH-USDT-SWAP", "td_mode": "isolated", "ord_type": "limit", "sz": "0.01", "px": "40000", "side": "buy"},
        {"inst_id": "BTC-USDT", "td_mode": "cash", "ord_type": "limit", "sz": "0.01", "px": "40000", "side": "buy"},
        {"inst_id": "BTC-USDT", "td_mode": "cash", "ord_type": "limit", "sz": "0.001", "px": "40001", "side": "buy"},
        {"inst_id": "BTC-USDT", "td_mode": "cash", "ord_type": "limit", "sz": "0.001", "px": "40000", "side": "sell"},
    ):
        with pytest.raises(LiveTradingDisabled, match="BTC-USDT"):
            assert_live_smoke_place(**kwargs)
    with pytest.raises(LiveTradingDisabled, match="BTC-USDT"):
        assert_live_smoke_place(
            inst_id="BTC-USDT",
            side="buy",
            sz="0.001",
            td_mode="cash",
            ord_type="limit",
            px="40000",
            reduce_only=True,
        )
    with pytest.raises(LiveTradingDisabled, match="posSide"):
        assert_live_smoke_place(
            inst_id="BTC-USDT-SWAP",
            side="buy",
            sz="0.01",
            td_mode="isolated",
            ord_type="limit",
            px="40000",
            pos_side="long",
        )
    with pytest.raises(LiveTradingDisabled):
        assert_live_smoke_cancel("ETH-USDT-SWAP")
    with pytest.raises(LiveTradingDisabled):
        assert_live_trade_payload(
            "POST",
            "/api/v5/trade/order",
            {"instId": "BTC-USDT", "tdMode": "cash", "side": "buy", "ordType": "market", "sz": "0.001"},
        )
    assert_live_trade_payload(
        "POST",
        "/api/v5/trade/order",
        {
            "instId": "BTC-USDT-SWAP",
            "tdMode": "isolated",
            "side": "buy",
            "ordType": "limit",
            "sz": "0.01",
            "px": "40000",
        },
    )
    with pytest.raises(LiveTradingDisabled, match="0.01"):
        assert_live_trade_payload(
            "POST",
            "/api/v5/trade/order",
            {
                "instId": "BTC-USDT-SWAP",
                "tdMode": "isolated",
                "side": "buy",
                "ordType": "limit",
                "sz": "1",
                "px": "40000",
            },
        )
    assert_live_trade_payload(
        "POST",
        "/api/v5/trade/cancel-order",
        {"instId": "BTC-USDT-SWAP", "ordId": "1"},
    )
    with pytest.raises(LiveTradingDisabled):
        assert_live_trade_payload("POST", "/api/v5/trade/amend-order", {"instId": "BTC-USDT"})
    with pytest.raises(LiveTradingDisabled):
        assert_live_trade_payload(
            "POST",
            "/api/v5/asset/withdrawal",
            {"ccy": "USDT", "amt": "1"},
        )


def test_cli_requires_demo_env(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(LiveTradingDisabled):
        require_cli_demo()
    monkeypatch.setenv("OKX_DEMO", "1")
    require_cli_demo()


def test_kill_switch_beats_the_go_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    assert resolve_allow_orders(allow_orders=None, read_only=None) is False
    assert resolve_allow_orders(allow_orders=True, read_only=None) is True
    assert resolve_allow_orders(allow_orders=True, read_only=True) is False
    monkeypatch.setenv("OKX_ALLOW_ORDERS", "1")
    assert resolve_allow_orders(allow_orders=None, read_only=None) is True
    monkeypatch.setenv("OKX_READ_ONLY", "1")
    assert resolve_allow_orders(allow_orders=True, read_only=None) is False
    assert resolve_allow_orders(allow_orders=None, read_only=None) is False


def test_risk_gate_halves_the_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    assert resolve_risk_gate(None) is False
    assert margin_ceiling(gate_on=False) == 10.0
    assert margin_ceiling(gate_on=True) == 5.0
    monkeypatch.setenv("OKX_RISK_GATE", "1")
    assert resolve_risk_gate(None) is True
    assert resolve_risk_gate(False) is False


def test_allowlist_is_trade_account_and_funding_balance() -> None:
    assert ("GET", "/api/v5/account/balance") in ALLOWED_PRIVATE
    assert ("GET", "/api/v5/asset/balances") in ALLOWED_PRIVATE
    assert ("POST", "/api/v5/asset/balances") not in ALLOWED_PRIVATE
    assert ("POST", "/api/v5/trade/cancel-order") in ALLOWED_PRIVATE
    assert ("POST", "/api/v5/trade/order") in ALLOWED_PRIVATE
    assert ("POST", SET_LEVERAGE_PATH) in ALLOWED_PRIVATE
    assert ("POST", SET_LEVERAGE_PATH) in LIVE_TRADE_PATHS
    assert assert_private_request("POST", SET_LEVERAGE_PATH) == SET_LEVERAGE_PATH
    account_posts = sorted(
        path for method, path in ALLOWED_PRIVATE if method == "POST" and path.startswith("/api/v5/account")
    )
    assert account_posts == [SET_LEVERAGE_PATH]
    assert assert_private_request("GET", "/api/v5/asset/balances") == "/api/v5/asset/balances"
    assert assert_private_request("GET", "/api/v5/asset/balances?ccy=USDT") == "/api/v5/asset/balances"
    for method, path in ALLOWED_PRIVATE:
        assert method in {"GET", "POST"}
        lowered = path.lower()
        assert "withdraw" not in lowered
        assert "transfer" not in lowered
        assert "deposit" not in lowered
        assert not lowered.startswith("/api/v5/finance")
        if lowered.startswith("/api/v5/asset"):
            assert (method, path) == ("GET", "/api/v5/asset/balances")
        else:
            assert path.startswith("/api/v5/account") or path.startswith("/api/v5/trade")


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/api/v5/asset/withdrawal"),
        ("POST", "/api/v5/asset/transfer"),
        ("GET", "/api/v5/asset/deposit-address"),
        ("POST", "/api/v5/asset/balances"),
        ("GET", "/api/v5/asset/bills"),
        ("GET", "/api/v5/asset/asset-valuation"),
        ("GET", "/api/v5/asset/balances/"),
        ("GET", "/api/v5/account/max-withdrawal"),
        ("POST", "/api/v5/finance/savings/purchase"),
        ("GET", "/api/v5/asset/currencies"),
        ("POST", "/api/v5/trade/batch-orders"),
        ("DELETE", "/api/v5/trade/order"),
        ("GET", "/api/v5/trade/order-algo"),
        ("POST", "/api/v5/account/set-position-mode"),
        ("POST", "/api/v5/account/set-account-level"),
        ("GET", "/api/v5/account/set-leverage"),
        ("POST", "/api/v5/account/config"),
    ],
)
def test_withdraw_and_unknown_paths_raise(method: str, path: str) -> None:
    with pytest.raises(UnsafeEndpointError):
        assert_private_request(method, path)


def test_name_cap_and_shared_group() -> None:
    book = _book()
    assert assert_order_margin(book, "BTC-USDT-SWAP", 10, {}, gate_on=False) == 10
    with pytest.raises(RiskLimitError):
        assert_order_margin(book, "BTC-USDT-SWAP", 10.01, {}, gate_on=False)
    with pytest.raises(RiskLimitError):
        assert_order_margin(book, "BTC-USDT-SWAP", 6, {}, gate_on=True)
    assert assert_order_margin(book, "BTC-USDT-SWAP", 5, {}, gate_on=True) == 5

    assert (
        assert_order_margin(
            book,
            "NVDA-USDT-SWAP",
            4,
            {"AMD-USDT-SWAP": 6},
            gate_on=False,
        )
        == 10
    )
    with pytest.raises(RiskLimitError, match="chips"):
        assert_order_margin(book, "NVDA-USDT-SWAP", 5, {"AMD-USDT-SWAP": 6}, gate_on=False)
    with pytest.raises(RiskLimitError, match="chips"):
        assert_order_margin(book, "NVDA-USDT-SWAP", 3, {"AMD-USDT-SWAP": 3}, gate_on=True)

    assert (
        assert_order_margin(
            book,
            "SPCX-USDT-SWAP",
            9,
            {"GOOGL-USDT-SWAP": 9, "SPCX-USDT-SWAP": 0},
            gate_on=False,
        )
        == 10
    )


def test_candidate_pool_shared_caps() -> None:
    book = load_cap_book()
    nvda = book.groups_for("NVDA-USDT-SWAP")
    assert any(group.name == "chips" and group.shared_cap for group in nvda)
    assert "AMD-USDT-SWAP" in nvda[0].inst_ids
    spcx = book.groups_for("SPCX-USDT-SWAP")
    assert spcx and spcx[0].name == "platforms" and spcx[0].shared_cap is False
    assert book.groups_for("BTC-USDT-SWAP") == ()


def test_orders_disabled_message() -> None:
    from tools.okx.guards import RiskGuards

    guards = RiskGuards(allow_orders=False, risk_gate=False, book=_book())
    with pytest.raises(OrdersDisabled):
        guards.assert_orders_enabled()
    guards.allow_orders = True
    guards.assert_orders_enabled()
