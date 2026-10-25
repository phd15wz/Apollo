from __future__ import annotations

import pytest

from tools.okx.errors import LiveTradingDisabled, OrdersDisabled, RiskLimitError, UnsafeEndpointError
from tools.okx.guards import (
    ALLOWED_PRIVATE,
    LIVE_TRADE_ENABLED,
    CapBook,
    CapGroup,
    assert_order_margin,
    assert_private_request,
    load_cap_book,
    margin_ceiling,
    require_cli_demo,
    resolve_allow_orders,
    resolve_demo,
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
    with pytest.raises(LiveTradingDisabled):
        resolve_demo(False)
    monkeypatch.setenv("OKX_DEMO", "0")
    with pytest.raises(LiveTradingDisabled):
        resolve_demo(True)
    monkeypatch.setenv("OKX_DEMO", "1")
    assert resolve_demo(True) is True
    monkeypatch.setattr("tools.okx.guards.LIVE_TRADE_ENABLED", True)
    with pytest.raises(LiveTradingDisabled):
        resolve_demo(True)


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


def test_allowlist_is_trade_and_account_only() -> None:
    assert ("GET", "/api/v5/account/balance") in ALLOWED_PRIVATE
    assert ("POST", "/api/v5/trade/cancel-order") in ALLOWED_PRIVATE
    assert ("POST", "/api/v5/trade/order") in ALLOWED_PRIVATE
    for method, path in ALLOWED_PRIVATE:
        assert method in {"GET", "POST"}
        assert path.startswith("/api/v5/account") or path.startswith("/api/v5/trade")
        lowered = path.lower()
        assert "withdraw" not in lowered
        assert "transfer" not in lowered
        assert "deposit" not in lowered
        assert not lowered.startswith("/api/v5/asset")
        assert not lowered.startswith("/api/v5/finance")


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/api/v5/asset/withdrawal"),
        ("POST", "/api/v5/asset/transfer"),
        ("GET", "/api/v5/asset/deposit-address"),
        ("GET", "/api/v5/account/max-withdrawal"),
        ("POST", "/api/v5/finance/savings/purchase"),
        ("GET", "/api/v5/asset/currencies"),
        ("POST", "/api/v5/trade/batch-orders"),
        ("DELETE", "/api/v5/trade/order"),
        ("GET", "/api/v5/trade/order-algo"),
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
