"""Offline sanctioned-read checks. No network and no portfolio authorization."""

from __future__ import annotations

import pytest

from tools.okx.errors import LiveTradingDisabled, UnsafeEndpointError
from tools.okx.guards import assert_live_place
from tools.okx.tests.conftest import FakeTransport, load_fixture, make_client
from tools.okx.tests.test_private import _client

pytestmark = pytest.mark.usefixtures("clean_okx_env")

NOW = 1_700_000_000_000


def test_portfolio_flag_defaults_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OKX_PILOT_PLAN", "1")
    client, _clock = _client(FakeTransport(), demo=False)
    assert client.pilot_plan is False
    with pytest.raises(TypeError):
        _client(FakeTransport(), demo=False, pilot_plan="yes")  # type: ignore[call-arg]
    with pytest.raises(LiveTradingDisabled):
        assert_live_place(
            inst_id="COHR-USDT-SWAP",
            side="buy",
            sz="0.02",
            td_mode="isolated",
            ord_type="limit",
            px="280",
        )


def test_sanctioned_reads_do_not_need_trade_or_the_demo_header() -> None:
    transport = FakeTransport()
    transport.add("/api/v5/account/config", {"code": "0", "data": [{
        "acctLv": "2", "posMode": "net_mode", "perm": "read_only", "level": "Lv1",
        "uid": "999", "label": "secret", "ip": "203.0.113.5",
    }]})
    transport.add("/api/v5/account/trade-fee", {"code": "0", "data": [{
        "instType": "SWAP", "level": "Lv1", "ts": str(NOW), "maker": "-0.0002", "taker": "-0.0005",
        "feeGroup": [{"groupId": "g1", "maker": "-0.0002", "taker": "-0.0005"}],
    }]})
    transport.add("/api/v5/account/leverage-info", {"code": "0", "data": [{
        "instId": "ABC-USDT-SWAP", "mgnMode": "isolated", "posSide": "net", "lever": "2",
    }]})
    transport.add("/api/v5/account/bills", {"code": "0", "data": [{
        "billId": "1", "instId": "ABC-USDT-SWAP", "type": "8", "subType": "173",
        "ts": str(NOW), "pnl": "-0.2", "fee": "0", "ccy": "USDT",
    }]})
    transport.add("/api/v5/trade/orders-pending", {"code": "0", "data": []})
    transport.add("/api/v5/trade/order", load_fixture("private_order.json"))
    transport.add("/api/v5/trade/orders-history", {"code": "0", "data": []})
    transport.add("/api/v5/account/positions", {"code": "0", "data": [{
        "instId": "ABC-USDT-SWAP", "instType": "SWAP", "posSide": "net", "pos": "0.01",
        "avgPx": "100", "upl": "-1", "margin": "1", "mgnMode": "isolated", "lever": "2",
        "uTime": str(NOW), "liqPx": "", "markPx": "100", "fee": "-0.01", "fundingFee": "0",
        "realizedPnl": "0",
    }]})
    client, _clock = _client(transport, demo=False, allow_orders=False, live_trade=False)
    config = client.account_config()
    assert config.acct_lv == "2"
    assert not hasattr(config, "uid")
    fee = client.trade_fee("SWAP", inst_family="ABC-USDT")
    assert fee.groups[0].taker == "-0.0005"
    assert client.leverage_info("ABC-USDT-SWAP")[0].lever == "2"
    bill = client.bills(inst_id="ABC-USDT-SWAP", begin=str(NOW))[0]
    assert bill.pnl == "-0.2"
    assert client.open_orders(inst_type="SWAP") == []
    order = client.get_order("BTC-USDT-SWAP", ord_id="312269865356374016")
    assert order.state == "live"
    assert client.order_history("SWAP", inst_id="ABC-USDT-SWAP") == []
    position = client.positions(inst_type="SWAP")[0]
    assert position.liq_px == ""
    assert position.fee == "-0.01"
    assert all(call["method"] == "GET" for call in transport.calls)
    assert all("x-simulated-trading" not in call["headers"] for call in transport.calls)
    with pytest.raises(LiveTradingDisabled):
        client.order_history("SWAP", archived=True)
    with pytest.raises(UnsafeEndpointError):
        client._request("GET", "/api/v5/account/position-tiers")
    public = FakeTransport()
    public.add("/api/v5/public/position-tiers", {"code": "0", "data": [{
        "instId": "ABC-USDT-SWAP", "instFamily": "ABC-USDT", "uly": "ABC-USDT", "tier": "1",
        "minSz": "0", "maxSz": "100", "mmr": "0.004", "imr": "0.01", "maxLever": "50",
    }]})
    tiers = make_client(public).position_tiers("SWAP", td_mode="isolated", inst_id="ABC-USDT-SWAP")
    assert tiers[0].mmr == "0.004"
    assert public.calls[0]["method"] == "GET"
