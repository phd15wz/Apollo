"""Offline checks for the isolated set-leverage allowlist.

These tests do not touch the network. Live and demo HTTP is a fake transport.
"""

from __future__ import annotations

import json

import pytest

from tools.okx.auth import sign
from tools.okx.client import PublicClient, RateLimiter
from tools.okx.errors import LiveTradingDisabled, OrdersDisabled, UnsafeEndpointError
from tools.okx.guards import SET_LEVERAGE_PATH, assert_live_trade_payload, assert_set_leverage_payload
from tools.okx.private_cli import main
from tools.okx.tests.conftest import FakeClock, FakeTransport
from tools.okx.tests.test_private import _SECRET, _TS, _client

pytestmark = pytest.mark.usefixtures("clean_okx_env")

_INST = "BTC-USDT-SWAP"


def _ack(lever: str = "1", inst_id: str = _INST, pos_side: str = "") -> dict[str, object]:
    return {
        "code": "0",
        "msg": "",
        "data": [
            {
                "instId": inst_id,
                "lever": lever,
                "mgnMode": "isolated",
                "posSide": pos_side,
            }
        ],
    }


def _body(lever: str, inst_id: str = _INST, pos_side: str = "") -> str:
    payload: dict[str, str] = {"instId": inst_id, "lever": lever, "mgnMode": "isolated"}
    if pos_side:
        payload["posSide"] = pos_side
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


@pytest.mark.parametrize("lever", [1, "1", 2, "3", 4, "5", 5])
def test_demo_isolated_levers_one_through_five(lever: int | str) -> None:
    transport = FakeTransport()
    transport.add(SET_LEVERAGE_PATH, _ack(str(int(lever))))
    client, _clock = _client(transport, allow_orders=True)
    result = client.set_leverage(_INST, lever)
    assert result.inst_id == _INST
    assert result.lever == str(int(lever))
    assert result.mgn_mode == "isolated"
    assert result.pos_side == ""
    call = transport.calls[0]
    assert call["method"] == "POST"
    assert call["path"] == SET_LEVERAGE_PATH
    assert call["headers"]["x-simulated-trading"] == "1"
    body = call["body"].decode()
    assert body == _body(str(int(lever)))
    assert "ccy" not in body
    assert call["headers"]["OK-ACCESS-SIGN"] == sign(_SECRET, _TS, "POST", SET_LEVERAGE_PATH, body)


def test_demo_long_short_pos_side_is_optional() -> None:
    transport = FakeTransport()
    transport.add(SET_LEVERAGE_PATH, _ack(pos_side="long"))
    client, _clock = _client(transport, allow_orders=True)
    result = client.set_leverage("ETH-USDT-SWAP", "2", pos_side="long")
    assert result.pos_side == "long"
    body = transport.calls[0]["body"].decode()
    assert body == _body("2", "ETH-USDT-SWAP", "long")
    assert "ccy" not in body


@pytest.mark.parametrize(
    "kwargs",
    [
        {"mgn_mode": "cross"},
        {"mgn_mode": "CROSS"},
        {"mgn_mode": "cash"},
        {"lever": 6},
        {"lever": "6"},
        {"lever": 0},
        {"lever": -1},
        {"lever": "0"},
        {"lever": "5.0"},
        {"lever": 5.0},
        {"lever": 1.5},
        {"lever": True},
        {"lever": "01"},
        {"lever": "+1"},
        {"inst_id": "BTC-USDT"},
        {"inst_id": "BTC-USD-SWAP"},
        {"inst_id": "btc-usdt-swap"},
        {"inst_id": ""},
        {"pos_side": "net"},
        {"pos_side": "buy"},
    ],
)
def test_payload_lock_refuses_before_http(kwargs: dict[str, object]) -> None:
    transport = FakeTransport()
    client, _clock = _client(transport, allow_orders=True)
    call = {"inst_id": _INST, "lever": 1, "mgn_mode": "isolated", "pos_side": ""}
    call.update(kwargs)
    with pytest.raises(ValueError):
        client.set_leverage(
            str(call["inst_id"]),
            call["lever"],  # type: ignore[arg-type]
            mgn_mode=str(call["mgn_mode"]),
            pos_side=str(call["pos_side"]),
        )
    assert transport.calls == []


