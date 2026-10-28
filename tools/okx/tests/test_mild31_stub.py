"""Offline checks for the locked mild-3.1 equity SWAP allowlist.

These tests do not touch the network. Live HTTP is a fake transport.
Nothing here places an order on OKX.
"""

from __future__ import annotations

import json

import pytest

from tools.okx.errors import LiveTradingDisabled, OrdersDisabled, UnsafeEndpointError
from tools.okx.guards import (
    MILD31_LOTS,
    MILD31_MAX_LEVER,
    MILD31_STUB,
    SET_LEVERAGE_PATH,
    assert_live_trade_payload,
    assert_mild31_place,
    assert_private_request,
    assert_set_leverage_payload,
)
from tools.okx.private_cli import main
from tools.okx.tests.conftest import FakeTransport, load_fixture
from tools.okx.tests.test_private import _client

pytestmark = pytest.mark.usefixtures("clean_okx_env")


def _place(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "inst_id": "MU-USDT-SWAP",
        "side": "buy",
        "sz": "0.01",
        "td_mode": "isolated",
        "ord_type": "limit",
        "px": "967.79",
    }
    body.update(overrides)
    return body


def test_stub_shape_is_the_five_locked_lots() -> None:
    assert [lot.inst_id for lot in MILD31_LOTS] == [
        "MU-USDT-SWAP",
        "SKHY-USDT-SWAP",
        "LITE-USDT-SWAP",
        "TSM-USDT-SWAP",
        "QQQ-USDT-SWAP",
    ]
    assert MILD31_STUB["MU-USDT-SWAP"].max_sz == "0.01"
    assert MILD31_STUB["LITE-USDT-SWAP"].max_sz == "0.01"
    assert MILD31_STUB["SKHY-USDT-SWAP"].max_sz == "0.03"
    assert MILD31_STUB["TSM-USDT-SWAP"].max_sz == "0.02"
    assert MILD31_STUB["QQQ-USDT-SWAP"].side == "sell"
    assert MILD31_STUB["QQQ-USDT-SWAP"].max_sz == "0.01"
    assert MILD31_MAX_LEVER == 2
    for lot in MILD31_LOTS:
        if lot.inst_id == "QQQ-USDT-SWAP":
            assert lot.side == "sell"
        else:
            assert lot.side == "buy"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"inst_id": "MU-USDT-SWAP", "side": "buy", "sz": "0.01"},
        {"inst_id": "SKHY-USDT-SWAP", "side": "buy", "sz": "0.03"},
        {"inst_id": "SKHY-USDT-SWAP", "side": "buy", "sz": "0.02"},
        {"inst_id": "LITE-USDT-SWAP", "side": "buy", "sz": "0.01", "ord_type": "ioc"},
        {"inst_id": "TSM-USDT-SWAP", "side": "buy", "sz": "0.02", "pos_side": "long"},
        {"inst_id": "QQQ-USDT-SWAP", "side": "sell", "sz": "0.01", "pos_side": "short"},
    ],
)
def test_allowlist_accepts_stub_lots(kwargs: dict[str, object]) -> None:
    assert_mild31_place(**_place(**kwargs))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"inst_id": "NVDA-USDT-SWAP", "side": "buy", "sz": "0.01"},
        {"inst_id": "BTC-USDT-SWAP", "side": "buy", "sz": "0.01"},
        {"inst_id": "MU-USDT-SWAP", "side": "sell", "sz": "0.01"},
        {"inst_id": "QQQ-USDT-SWAP", "side": "buy", "sz": "0.01"},
        {"inst_id": "MU-USDT-SWAP", "sz": "0.02"},
        {"inst_id": "SKHY-USDT-SWAP", "sz": "0.04"},
        {"inst_id": "TSM-USDT-SWAP", "sz": "0.03"},
        {"inst_id": "QQQ-USDT-SWAP", "side": "sell", "sz": "0.02"},
        {"inst_id": "MU-USDT-SWAP", "sz": "0.015"},
        {"inst_id": "MU-USDT-SWAP", "td_mode": "cross"},
        {"inst_id": "MU-USDT-SWAP", "td_mode": "cash"},
        {"inst_id": "MU-USDT-SWAP", "ord_type": "market"},
        {"inst_id": "MU-USDT-SWAP", "reduce_only": True},
        {"inst_id": "MU-USDT-SWAP", "pos_side": "short"},
        {"inst_id": "QQQ-USDT-SWAP", "side": "sell", "pos_side": "long"},
    ],
)
def test_allowlist_refuses_outside_the_lock(kwargs: dict[str, object]) -> None:
    with pytest.raises(LiveTradingDisabled, match="mild-3.1"):
        assert_mild31_place(**_place(**kwargs))


