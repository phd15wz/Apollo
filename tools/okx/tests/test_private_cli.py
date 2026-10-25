from __future__ import annotations

import json

import pytest

from tools.okx.private_cli import cli_demo, client_options, main
from tools.okx.tests.conftest import FakeTransport, load_fixture
from tools.okx.tests.test_private import _client

pytestmark = pytest.mark.usefixtures("clean_okx_env")


def test_cli_live_reads_and_demo_writes(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli_demo("balance") is False
    assert cli_demo("positions") is False
    assert cli_demo("funding") is False
    code = main(["balance"])
    assert code == 2
    assert "OKX_API_KEY" in capsys.readouterr().err

    code = main(
        [
            "place",
            "--inst",
            "BTC-USDT-SWAP",
            "--side",
            "buy",
            "--sz",
            "1",
            "--td-mode",
            "cross",
            "--ord-type",
            "market",
        ]
    )
    assert code == 2
    assert "OKX_DEMO" in capsys.readouterr().err

    code = main(["cancel", "--inst", "BTC-USDT-SWAP", "--ord-id", "1"])
    assert code == 2
    assert "OKX_DEMO" in capsys.readouterr().err

    monkeypatch.setenv("OKX_DEMO", "0")
    assert cli_demo("positions") is False
    assert cli_demo("funding") is False
    code = main(["order", "--inst", "BTC-USDT-SWAP", "--ord-id", "1"])
    assert code == 2
    assert "OKX_DEMO" in capsys.readouterr().err

    monkeypatch.setenv("OKX_DEMO", "1")
    assert cli_demo("balance") is True
    assert cli_demo("funding") is True
    monkeypatch.setenv("OKX_API_SECRET", "super-secret-value")
    code = main(["balance"])
    captured = capsys.readouterr()
    assert code == 2
    assert "super-secret-value" not in captured.err
    assert "super-secret-value" not in captured.out


def test_cli_live_balance_skips_allow_orders(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    captured: dict[str, object] = {}

    class _Live:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def balance(self, ccy: str = "") -> dict[str, str]:
            return {"ccy": ccy}

    monkeypatch.setattr("tools.okx.private_cli.PrivateClient", _Live)
    code = main(["balance", "--ccy", "USDT"])
    assert code == 0
    assert captured["demo"] is False
    assert captured["allow_orders"] is None
    assert json.loads(capsys.readouterr().out)["ccy"] == "USDT"

    captured.clear()

    class _Funding:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def funding(self, ccy: str = "") -> list[dict[str, str]]:
            return [{"ccy": ccy, "bal": "1"}]

    monkeypatch.setattr("tools.okx.private_cli.PrivateClient", _Funding)
    code = main(["funding", "--ccy", "USDT"])
    assert code == 0
    assert captured["demo"] is False
    assert captured["allow_orders"] is None
    assert json.loads(capsys.readouterr().out) == [{"bal": "1", "ccy": "USDT"}]

    monkeypatch.setenv("OKX_ALLOW_ORDERS", "1")
    code = main(
        [
            "cancel",
            "--inst",
            "BTC-USDT-SWAP",
            "--ord-id",
            "1",
        ]
    )
    assert code == 2
    assert "OKX_DEMO" in capsys.readouterr().err
    assert captured["demo"] is False


def test_cli_balance_and_cancel(capsys: pytest.CaptureFixture[str]) -> None:
    transport = FakeTransport()
    transport.add("/api/v5/account/balance", load_fixture("private_balance.json"))
    transport.add("/api/v5/trade/cancel-order", load_fixture("private_order_ack.json"))
    reader, _clock = _client(transport)
    code = main(["balance", "--ccy", "USDT"], client=reader)
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"][0]["ccy"] == "USDT"
    assert "test-secret" not in json.dumps(payload)

    code = main(
        ["cancel", "--inst", "BTC-USDT-SWAP", "--ord-id", "312269865356374016"],
        client=reader,
    )
    captured = capsys.readouterr()
    assert code == 2
    assert "refused" in captured.err
    assert len(transport.calls) == 1

    writer, _clock = _client(transport, allow_orders=True)
    code = main(
        ["cancel", "--inst", "BTC-USDT-SWAP", "--ord-id", "312269865356374016"],
        client=writer,
    )
    assert code == 0
    ack = json.loads(capsys.readouterr().out)
    assert ack["ord_id"] == "312269865356374016"
    assert transport.calls[-1]["path"] == "/api/v5/trade/cancel-order"

    transport.add("/api/v5/asset/balances", load_fixture("private_funding_balance.json"))
    code = main(["funding", "--ccy", "USDT"], client=reader)
    assert code == 0
    funding = json.loads(capsys.readouterr().out)
    assert funding[0]["ccy"] == "USDT"
    assert funding[0]["avail_bal"] == 20.0
    assert transport.calls[-1]["path"] == "/api/v5/asset/balances"
    assert "x-simulated-trading" in transport.calls[-1]["headers"]


def test_cli_place_reports_margin_and_order_status(capsys: pytest.CaptureFixture[str]) -> None:
    transport = FakeTransport()
    transport.add("/api/v5/trade/order", load_fixture("private_order_ack.json"), once=True)
    transport.add("/api/v5/trade/order", load_fixture("private_order.json"))
    writer, _clock = _client(transport, allow_orders=True)
    code = main(
        [
            "place",
            "--inst",
            "BTC-USDT-SWAP",
            "--side",
            "buy",
            "--sz",
            "1",
            "--td-mode",
            "cross",
            "--ord-type",
            "market",
            "--margin-usd",
            "12",
        ],
        client=writer,
    )
    assert code == 2
    assert "cap" in capsys.readouterr().err
    assert transport.calls == []

    code = main(
        [
            "place",
            "--inst",
            "BTC-USDT-SWAP",
            "--side",
            "buy",
            "--sz",
            "1",
            "--td-mode",
            "cross",
            "--ord-type",
            "limit",
            "--px",
            "60000",
            "--margin-usd",
            "4",
            "--cl-ord-id",
            "demo1",
        ],
        client=writer,
    )
    assert code == 0
    placed = json.loads(capsys.readouterr().out)
    assert placed["ord_id"] == "312269865356374016"

    code = main(
        ["order", "--inst", "BTC-USDT-SWAP", "--ord-id", "312269865356374016"],
        client=writer,
    )
    assert code == 0
    order = json.loads(capsys.readouterr().out)
    assert order["state"] == "live"


def test_cli_live_smoke_requires_opt_in_and_cancels(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["--allow-orders", "smoke"])
    assert code == 2
    assert "OKX_DEMO" in capsys.readouterr().err

    monkeypatch.setenv("OKX_LIVE_TRADE", "1")
    code = main(["smoke"])
    assert code == 2
    assert "--allow-orders" in capsys.readouterr().err

    monkeypatch.setenv("OKX_DEMO", "1")
    code = main(["--allow-orders", "smoke"])
    assert code == 2
    assert "OKX_DEMO" in capsys.readouterr().err
    monkeypatch.delenv("OKX_DEMO")

    code = main(["order", "--inst", "BTC-USDT", "--ord-id", "1"])
    assert code == 2
    assert "OKX_DEMO" in capsys.readouterr().err

    captured: dict[str, object] = {}

    class _SmokeClient:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)
            self.demo = kwargs.get("demo")

        def smoke_place_cancel(self, **kwargs: object) -> tuple[dict[str, str], dict[str, str]]:
            return {"ord_id": "1"}, {"ord_id": "1"}

        def funding(self, ccy: str = "") -> list[dict[str, str]]:
            return [{"ccy": ccy}]

    monkeypatch.setattr("tools.okx.private_cli.PrivateClient", _SmokeClient)
    code = main(["--allow-orders", "smoke", "--px", "40000", "--margin-usd", "1"])
    assert code == 0
    assert captured["demo"] is False
    assert captured["allow_orders"] is True
    assert captured["live_trade"] is True
    payload = json.loads(capsys.readouterr().out)
    assert payload["placed"]["ord_id"] == "1"
    assert payload["cancelled"]["ord_id"] == "1"

    captured.clear()
    code = main(["funding", "--ccy", "USDT"])
    assert code == 0
    assert captured["demo"] is False
    assert captured["allow_orders"] is None
    assert "live_trade" not in captured
    assert json.loads(capsys.readouterr().out) == [{"ccy": "USDT"}]

    transport = FakeTransport()
    ack = load_fixture("private_order_ack.json")
    transport.add("/api/v5/trade/order", ack)
    transport.add("/api/v5/trade/cancel-order", ack)
    live, _clock = _client(transport, demo=False, allow_orders=True, live_trade=True)
    code = main(
        [
            "place",
            "--inst",
            "BTC-USDT",
            "--side",
            "buy",
            "--sz",
            "0.001",
            "--td-mode",
            "cash",
            "--ord-type",
            "limit",
            "--px",
            "40000",
            "--margin-usd",
            "1",
        ],
        client=live,
    )
    assert code == 0
    placed_io = capsys.readouterr()
    nested = json.loads(placed_io.out)
    assert nested["placed"]["ord_id"] == "312269865356374016"
    assert nested["cancelled"]["ord_id"] == nested["placed"]["ord_id"]
    assert transport.calls[0]["path"] == "/api/v5/trade/order"
    assert transport.calls[1]["path"] == "/api/v5/trade/cancel-order"
    assert "x-simulated-trading" not in transport.calls[0]["headers"]
    assert "cancelling live order" in placed_io.err

    before = len(transport.calls)
    code = main(
        [
            "place",
            "--inst",
            "BTC-USDT-SWAP",
            "--side",
            "buy",
            "--sz",
            "1",
            "--td-mode",
            "cross",
            "--ord-type",
            "market",
            "--margin-usd",
            "1",
        ],
        client=live,
    )
    assert code == 2
    assert "BTC-USDT" in capsys.readouterr().err
    assert len(transport.calls) == before

    code = main(["cancel", "--inst", "BTC-USDT-SWAP", "--ord-id", "312269865356374016"], client=live)
    assert code == 2
    assert len(transport.calls) == before


def test_client_options_from_flags() -> None:
    parser_args = client_options(
        type("A", (), {"allow_orders": True, "read_only": False, "risk_gate": True})()
    )
    assert parser_args == {"allow_orders": True, "read_only": None, "risk_gate": True}
    readonly = client_options(
        type("A", (), {"allow_orders": False, "read_only": True, "risk_gate": False})()
    )
    assert readonly["read_only"] is True
    assert readonly["allow_orders"] is None
    assert readonly["risk_gate"] is None