def test_ccy_and_cross_bodies_are_refused() -> None:
    with pytest.raises(ValueError, match="isolated"):
        assert_set_leverage_payload(
            {"instId": _INST, "lever": "5", "mgnMode": "cross", "ccy": "USDT"}
        )
    with pytest.raises(ValueError, match="isolated"):
        assert_set_leverage_payload({"instId": _INST, "lever": "5", "mgnMode": "isolated", "ccy": "USDT"})
    with pytest.raises(ValueError, match="1 to 5"):
        assert_set_leverage_payload({"instId": _INST, "lever": "6", "mgnMode": "isolated"})
    assert assert_set_leverage_payload({"instId": _INST, "lever": 1, "mgnMode": "isolated"}) == {
        "instId": _INST,
        "lever": "1",
        "mgnMode": "isolated",
    }
    with pytest.raises(ValueError, match="1 to 5"):
        assert_live_trade_payload(
            "POST",
            SET_LEVERAGE_PATH,
            {"instId": _INST, "lever": "9", "mgnMode": "isolated"},
        )
    assert_live_trade_payload(
        "POST",
        SET_LEVERAGE_PATH,
        {"instId": _INST, "lever": "5", "mgnMode": "isolated"},
    )


def test_missing_opt_in_does_not_send() -> None:
    transport = FakeTransport()
    live_locked, _clock = _client(transport, demo=False, allow_orders=True)
    with pytest.raises(LiveTradingDisabled, match="Live Trade"):
        live_locked.set_leverage(_INST, 1)
    no_go, _clock = _client(transport, demo=False, allow_orders=False, live_trade=True)
    with pytest.raises(OrdersDisabled):
        no_go.set_leverage(_INST, 1)
    demo_locked, _clock = _client(transport)
    with pytest.raises(OrdersDisabled):
        demo_locked.set_leverage(_INST, 1)
    assert transport.calls == []


def test_read_only_beats_live_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OKX_READ_ONLY", "1")
    transport = FakeTransport()
    client, _clock = _client(transport, demo=False, allow_orders=True, live_trade=True)
    assert client.allow_orders is False
    with pytest.raises(OrdersDisabled):
        client.set_leverage(_INST, 1)
    assert transport.calls == []


def test_live_isolated_set_leverage_signs_without_demo_header() -> None:
    transport = FakeTransport()
    transport.add(SET_LEVERAGE_PATH, _ack("1"))
    client, _clock = _client(transport, demo=False, allow_orders=True, live_trade=True)
    result = client.set_leverage(_INST, "1")
    assert result.lever == "1"
    call = transport.calls[0]
    assert call["path"] == SET_LEVERAGE_PATH
    assert "x-simulated-trading" not in call["headers"]
    body = call["body"].decode()
    assert body == _body("1")
    assert call["headers"]["OK-ACCESS-SIGN"] == sign(_SECRET, _TS, "POST", SET_LEVERAGE_PATH, body)


def test_env_opt_in_opens_live_set_leverage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OKX_LIVE_TRADE", "1")
    monkeypatch.setenv("OKX_ALLOW_ORDERS", "1")
    monkeypatch.setenv("OKX_DEMO", "0")
    transport = FakeTransport()
    transport.add(SET_LEVERAGE_PATH, _ack("4"))
    client, _clock = _client(transport, demo=False)
    assert client.demo is False
    assert client.allow_orders is True
    assert client.live_trade is True
    assert client.set_leverage(_INST, 4).lever == "4"
    assert transport.calls[0]["body"] == _body("4").encode()
    assert "x-simulated-trading" not in transport.calls[0]["headers"]


def test_other_account_paths_stay_blocked_with_opt_in() -> None:
    transport = FakeTransport()
    client, _clock = _client(transport, demo=False, allow_orders=True, live_trade=True)
    blocked = (
        ("POST", "/api/v5/account/set-position-mode", {"posMode": "net_mode"}),
        ("POST", "/api/v5/account/set-account-level", {"acctLv": "2"}),
        ("POST", "/api/v5/account/config", {}),
        ("GET", "/api/v5/account/config", None),
        ("POST", "/api/v5/asset/transfer", {"ccy": "USDT", "amt": "1"}),
        ("POST", "/api/v5/asset/withdrawal", {"ccy": "USDT", "amt": "1"}),
        ("GET", "/api/v5/asset/deposit-address", None),
        ("POST", "/api/v5/asset/deposit-address", None),
    )
    for method, path, payload in blocked:
        with pytest.raises(UnsafeEndpointError):
            client._request(method, path, payload=payload)
    with pytest.raises(ValueError, match="isolated"):
        client._request(
            "POST",
            SET_LEVERAGE_PATH,
            payload={"instId": _INST, "lever": "5", "mgnMode": "cross"},
        )
    with pytest.raises(ValueError, match="1 to 5"):
        client.set_leverage(_INST, 6)
    assert transport.calls == []