def test_payload_gate_allows_stub_and_still_blocks_other_routes() -> None:
    assert_live_trade_payload(
        "POST",
        "/api/v5/trade/order",
        {
            "instId": "LITE-USDT-SWAP",
            "tdMode": "isolated",
            "side": "buy",
            "ordType": "limit",
            "sz": "0.01",
            "px": "1021.35",
        },
    )
    assert_live_trade_payload(
        "POST",
        "/api/v5/trade/cancel-order",
        {"instId": "TSM-USDT-SWAP", "ordId": "1"},
    )
    with pytest.raises(LiveTradingDisabled):
        assert_live_trade_payload(
            "POST",
            "/api/v5/trade/order",
            {
                "instId": "NVDA-USDT-SWAP",
                "tdMode": "isolated",
                "side": "buy",
                "ordType": "limit",
                "sz": "0.01",
                "px": "100",
            },
        )
    with pytest.raises(LiveTradingDisabled):
        assert_live_trade_payload(
            "POST",
            "/api/v5/trade/amend-order",
            {"instId": "MU-USDT-SWAP", "ordId": "1", "newPx": "900"},
        )
    with pytest.raises(UnsafeEndpointError, match="withdraw"):
        assert_private_request("POST", "/api/v5/asset/withdrawal")
    with pytest.raises(UnsafeEndpointError, match="transfer"):
        assert_private_request("POST", "/api/v5/asset/transfer")


def test_stub_leverage_stops_at_two() -> None:
    assert assert_set_leverage_payload(
        {"instId": "MU-USDT-SWAP", "lever": "2", "mgnMode": "isolated"}
    )["lever"] == "2"
    assert assert_set_leverage_payload(
        {"instId": "QQQ-USDT-SWAP", "lever": 1, "mgnMode": "isolated"}
    )["lever"] == "1"
    with pytest.raises(ValueError, match="1 to 2"):
        assert_set_leverage_payload({"instId": "SKHY-USDT-SWAP", "lever": "3", "mgnMode": "isolated"})
    with pytest.raises(ValueError, match="1 to 2"):
        assert_set_leverage_payload({"instId": "TSM-USDT-SWAP", "lever": 5, "mgnMode": "isolated"})
    assert assert_set_leverage_payload(
        {"instId": "BTC-USDT-SWAP", "lever": "5", "mgnMode": "isolated"}
    )["lever"] == "5"


def test_live_place_rests_and_dual_opt_in_still_required() -> None:
    ack = load_fixture("private_order_ack.json")
    transport = FakeTransport()
    transport.add("/api/v5/trade/order", ack)
    transport.add("/api/v5/trade/cancel-order", ack)

    missing_trade, _clock = _client(transport, demo=False, allow_orders=True, live_trade=False)
    with pytest.raises(LiveTradingDisabled):
        missing_trade.place_order(**_place(), margin_usd=1)
    missing_go, _clock = _client(transport, demo=False, allow_orders=False, live_trade=True)
    with pytest.raises(OrdersDisabled):
        missing_go.place_order(**_place(), margin_usd=1)
    assert transport.calls == []

    live, _clock = _client(transport, demo=False, allow_orders=True, live_trade=True)
    placed = live.place_order(**_place(), margin_usd=1)
    assert placed.ord_id
    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call["path"] == "/api/v5/trade/order"
    assert "x-simulated-trading" not in call["headers"]
    assert json.loads(call["body"]) == {
        "instId": "MU-USDT-SWAP",
        "ordType": "limit",
        "px": "967.79",
        "side": "buy",
        "sz": "0.01",
        "tdMode": "isolated",
    }
    before = len(transport.calls)
    with pytest.raises(LiveTradingDisabled):
        live.place_order(**_place(inst_id="NVDA-USDT-SWAP"), margin_usd=1)
    with pytest.raises(LiveTradingDisabled):
        live.place_order(**_place(td_mode="cross"), margin_usd=1)
    with pytest.raises(LiveTradingDisabled):
        live.place_order(**_place(inst_id="QQQ-USDT-SWAP", side="buy"), margin_usd=1)
    with pytest.raises(LiveTradingDisabled):
        live.cancel_order("NVDA-USDT-SWAP", ord_id="1")
    assert len(transport.calls) == before

    cancelled = live.cancel_order("MU-USDT-SWAP", ord_id=placed.ord_id)
    assert cancelled.ord_id == placed.ord_id
    assert transport.calls[-1]["path"] == "/api/v5/trade/cancel-order"
    assert b'"instId":"MU-USDT-SWAP"' in transport.calls[-1]["body"]


def test_cli_stub_place_does_not_cancel(capsys: pytest.CaptureFixture[str]) -> None:
    transport = FakeTransport()
    ack = load_fixture("private_order_ack.json")
    transport.add("/api/v5/trade/order", ack)
    transport.add("/api/v5/trade/cancel-order", ack)
    live, _clock = _client(transport, demo=False, allow_orders=True, live_trade=True)
    code = main(
        [
            "place",
            "--inst",
            "SKHY-USDT-SWAP",
            "--side",
            "buy",
            "--sz",
            "0.03",
            "--td-mode",
            "isolated",
            "--ord-type",
            "limit",
            "--px",
            "160.45",
            "--margin-usd",
            "3",
        ],
        client=live,
    )
    assert code == 0
    placed = json.loads(capsys.readouterr().out)
    assert placed["ord_id"] == "312269865356374016"
    assert "cancelled" not in placed
    assert len(transport.calls) == 1
    assert transport.calls[0]["path"] == "/api/v5/trade/order"

    code = main(["cancel", "--inst", "SKHY-USDT-SWAP", "--ord-id", placed["ord_id"]], client=live)
    assert code == 0
    assert transport.calls[-1]["path"] == "/api/v5/trade/cancel-order"


def test_cli_stub_leverage_above_two_is_refused(capsys: pytest.CaptureFixture[str]) -> None:
    transport = FakeTransport()
    transport.add(
        SET_LEVERAGE_PATH,
        {"code": "0", "msg": "", "data": [{"instId": "LITE-USDT-SWAP", "lever": "2", "mgnMode": "isolated"}]},
    )
    live, _clock = _client(transport, demo=False, allow_orders=True, live_trade=True)
    code = main(
        ["set-leverage", "--inst", "LITE-USDT-SWAP", "--lever", "3", "--mgn-mode", "isolated"],
        client=live,
    )
    assert code == 2
    assert "1 to 2" in capsys.readouterr().err
    assert transport.calls == []
    code = main(
        ["set-leverage", "--inst", "LITE-USDT-SWAP", "--lever", "2", "--mgn-mode", "isolated"],
        client=live,
    )
    assert code == 0
    assert json.loads(transport.calls[0]["body"])["lever"] == "2"
    assert json.loads(transport.calls[0]["body"])["mgnMode"] == "isolated"