def test_public_client_still_refuses_set_leverage() -> None:
    client = PublicClient(
        transport=FakeTransport(),
        clock=FakeClock(),
        sleep=lambda _seconds: None,
        limiter=RateLimiter(clock=FakeClock(), sleep=lambda _seconds: None),
    )
    with pytest.raises(UnsafeEndpointError):
        client.get(SET_LEVERAGE_PATH)


def test_set_leverage_rate_limit_waits() -> None:
    clock = FakeClock()
    transport = FakeTransport()
    transport.add(SET_LEVERAGE_PATH, _ack())
    client, _clock = _client(transport, clock=clock, allow_orders=True)
    for _ in range(20):
        client.set_leverage(_INST, 1)
    assert clock.sleeps == []
    client.set_leverage(_INST, 1)
    assert clock.sleeps
    assert clock.sleeps[0] == pytest.approx(2.0)


def test_cli_set_leverage_follows_demo_and_live_gates(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["set-leverage", "--inst", _INST, "--lever", "1"])
    assert code == 2
    assert "OKX_DEMO" in capsys.readouterr().err

    monkeypatch.setenv("OKX_LIVE_TRADE", "1")
    code = main(["set-leverage", "--inst", _INST, "--lever", "1"])
    assert code == 2
    assert "--allow-orders" in capsys.readouterr().err

    monkeypatch.setenv("OKX_DEMO", "1")
    code = main(["--allow-orders", "set-leverage", "--inst", _INST, "--lever", "1"])
    assert code == 2
    assert "OKX_DEMO" in capsys.readouterr().err
    monkeypatch.delenv("OKX_DEMO")

    captured: dict[str, object] = {}

    class _Client:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)
            self.demo = kwargs.get("demo")

        def set_leverage(self, inst_id: str, lever: str, **kwargs: object) -> dict[str, str]:
            return {"inst_id": inst_id, "lever": lever, "mgn_mode": str(kwargs.get("mgn_mode"))}

    monkeypatch.setattr("tools.okx.private_cli.PrivateClient", _Client)
    code = main(
        ["--allow-orders", "set-leverage", "--inst", "ETH-USDT-SWAP", "--lever", "1", "--mgn-mode", "isolated"]
    )
    assert code == 0
    assert captured["demo"] is False
    assert captured["allow_orders"] is True
    assert captured["live_trade"] is True
    assert json.loads(capsys.readouterr().out)["inst_id"] == "ETH-USDT-SWAP"

    transport = FakeTransport()
    transport.add(SET_LEVERAGE_PATH, _ack("3"))
    demo, _clock = _client(transport, allow_orders=True)
    code = main(["set-leverage", "--inst", _INST, "--lever", "3"], client=demo)
    assert code == 0
    assert transport.calls[0]["headers"]["x-simulated-trading"] == "1"
    assert transport.calls[0]["body"] == _body("3").encode()
    assert json.loads(capsys.readouterr().out)["lever"] == "3"

    before = len(transport.calls)
    code = main(
        ["set-leverage", "--inst", _INST, "--lever", "1", "--mgn-mode", "cross"],
        client=demo,
    )
    assert code == 2
    assert "isolated" in capsys.readouterr().err
    code = main(["set-leverage", "--inst", _INST, "--lever", "6"], client=demo)
    assert code == 2
    assert "1 to 5" in capsys.readouterr().err
    assert len(transport.calls) == before

    monkeypatch.delenv("OKX_LIVE_TRADE", raising=False)
    live_locked, _clock = _client(FakeTransport(), demo=False, allow_orders=True, live_trade=False)
    code = main(["set-leverage", "--inst", _INST, "--lever", "1"], client=live_locked)
    assert code == 2
    assert "Live Trade" in capsys.readouterr().err
