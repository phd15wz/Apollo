"""Offline checks for the 2026-11-19 book.

NO NETWORK. NO TRADE. Synthetic prices are not the Nov 19 quote.
Fractions and attempt counts in this file are test policy, not a
risk-approved buffer. A passing mock is not proof of a live exit.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from tools.okx.client import HttpResponse, RateLimiter
from tools.okx.errors import LiveTradingDisabled, OrdersDisabled, RiskLimitError, UnsafeEndpointError
from tools.okx.guards import SANCTIONED_LIVE_READS, assert_live_place
from tools.okx.pilot_audit import AuditError, load_audit
from tools.okx.pilot_book import OkxPilotBook
from tools.okx.pilot_control import (
    PILOT_LEGS,
    AccountSnap,
    BillSnap,
    FeeSnap,
    InstrumentSnap,
    LeverSnap,
    MarkSnap,
    OrderRequest,
    OrderSnap,
    PilotBlocked,
    PilotController,
    PilotNotFound,
    PilotRejected,
    PilotResult,
    PilotTransportError,
    PositionSnap,
    _fresh,
    TierSnap,
    projection_ready,
)
from tools.okx.pilot_policy import (
    LIVE_CLEARANCE,
    NET_LOSS_TRIGGER_USDT,
    PILOT_LEG,
    BillInput,
    PilotPolicy,
    PnlInputError,
    cap_problems,
    commission_rate,
    net_pnl,
    policy_problems,
    portfolio_amounts,
    project_isolated_liq,
    stop_price,
)
from tools.okx.tests.conftest import FakeClock, FakeTransport, load_fixture, make_client
from tools.okx.tests.test_private import _client

pytestmark = pytest.mark.usefixtures("clean_okx_env")

NOW = 1_700_000_000_000
# SYNTHETIC notionals: gross 19.5, initial margin 9.75. Not the Nov 19 quote.
MARKS = {
    "MU-USDT-SWAP": Decimal("370"),
    "LITE-USDT-SWAP": Decimal("350"),
    "COHR-USDT-SWAP": Decimal("280"),
    "QQQ-USDT-SWAP": Decimal("670"),
}


def _policy(**overrides: object) -> PilotPolicy:
    """TEST POLICY. 0.25 and 3 attempts are fixtures, not approved settings."""
    values: dict[str, object] = {
        "distance_to_liq_fraction": Decimal("0.25"),
        "max_slippage_fraction": Decimal("0.01"),
        "max_input_age_ms": 5_000,
        "entry_ord_type": "ioc",
        "limit_rest_steps": None,
        "max_close_attempts": 3,
        "acknowledge_leg_can_liquidate_before_portfolio_stop": True,
        "acknowledge_public_tier_table": True,
        "acknowledge_between_check_risk": True,
    }
    values.update(overrides)
    return PilotPolicy(**values)  # type: ignore[arg-type]


class SyntheticBook:
    """In-memory book. It does not open a socket and it cannot set leverage."""

    def __init__(self) -> None:
        self.now = NOW
        self.marks = dict(MARKS)
        self.mark_ts = {inst: NOW for inst in MARKS}
        self.state = {
            inst: {"pos": Decimal("0"), "avg": None, "upl": Decimal("0"), "liq": None}
            for inst in PILOT_LEG
        }
        self.orders: dict[str, OrderSnap] = {}
        self.ledger: list[BillSnap] = []
        self.placed: list[OrderRequest] = []
        self.cancelled: list[str] = []
        self.fail_entry: set[str] = set()
        self.partial: dict[str, Decimal] = {}
        self.rest_on_place = False
        self.fill_on_cancel = False
        self.close_failures = 0
        self.foreign: list[OrderSnap] = []
        self.hide_pending = False
        self.hide_positions = False
        self.hide_bills = False
        self.lookup_fail: set[str] = set()
        self.acct_lv = "2"
        self.pos_mode = "net_mode"
        self.perm = "read_only,trade"
        self.lever = "2"
        self.mmr = Decimal("0.01")
        self.taker = Decimal("-0.001")
        self.tier_error = False
        self.fee_error = False

    def set_leverage(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError("the pilot must not set leverage")

    def account(self) -> AccountSnap:
        return AccountSnap(self.acct_lv, self.pos_mode, self.perm, self.now)

    def fee(self, inst_id: str) -> FeeSnap:
        if self.fee_error:
            raise PilotTransportError("fee missing")
        return FeeSnap(self.taker, "Lv1", "g1", self.now, self.now)

    def leverage(self, inst_id: str) -> tuple[LeverSnap, ...]:
        if self.pos_mode == "long_short_mode":
            side = "short" if inst_id.startswith("QQQ") else "long"
        else:
            side = "net"
        return (LeverSnap(side, self.lever, "isolated"),)

    def tiers(self, inst_id: str) -> tuple[TierSnap, ...]:
        del inst_id
        if self.tier_error:
            raise PilotTransportError("tier missing")
        return (TierSnap(Decimal("0"), Decimal("1000"), self.mmr, Decimal("50"), self.now),)

    def instrument(self, inst_id: str) -> InstrumentSnap:
        del inst_id
        return InstrumentSnap(
            Decimal("1"), "1", "linear", "USDT", "live", Decimal("0.1"), Decimal("0.01"), Decimal("0.01"), "g1", self.now
        )

    def mark(self, inst_id: str) -> MarkSnap:
        return MarkSnap(self.marks[inst_id], self.mark_ts[inst_id], self.now)

    def positions(self) -> tuple[PositionSnap, ...]:
        if self.hide_positions:
            raise PilotTransportError("positions")
        rows = []
        for inst, row in self.state.items():
            if row["pos"] == 0:
                continue
            rows.append(
                PositionSnap(
                    inst, "", row["pos"], row["avg"], row["upl"], row["liq"], "isolated", self.now
                )
            )
        return tuple(rows)

    def pending(self) -> tuple[OrderSnap, ...]:
        if self.hide_pending:
            raise PilotTransportError("pending")
        live = [row for row in self.orders.values() if row.state in {"live", "partially_filled"}]
        return tuple(live + self.foreign)

    def order(self, inst_id: str, cl_ord_id: str) -> OrderSnap:
        if cl_ord_id in self.lookup_fail:
            raise PilotTransportError("lookup")
        row = self.orders.get(cl_ord_id)
        if row is None or row.inst_id != inst_id:
            raise PilotNotFound(cl_ord_id)
        return row

    def bills(self, inst_id: str, begin_ms: int) -> tuple[BillSnap, ...]:
        del begin_ms
        if self.hide_bills:
            raise PilotTransportError("bills")
        return tuple(row for row in self.ledger if row.inst_id == inst_id)

    def place(self, request: OrderRequest) -> None:
        self.placed.append(request)
        if request.intent == "entry":
            if Decimal(request.sz) != PILOT_LEG[request.inst_id].size or request.reduce_only:
                raise AssertionError("entry left the selected size")
            if request.inst_id in self.fail_entry:
                raise PilotRejected("entry refused")
            if request.inst_id in self.partial:
                self._fill(request, self.partial[request.inst_id], "canceled")
                return
            if self.rest_on_place:
                self._store(request, Decimal("0"), "live")
                return
            self._fill(request, Decimal(request.sz), "filled")
            return
        if request.reduce_only is not True:
            raise AssertionError("close is not reduce-only")
        pos = self.state[request.inst_id]["pos"]
        size = Decimal(request.sz)
        if size > abs(pos) or size > PILOT_LEG[request.inst_id].size:
            raise AssertionError("reduce-only close is larger than the position or the plan")
        if self.close_failures > 0:
            self.close_failures -= 1
            raise PilotTransportError("close failed")
        remaining = abs(pos) - size
        self.state[request.inst_id]["pos"] = Decimal("0") if remaining == 0 else remaining.copy_sign(pos)
        self._store(request, size, "filled")

    def cancel(self, inst_id: str, cl_ord_id: str) -> None:
        self.cancelled.append(cl_ord_id)
        row = self.orders.get(cl_ord_id)
        if row is None:
            return
        if self.fill_on_cancel and row.acc_fill_sz == 0:
            signed = row.sz if row.side == "buy" else -row.sz
            self.state[inst_id]["pos"] = signed
            self.state[inst_id]["avg"] = self.marks[inst_id]
            self.orders[cl_ord_id] = OrderSnap(inst_id, cl_ord_id, row.side, row.sz, row.sz, "filled", self.now)
            return
        self.orders[cl_ord_id] = OrderSnap(inst_id, cl_ord_id, row.side, row.sz, row.acc_fill_sz, "canceled", self.now)

    def _fill(self, request: OrderRequest, size: Decimal, state: str) -> None:
        signed = size if request.side == "buy" else -size
        self.state[request.inst_id]["pos"] = signed
        self.state[request.inst_id]["avg"] = self.marks[request.inst_id]
        self._store(request, size, state)

    def _store(self, request: OrderRequest, filled: Decimal, state: str) -> None:
        self.orders[request.cl_ord_id] = OrderSnap(
            request.inst_id, request.cl_ord_id, request.side, Decimal(request.sz), filled, state, self.now
        )


def _go(tmp_path: Path, book: SyntheticBook | None = None, **overrides: object):
    book = book or SyntheticBook()
    controller = PilotController.create(tmp_path / "audit.json", _policy(**overrides))
    controller.arm(book, NOW)
    entered = controller.enter(book, NOW, send_orders=True)
    return controller, book, entered


def _entries(book: SyntheticBook) -> list[OrderRequest]:
    return [row for row in book.placed if row.intent == "entry"]


def _closes(book: SyntheticBook) -> list[OrderRequest]:
    return [row for row in book.placed if row.intent == "close"]


def test_policy_has_no_silent_defaults_and_exact_liq_identity() -> None:
    problems = policy_problems(PilotPolicy())
    assert any("distance_to_liq_fraction" in item for item in problems)
    assert any("liquidated before" in item for item in problems)
    assert any("15 minutes" in item for item in problems)
    assert any("public tier" in item for item in problems)
    assert LIVE_CLEARANCE is False
    long = project_isolated_liq(
        direction="long",
        avg_px=Decimal("100"),
        pos=Decimal("1"),
        ct_val=Decimal("1"),
        mmr=Decimal("0.01"),
        taker_commission=Decimal("0.001"),
    )
    short = project_isolated_liq(
        direction="short",
        avg_px=Decimal("100"),
        pos=Decimal("1"),
        ct_val=Decimal("1"),
        mmr=Decimal("0.01"),
        taker_commission=Decimal("0.001"),
    )
    assert long == Decimal("50.1") / Decimal("0.989")
    assert short == Decimal("149.9") / Decimal("1.011")
    stop = stop_price(direction="long", entry=Decimal("100"), liq=long, distance_fraction=Decimal("0.25"))
    assert long < stop < Decimal("100")
    with pytest.raises(PnlInputError):
        stop_price(direction="long", entry=Decimal("100"), liq=long, distance_fraction=Decimal("0"))
    assert commission_rate(Decimal("-0.001")) == Decimal("0.001")
    assert commission_rate(Decimal("0.001")) == Decimal("0")


def test_caps_boundary_and_loss_trigger_include_fee_and_funding_sign() -> None:
    gross, margin, _net = portfolio_amounts(tuple((inst, px, Decimal("1")) for inst, px in MARKS.items()))
    assert gross == Decimal("19.5")
    assert margin == Decimal("9.75")
    assert cap_problems(gross, margin) == ()
    boundary = (
        ("MU-USDT-SWAP", Decimal("100"), Decimal("1")),
        ("LITE-USDT-SWAP", Decimal("100"), Decimal("1")),
        ("COHR-USDT-SWAP", Decimal("100"), Decimal("1")),
        ("QQQ-USDT-SWAP", Decimal("2600"), Decimal("1")),
    )
    exact_gross, exact_margin, _ = portfolio_amounts(boundary)
    assert exact_gross == Decimal("30")
    assert exact_margin == Decimal("15")
    assert cap_problems(exact_gross, exact_margin) == ()
    over = portfolio_amounts(
        (
            ("MU-USDT-SWAP", Decimal("100"), Decimal("1")),
            ("LITE-USDT-SWAP", Decimal("100"), Decimal("1")),
            ("COHR-USDT-SWAP", Decimal("100"), Decimal("1")),
            ("QQQ-USDT-SWAP", Decimal("2600.02"), Decimal("1")),
        )
    )
    assert cap_problems(over[0], over[1])

    def bill(bill_id: str, bill_type: str, pnl: str, fee: str = "0", ts: int = 10) -> BillInput:
        return BillInput(bill_id, "MU-USDT-SWAP", bill_type, ts, Decimal(pnl), Decimal(fee), "USDT")

    open_loss = net_pnl(unrealized_by_inst={"MU-USDT-SWAP": Decimal("-1")}, bills=(), baseline_ms=0)
    closed = net_pnl(unrealized_by_inst={}, bills=(bill("b1", "2", "-1"),), baseline_ms=0)
    assert open_loss.net == Decimal("-1")
    assert closed.net == Decimal("-1")
    doubled = net_pnl(
        unrealized_by_inst={"MU-USDT-SWAP": Decimal("-1")},
        bills=(bill("b1", "2", "-1"),),
        baseline_ms=0,
    )
    assert doubled.net == Decimal("-2")
    with_fee = net_pnl(
        unrealized_by_inst={},
        bills=(bill("b1", "2", "-6.9", "-0.1"),),
        baseline_ms=0,
    )
    assert with_fee.net == Decimal("-7")
    assert with_fee.trigger_hit is True
    funding_in = net_pnl(unrealized_by_inst={}, bills=(bill("f", "8", "0.4"),), baseline_ms=0)
    funding_out = net_pnl(unrealized_by_inst={}, bills=(bill("f", "8", "-0.4"),), baseline_ms=0)
    assert funding_in.net == Decimal("0.4")
    assert funding_out.net == Decimal("-0.4")
    before = net_pnl(unrealized_by_inst={}, bills=(bill("old", "2", "-9", ts=1),), baseline_ms=5)
    assert before.net == Decimal("0")
    exact = net_pnl(unrealized_by_inst={}, bills=(bill("b", "2", "-7"),), baseline_ms=0)
    assert exact.trigger_hit is True
    just_above = net_pnl(unrealized_by_inst={}, bills=(bill("b", "2", "-6.99"),), baseline_ms=0)
    assert just_above.trigger_hit is False
    with pytest.raises(PnlInputError):
        net_pnl(unrealized_by_inst={}, bills=(bill("x", "7", "-1"),), baseline_ms=0)
    with pytest.raises(PnlInputError):
        net_pnl(unrealized_by_inst={}, bills=(bill("x", "8", "-1", "-0.01"),), baseline_ms=0)
    assert NET_LOSS_TRIGGER_USDT == Decimal("7")


def test_each_leg_can_be_liquidated_before_the_portfolio_trigger(tmp_path: Path) -> None:
    for inst, mark in MARKS.items():
        leg = PILOT_LEG[inst]
        _liq, _stop, loss = projection_ready(
            direction=leg.direction,
            mark=mark,
            pos=leg.size,
            ct_val=Decimal("1"),
            mmr=Decimal("0.01"),
            taker_signed=Decimal("-0.001"),
            distance_fraction=Decimal("0.25"),
        )
        assert loss < Decimal("7")
    with pytest.raises(PilotBlocked):
        PilotController.create(
            tmp_path / "audit.json",
            _policy(acknowledge_leg_can_liquidate_before_portfolio_stop=False),
        )


def test_gate_keeps_the_stub_and_refuses_anything_outside_the_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OKX_PILOT_PLAN", "1")
    transport = FakeTransport()
    ignored, _clock = _client(transport, demo=False, allow_orders=True, live_trade=True)
    assert ignored.pilot_plan is False
    with pytest.raises(TypeError, match="bool"):
        _client(FakeTransport(), demo=False, pilot_plan="yes")  # type: ignore[arg-type]
    with pytest.raises(LiveTradingDisabled, match="outside the mild-3.1 stub"):
        ignored.place_order(
            inst_id="COHR-USDT-SWAP",
            side="buy",
            sz="0.02",
            td_mode="isolated",
            ord_type="limit",
            px="280",
            margin_usd=1,
        )
    assert transport.calls == []

    assert_live_place(
        inst_id="LITE-USDT-SWAP",
        side="sell",
        sz="0.01",
        td_mode="isolated",
        ord_type="limit",
        px="300",
        reduce_only=True,
        pilot_plan=False,
    )
    with pytest.raises(LiveTradingDisabled):
        assert_live_place(
            inst_id="LITE-USDT-SWAP",
            side="sell",
            sz="0.02",
            td_mode="isolated",
            ord_type="limit",
            px="300",
            reduce_only=True,
        )
    assert_live_place(
        inst_id="SKHY-USDT-SWAP",
        side="buy",
        sz="0.03",
        td_mode="isolated",
        ord_type="limit",
        px="10",
        pilot_plan=True,
    )

    ack = load_fixture("private_order_ack.json")
    live_transport = FakeTransport()
    live_transport.add("/api/v5/trade/order", ack)
    missing_trade, _clock = _client(live_transport, demo=False, allow_orders=True, live_trade=False, pilot_plan=True)
    with pytest.raises(LiveTradingDisabled):
        missing_trade.place_order(
            inst_id="COHR-USDT-SWAP", side="buy", sz="0.02", td_mode="isolated", ord_type="ioc", px="280", margin_usd=1
        )
    missing_go, _clock = _client(live_transport, demo=False, allow_orders=False, live_trade=True, pilot_plan=True)
    with pytest.raises(OrdersDisabled):
        missing_go.place_order(
            inst_id="COHR-USDT-SWAP", side="buy", sz="0.02", td_mode="isolated", ord_type="ioc", px="280", margin_usd=1
        )
    locked, _clock = _client(
        live_transport, demo=False, allow_orders=True, live_trade=True, pilot_plan=True, read_only=True
    )
    with pytest.raises(OrdersDisabled):
        locked.place_order(
            inst_id="COHR-USDT-SWAP", side="buy", sz="0.02", td_mode="isolated", ord_type="ioc", px="280", margin_usd=1
        )
    assert live_transport.calls == []

    live, _clock = _client(live_transport, demo=False, allow_orders=True, live_trade=True, pilot_plan=True)
    opened = live.place_order(
        inst_id="COHR-USDT-SWAP", side="buy", sz="0.02", td_mode="isolated", ord_type="ioc", px="280", margin_usd=1
    )
    assert opened.ord_id
    body = __import__("json").loads(live_transport.calls[0]["body"])
    assert body["instId"] == "COHR-USDT-SWAP"
    assert body["sz"] == "0.02"
    assert body["side"] == "buy"
    assert "reduceOnly" not in body
    with pytest.raises(LiveTradingDisabled):
        live.place_order(
            inst_id="COHR-USDT-SWAP", side="buy", sz="0.01", td_mode="isolated", ord_type="ioc", px="280", margin_usd=1
        )
    with pytest.raises(LiveTradingDisabled):
        live.place_order(
            inst_id="COHR-USDT-SWAP",
            side="sell",
            sz="0.03",
            td_mode="isolated",
            ord_type="ioc",
            px="280",
            reduce_only=True,
        )
    closed = live.place_order(
        inst_id="COHR-USDT-SWAP",
        side="sell",
        sz="0.02",
        td_mode="isolated",
        ord_type="ioc",
        px="270",
        reduce_only=True,
    )
    assert closed.ord_id
    capped = FakeTransport()
    capped_client, _clock = _client(capped, demo=False, allow_orders=True, live_trade=True, pilot_plan=True)
    with pytest.raises(RiskLimitError, match="optical"):
        capped_client.place_order(
            inst_id="COHR-USDT-SWAP",
            side="buy",
            sz="0.02",
            td_mode="isolated",
            ord_type="ioc",
            px="280",
            margin_usd=6,
            open_margins={"LITE-USDT-SWAP": 6},
        )
    assert capped.calls == []


@pytest.mark.parametrize("inst_id", [leg.inst_id for leg in PILOT_LEGS])
def test_one_leg_entry_failure_unwinds_and_stops(tmp_path: Path, inst_id: str) -> None:
    book = SyntheticBook()
    book.fail_entry.add(inst_id)
    controller, book, entered = _go(tmp_path, book)
    assert entered.live_clearance is False
    assert inst_id in {row.inst_id for row in _entries(book)}
    later = [leg.inst_id for leg in PILOT_LEGS]
    later = later[later.index(inst_id) + 1 :]
    assert not any(row.inst_id in later for row in _entries(book))
    assert all(row.reduce_only for row in _closes(book))
    assert controller.audit.entries_sealed is True
    assert controller.audit.phase in {"flat", "escalated"}
    before = len(book.placed)
    controller.step(book, NOW + 1_000)
    assert len(book.placed) == before


def test_partial_dust_does_not_round_up_or_continue(tmp_path: Path) -> None:
    book = SyntheticBook()
    book.partial["MU-USDT-SWAP"] = Decimal("0.005")
    controller, book, _entered = _go(tmp_path, book)
    assert [row.inst_id for row in _entries(book)] == ["MU-USDT-SWAP"]
    assert _closes(book) == []
    assert controller.audit.phase == "unwinding"
    assert "below the lot" in controller.audit.escalation_reason
    before = len(book.placed)
    book.now = NOW + 15 * 60 * 1000
    book.mark_ts["MU-USDT-SWAP"] = book.now
    controller.step(book, book.now)
    assert len(book.placed) == before
    assert book.state["MU-USDT-SWAP"]["pos"] == Decimal("0.005")


def test_partial_fill_unwinds_without_top_up(tmp_path: Path) -> None:
    book = SyntheticBook()
    book.partial["COHR-USDT-SWAP"] = Decimal("0.01")
    _controller, book, _entered = _go(tmp_path, book)
    assert "QQQ-USDT-SWAP" not in {row.inst_id for row in _entries(book)}
    cohr_closes = [row for row in _closes(book) if row.inst_id == "COHR-USDT-SWAP"]
    assert [row.sz for row in cohr_closes] == ["0.01"]
    assert all(row.reduce_only and row.side == "sell" for row in cohr_closes)


def test_ioc_rest_cancels_then_closes_the_fill_from_the_cancel(tmp_path: Path) -> None:
    book = SyntheticBook()
    book.rest_on_place = True
    book.fill_on_cancel = True
    controller, book, entered = _go(tmp_path, book)
    assert entered.phase == "flat"
    assert len(_entries(book)) == 1
    assert book.cancelled
    assert len(_closes(book)) == 1
    assert _closes(book)[0].reduce_only is True
    assert controller.audit.phase == "flat"


def test_limit_rest_is_cancelled_after_the_allowed_steps(tmp_path: Path) -> None:
    book = SyntheticBook()
    book.rest_on_place = True
    controller = PilotController.create(tmp_path / "audit.json", _policy(entry_ord_type="limit", limit_rest_steps=1))
    controller.arm(book, NOW)
    entered = controller.enter(book, NOW, send_orders=True)
    assert entered.phase == "entering"
    assert len(_entries(book)) == 1
    book.now = NOW + 1_000
    stepped = controller.step(book, NOW + 1_000)
    assert book.cancelled
    assert stepped.phase in {"flat", "escalated"}
    assert len(_entries(book)) == 1


def test_delayed_fill_continues_only_the_unsubmitted_legs(tmp_path: Path) -> None:
    book = SyntheticBook()
    book.rest_on_place = True
    controller = PilotController.create(tmp_path / "audit.json", _policy(entry_ord_type="limit", limit_rest_steps=3))
    controller.arm(book, NOW)
    controller.enter(book, NOW, send_orders=True)
    resting = _entries(book)[0]
    book.rest_on_place = False
    book.state[resting.inst_id]["pos"] = Decimal(resting.sz)
    book.state[resting.inst_id]["avg"] = book.marks[resting.inst_id]
    book.orders[resting.cl_ord_id] = OrderSnap(
        resting.inst_id, resting.cl_ord_id, resting.side, Decimal(resting.sz), Decimal(resting.sz), "filled", NOW
    )
    book.now = NOW + 1_000
    result = controller.step(book, NOW + 1_000)
    assert result.phase == "monitoring"
    assert {row.inst_id for row in _entries(book)} == {leg.inst_id for leg in PILOT_LEGS}
    assert len([row for row in _entries(book) if row.inst_id == "MU-USDT-SWAP"]) == 1


def test_restart_does_not_send_the_submitted_entry_again(tmp_path: Path) -> None:
    book = SyntheticBook()
    book.rest_on_place = True
    path = tmp_path / "audit.json"
    controller = PilotController.create(path, _policy(entry_ord_type="limit", limit_rest_steps=3))
    controller.arm(book, NOW)
    controller.enter(book, NOW, send_orders=True)
    submitted = controller.audit.orders[0].cl_ord_id
    resumed = PilotController.load(path, _policy(entry_ord_type="limit", limit_rest_steps=3))
    assert resumed._resumed is True
    book.now = NOW + 1_000
    result = resumed.step(book, NOW + 1_000)
    assert result.phase in {"flat", "escalated"}
    assert [row.cl_ord_id for row in _entries(book)] == [submitted]
    assert submitted in book.cancelled


def test_portfolio_trigger_and_leg_stop_flatten_every_open_leg(tmp_path: Path) -> None:
    controller, book, entered = _go(tmp_path)
    assert entered.phase == "monitoring"
    for inst in MARKS:
        book.state[inst]["upl"] = Decimal("-1.75")
    book.now = NOW + 1_000
    triggered = controller.step(book, NOW + 1_000)
    assert triggered.phase == "flat"
    assert " -7 " in f" {triggered.reason} " or "-7" in triggered.reason
    assert {row.inst_id for row in _closes(book)} == set(MARKS)
    assert all(row.reduce_only for row in _closes(book))

    controller, book, _entered = _go(tmp_path / "stop")
    _liq, stop, _loss = projection_ready(
        direction="long",
        mark=Decimal("370"),
        pos=Decimal("0.01"),
        ct_val=Decimal("1"),
        mmr=Decimal("0.01"),
        taker_signed=Decimal("-0.001"),
        distance_fraction=Decimal("0.25"),
    )
    book.marks["MU-USDT-SWAP"] = stop
    book.mark_ts["MU-USDT-SWAP"] = NOW + 1_000
    book.now = NOW + 1_000
    stopped = controller.step(book, NOW + 1_000)
    assert stopped.phase == "flat"
    assert "leg stop" in stopped.reason
    assert {row.inst_id for row in _closes(book)} == set(MARKS)


def test_realized_transition_does_not_keep_the_old_upl(tmp_path: Path) -> None:
    controller, book, _entered = _go(tmp_path)
    book.state["MU-USDT-SWAP"]["upl"] = Decimal("-1")
    book.now = NOW + 1_000
    marked = controller.step(book, NOW + 1_000)
    assert marked.phase == "monitoring"
    assert Decimal(marked.net_pnl) == Decimal("-1")
    book.state["MU-USDT-SWAP"]["pos"] = Decimal("0")
    book.ledger.append(BillSnap("trade-1", "MU-USDT-SWAP", "2", NOW + 1_000, Decimal("-1"), Decimal("0"), "USDT"))
    for inst in ("LITE-USDT-SWAP", "COHR-USDT-SWAP", "QQQ-USDT-SWAP"):
        book.state[inst]["pos"] = Decimal("0")
    book.now = NOW + 2_000
    flat = controller.step(book, NOW + 2_000)
    assert flat.phase == "flat"
    assert Decimal(flat.net_pnl) == Decimal("-1")


def test_unknown_pending_blocks_arm_and_escalates_without_cancel(tmp_path: Path) -> None:
    book = SyntheticBook()
    book.foreign.append(OrderSnap("MU-USDT-SWAP", "EXT123", "buy", Decimal("0.01"), Decimal("0"), "live", NOW))
    controller = PilotController.create(tmp_path / "audit.json", _policy())
    with pytest.raises(PilotBlocked, match="pending"):
        controller.arm(book, NOW)
    assert book.placed == []
    assert book.cancelled == []

    clean = SyntheticBook()
    controller, clean, _entered = _go(tmp_path / "watch")
    clean.foreign.append(OrderSnap("QQQ-USDT-SWAP", "EXT999", "sell", Decimal("0.01"), Decimal("0"), "live", NOW))
    clean.now = NOW + 1_000
    stepped = controller.step(clean, NOW + 1_000)
    assert stepped.phase == "monitoring"
    assert "EXT999" not in clean.cancelled
    assert _closes(clean) == []
    assert "unknown live order" in stepped.reason


def test_missing_inputs_and_bad_marks_do_not_arm_or_invent_a_price(tmp_path: Path) -> None:
    book = SyntheticBook()
    book.tier_error = True
    controller = PilotController.create(tmp_path / "tier.json", _policy())
    with pytest.raises(PilotBlocked):
        controller.arm(book, NOW)
    assert book.placed == []

    book = SyntheticBook()
    book.fee_error = True
    controller = PilotController.create(tmp_path / "fee.json", _policy())
    with pytest.raises(PilotBlocked):
        controller.arm(book, NOW)

    book = SyntheticBook()
    book.lever = "5"
    controller = PilotController.create(tmp_path / "lever.json", _policy())
    with pytest.raises(PilotBlocked, match="leverage"):
        controller.arm(book, NOW)

    book = SyntheticBook()
    book.acct_lv = "3"
    controller = PilotController.create(tmp_path / "mode.json", _policy())
    with pytest.raises(PilotBlocked, match="acctLv"):
        controller.arm(book, NOW)

    book = SyntheticBook()
    for inst in MARKS:
        book.marks[inst] = Decimal("10000")
    controller = PilotController.create(tmp_path / "cap.json", _policy())
    with pytest.raises(PilotBlocked, match="cap"):
        controller.arm(book, NOW)
    assert book.placed == []

    controller, book, _entered = _go(tmp_path / "stale")
    book.mark_ts["MU-USDT-SWAP"] = NOW - 20_000
    book.now = NOW + 1_000
    stale = controller.step(book, NOW + 1_000)
    assert stale.phase == "monitoring"
    assert _closes(book) == []
    assert "no close price was sent" in stale.reason

    controller, book, _entered = _go(tmp_path / "zero")
    book.marks["MU-USDT-SWAP"] = Decimal("0")
    book.mark_ts["MU-USDT-SWAP"] = NOW + 1_000
    book.now = NOW + 1_000
    controller.step(book, NOW + 1_000)
    assert not any(row.inst_id == "MU-USDT-SWAP" for row in _closes(book))


def test_close_failures_persist_and_then_send_nothing(tmp_path: Path) -> None:
    book = SyntheticBook()
    book.close_failures = 50
    path = tmp_path / "audit.json"
    controller, book, _entered = _go(path.parent, book, max_close_attempts=2)
    for inst in MARKS:
        book.state[inst]["upl"] = Decimal("-2")
    book.now = NOW + 1_000
    failed = controller.step(book, NOW + 1_000)
    assert failed.phase == "escalated"
    assert controller.audit.close_attempts == 2
    assert len(_closes(book)) == 8
    before = len(book.placed)
    loaded = PilotController.load(path, _policy(max_close_attempts=2))
    loaded.step(book, NOW + 2_000)
    assert len(book.placed) == before


def _fresh_marks(book: SyntheticBook, now_ms: int) -> None:
    book.now = now_ms
    for inst in MARKS:
        book.mark_ts[inst] = now_ms


def test_oversized_leg_does_not_block_the_other_closes(tmp_path: Path) -> None:
    path = tmp_path / "audit.json"
    controller, book, _entered = _go(tmp_path)
    book.state["MU-USDT-SWAP"]["pos"] = Decimal("0.05")
    book.state["MU-USDT-SWAP"]["upl"] = Decimal("-7")
    _fresh_marks(book, NOW + 1_000)
    result = controller.step(book, book.now)
    assert "larger than the plan" in result.reason
    assert "MU-USDT-SWAP" in result.reason
    assert not any(row.inst_id == "MU-USDT-SWAP" for row in _closes(book))
    assert {row.inst_id for row in _closes(book)} == {"LITE-USDT-SWAP", "COHR-USDT-SWAP", "QQQ-USDT-SWAP"}
    assert [(row.inst_id, row.side, row.sz, row.reduce_only) for row in _closes(book)] == [
        ("LITE-USDT-SWAP", "sell", "0.01", True),
        ("COHR-USDT-SWAP", "sell", "0.02", True),
        ("QQQ-USDT-SWAP", "buy", "0.01", True),
    ]
    assert book.state["MU-USDT-SWAP"]["pos"] == Decimal("0.05")
    assert "larger than the plan" in path.read_text(encoding="utf-8")
    before = len(book.placed)
    _fresh_marks(book, NOW + 15 * 60 * 1000)
    again = controller.step(book, book.now)
    assert len(book.placed) == before
    assert "larger than the plan" in again.reason
    loaded = PilotController.load(path, _policy())
    loaded.step(book, book.now)
    assert len(book.placed) == before
    assert book.state["MU-USDT-SWAP"]["pos"] == Decimal("0.05")
    assert "MU-USDT-SWAP" in loaded.audit.escalation_reason


def test_dust_leg_does_not_block_the_other_closes(tmp_path: Path) -> None:
    controller, book, _entered = _go(tmp_path)
    book.state["MU-USDT-SWAP"]["pos"] = Decimal("0.005")
    book.state["MU-USDT-SWAP"]["upl"] = Decimal("-7")
    _fresh_marks(book, NOW + 1_000)
    result = controller.step(book, book.now)
    assert "below the lot" in result.reason
    assert not any(row.inst_id == "MU-USDT-SWAP" for row in _closes(book))
    assert [(row.inst_id, row.side, row.sz, row.reduce_only) for row in _closes(book)] == [
        ("LITE-USDT-SWAP", "sell", "0.01", True),
        ("COHR-USDT-SWAP", "sell", "0.02", True),
        ("QQQ-USDT-SWAP", "buy", "0.01", True),
    ]
    assert book.state["MU-USDT-SWAP"]["pos"] == Decimal("0.005")
    before = len(book.placed)
    _fresh_marks(book, NOW + 15 * 60 * 1000)
    controller.step(book, book.now)
    assert len(book.placed) == before


def test_stale_quote_does_not_block_fresh_legs(tmp_path: Path) -> None:
    path = tmp_path / "audit.json"
    controller, book, _entered = _go(tmp_path)
    for inst in MARKS:
        book.state[inst]["upl"] = Decimal("-1.75")
    _fresh_marks(book, NOW + 1_000)
    book.mark_ts["MU-USDT-SWAP"] = NOW - 20_000
    result = controller.step(book, book.now)
    assert "no close price was sent" in result.reason
    assert not any(row.inst_id == "MU-USDT-SWAP" for row in _closes(book))
    assert [(row.inst_id, row.side, row.sz, row.reduce_only) for row in _closes(book)] == [
        ("LITE-USDT-SWAP", "sell", "0.01", True),
        ("COHR-USDT-SWAP", "sell", "0.02", True),
        ("QQQ-USDT-SWAP", "buy", "0.01", True),
    ]
    assert book.state["MU-USDT-SWAP"]["pos"] != 0
    assert "MU-USDT-SWAP" in path.read_text(encoding="utf-8")
    _fresh_marks(book, NOW + 15 * 60 * 1000)
    loaded = PilotController.load(path, _policy())
    loaded.step(book, book.now)
    mu = [row for row in _closes(book) if row.inst_id == "MU-USDT-SWAP"]
    assert len(mu) == 1
    assert mu[0].side == "sell" and mu[0].sz == "0.01" and mu[0].reduce_only is True
    assert [row.inst_id for row in _closes(book) if row.inst_id == "LITE-USDT-SWAP"] == ["LITE-USDT-SWAP"]
    assert book.state["MU-USDT-SWAP"]["pos"] == 0


def test_unknown_order_does_not_block_other_closes_or_get_duplicated(tmp_path: Path) -> None:
    path = tmp_path / "audit.json"
    controller, book, _entered = _go(tmp_path)
    for inst in MARKS:
        book.state[inst]["upl"] = Decimal("-1.75")
    book.foreign.append(OrderSnap("QQQ-USDT-SWAP", "EXT999", "sell", Decimal("0.01"), Decimal("0"), "live", NOW))
    _fresh_marks(book, NOW + 1_000)
    result = controller.step(book, book.now)
    assert "unknown live order" in result.reason
    assert "EXT999" not in book.cancelled
    assert not any(row.inst_id == "QQQ-USDT-SWAP" for row in _closes(book))
    assert [(row.inst_id, row.side, row.sz, row.reduce_only) for row in _closes(book)] == [
        ("MU-USDT-SWAP", "sell", "0.01", True),
        ("LITE-USDT-SWAP", "sell", "0.01", True),
        ("COHR-USDT-SWAP", "sell", "0.02", True),
    ]
    assert book.state["QQQ-USDT-SWAP"]["pos"] != 0
    assert "QQQ-USDT-SWAP" in path.read_text(encoding="utf-8")
    book.foreign.clear()
    _fresh_marks(book, NOW + 15 * 60 * 1000)
    loaded = PilotController.load(path, _policy())
    loaded.step(book, book.now)
    qqq = [row for row in _closes(book) if row.inst_id == "QQQ-USDT-SWAP"]
    assert len(qqq) == 1
    assert qqq[0].side == "buy" and qqq[0].sz == "0.01" and qqq[0].reduce_only is True
    assert [row.inst_id for row in _closes(book) if row.inst_id == "MU-USDT-SWAP"] == ["MU-USDT-SWAP"]
    assert "EXT999" not in book.cancelled
    assert book.state["QQQ-USDT-SWAP"]["pos"] == 0


def test_next_check_and_restart_finish_valid_legs_without_a_duplicate(tmp_path: Path) -> None:
    path = tmp_path / "audit.json"
    controller, book, _entered = _go(tmp_path)
    book.state["MU-USDT-SWAP"]["pos"] = Decimal("0.05")
    book.state["MU-USDT-SWAP"]["upl"] = Decimal("-7")
    book.close_failures = 1
    _fresh_marks(book, NOW + 1_000)
    first = controller.step(book, book.now)
    assert "larger than the plan" in first.reason
    assert [row.inst_id for row in _closes(book)] == [
        "LITE-USDT-SWAP",
        "COHR-USDT-SWAP",
        "QQQ-USDT-SWAP",
    ]
    assert book.state["LITE-USDT-SWAP"]["pos"] != 0
    assert book.state["COHR-USDT-SWAP"]["pos"] == 0
    assert book.state["QQQ-USDT-SWAP"]["pos"] == 0
    assert controller.audit.phase == "unwinding"
    _fresh_marks(book, NOW + 15 * 60 * 1000)
    loaded = PilotController.load(path, _policy())
    second = loaded.step(book, book.now)
    lite = [row for row in _closes(book) if row.inst_id == "LITE-USDT-SWAP"]
    assert len(lite) == 2
    assert all(row.side == "sell" and row.sz == "0.01" and row.reduce_only for row in lite)
    assert [row.inst_id for row in _closes(book) if row.inst_id == "COHR-USDT-SWAP"] == ["COHR-USDT-SWAP"]
    assert [row.inst_id for row in _closes(book) if row.inst_id == "QQQ-USDT-SWAP"] == ["QQQ-USDT-SWAP"]
    assert not any(row.inst_id == "MU-USDT-SWAP" for row in _closes(book))
    assert book.state["LITE-USDT-SWAP"]["pos"] == 0
    assert book.state["MU-USDT-SWAP"]["pos"] == Decimal("0.05")
    assert "larger than the plan" in second.reason
    before = len(book.placed)
    _fresh_marks(book, book.now + 15 * 60 * 1000)
    PilotController.load(path, _policy()).step(book, book.now)
    assert len(book.placed) == before


def test_enter_without_send_orders_places_nothing(tmp_path: Path) -> None:
    book = SyntheticBook()
    controller = PilotController.create(tmp_path / "audit.json", _policy())
    controller.arm(book, NOW)
    with pytest.raises(PilotBlocked, match="send_orders"):
        controller.enter(book, NOW)
    with pytest.raises(PilotBlocked, match="send_orders"):
        controller.enter(book, NOW, send_orders=False)
    assert book.placed == []
    assert controller.audit.phase == "armed"


def test_read_permission_can_arm_but_cannot_enter(tmp_path: Path) -> None:
    book = SyntheticBook()
    book.perm = "read_only"
    controller = PilotController.create(tmp_path / "audit.json", _policy())
    armed = controller.arm(book, NOW)
    assert armed.phase == "armed"
    assert book.placed == []
    with pytest.raises(PilotBlocked, match="trade"):
        controller.enter(book, NOW, send_orders=True)
    assert book.placed == []


def test_audit_stores_recovery_not_policy_or_secrets(tmp_path: Path) -> None:
    path = tmp_path / "audit.json"
    policy = _policy()
    controller = PilotController.create(path, policy)
    text = path.read_text(encoding="utf-8")
    assert "distance_to_liq_fraction" not in text
    assert "0.25" not in text
    assert "test-key" not in text
    assert "2026-11-19-mu-lite-cohr-qqq" in text
    with pytest.raises(AuditError):
        PilotController.create(path, policy)
    with pytest.raises(AuditError, match="fingerprint"):
        load_audit(path, policy_fingerprint="different")
    controller.arm(SyntheticBook(), NOW)
    assert controller.audit.baseline_ms == NOW


def test_fifteen_minute_gap_does_not_flatten_by_itself(tmp_path: Path) -> None:
    controller, book, _entered = _go(tmp_path)
    book.now = NOW + 15 * 60 * 1000
    for inst in MARKS:
        book.mark_ts[inst] = book.now
    waited = controller.step(book, book.now)
    assert waited.phase == "monitoring"
    assert _closes(book) == []
    for inst in MARKS:
        book.state[inst]["upl"] = Decimal("-1.75")
    book.now += 15 * 60 * 1000
    for inst in MARKS:
        book.mark_ts[inst] = book.now
    triggered = controller.step(book, book.now)
    assert triggered.phase == "flat"
    assert "-7" in triggered.reason


def test_scenarios_do_not_open_sockets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("socket opened")

    monkeypatch.setattr("socket.socket", _boom)
    test_portfolio_trigger_and_leg_stop_flatten_every_open_leg(tmp_path)


def test_result_cannot_claim_live_clearance() -> None:
    with pytest.raises(PilotBlocked):
        PilotResult("armed", "arm", None, "", (), True)


def test_sanctioned_reads_do_not_need_trade_or_the_demo_header() -> None:
    transport = FakeTransport()
    transport.add("/api/v5/account/config", {"code": "0", "data": [{
        "acctLv": "2", "posMode": "net_mode", "perm": "read_only,trade", "level": "Lv1",
        "uid": "999", "label": "secret", "ip": "203.0.113.5",
    }]})
    transport.add("/api/v5/account/trade-fee", {"code": "0", "data": [{
        "instType": "SWAP", "level": "Lv1", "ts": str(NOW), "maker": "-0.0002", "taker": "-0.0005",
        "feeGroup": [{"groupId": "g1", "maker": "-0.0002", "taker": "-0.0005"}],
    }]})
    transport.add("/api/v5/account/leverage-info", {"code": "0", "data": [{
        "instId": "MU-USDT-SWAP", "mgnMode": "isolated", "posSide": "net", "lever": "2",
    }]})
    transport.add("/api/v5/account/bills", {"code": "0", "data": [{
        "billId": "1", "instId": "MU-USDT-SWAP", "type": "8", "subType": "173",
        "ts": str(NOW), "pnl": "-0.2", "fee": "0", "ccy": "USDT",
    }]})
    transport.add("/api/v5/trade/orders-pending", {"code": "0", "data": []})
    transport.add("/api/v5/trade/order", load_fixture("private_order.json"))
    transport.add("/api/v5/trade/orders-history", {"code": "0", "data": []})
    transport.add("/api/v5/account/positions", {"code": "0", "data": [{
        "instId": "MU-USDT-SWAP", "instType": "SWAP", "posSide": "net", "pos": "0.01",
        "avgPx": "370", "upl": "-1", "margin": "1.85", "mgnMode": "isolated", "lever": "2",
        "uTime": str(NOW), "liqPx": "", "markPx": "370", "fee": "-0.01", "fundingFee": "0",
        "realizedPnl": "0",
    }]})
    client, _clock = _client(transport, demo=False, allow_orders=False, live_trade=False, pilot_plan=False)
    config = client.account_config()
    assert config.acct_lv == "2"
    assert not hasattr(config, "uid")
    fee = client.trade_fee("SWAP", inst_family="MU-USDT")
    assert fee.groups[0].taker == "-0.0005"
    assert client.leverage_info("MU-USDT-SWAP")[0].lever == "2"
    bill = client.bills(inst_id="MU-USDT-SWAP", begin=str(NOW))[0]
    assert bill.pnl == "-0.2"
    assert bill.fee == "0"
    assert client.open_orders(inst_type="SWAP") == []
    order = client.get_order("BTC-USDT-SWAP", ord_id="312269865356374016")
    assert order.state == "live"
    assert client.order_history("SWAP", inst_id="MU-USDT-SWAP") == []
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
        "instId": "MU-USDT-SWAP", "instFamily": "MU-USDT", "uly": "MU-USDT", "tier": "1",
        "minSz": "0", "maxSz": "100", "mmr": "0.004", "imr": "0.01", "maxLever": "50",
    }]})
    tiers = make_client(public).position_tiers("SWAP", td_mode="isolated", inst_family="MU-USDT")
    assert tiers[0].mmr == "0.004"
    assert public.calls[0]["method"] == "GET"
    assert public.calls[0]["params"]["instFamily"] == "MU-USDT"
    assert "instId" not in public.calls[0]["params"]
    with pytest.raises(ValueError, match="instFamily"):
        make_client(public).position_tiers("SWAP", td_mode="isolated", inst_id="MU-USDT-SWAP")


def test_okx_book_read_does_not_post_or_enable_trade() -> None:
    transport = FakeTransport()
    transport.add("/api/v5/account/config", {"code": "0", "data": [{
        "acctLv": "2", "posMode": "net_mode", "perm": "read_only", "level": "Lv1",
    }]})
    transport.add("/api/v5/account/positions", {"code": "0", "data": []})
    client, _clock = _client(transport, demo=False, allow_orders=False, live_trade=False, pilot_plan=True)
    public = FakeTransport()
    book = OkxPilotBook(client, make_client(public), now_ms=NOW)
    assert book.account().acct_lv == "2"
    request = OrderRequest(
        "COHR-USDT-SWAP", "buy", "0.02", "280", "ioc", False, "", "APL60650001", "1", "entry"
    )
    with pytest.raises(PilotRejected):
        book.place(request)
    assert all(call["method"] == "GET" for call in transport.calls)
    assert client.live_trade is False


def test_runbook_names_the_sanctioned_reads_and_refuses_clearance() -> None:
    text = Path("docs/pilot-risk-plan-runbook.md").read_text(encoding="utf-8")
    for row in SANCTIONED_LIVE_READS:
        assert row.path in text
    assert "not live clearance" in text
    assert "15 minutes" in text
    assert "d7fa452" in text
    assert "3394944" in text
    assert "74ecb90" in text
    assert "24.5554" in text


class _StepClock:
    def __init__(self) -> None:
        self.ms = NOW

    def __call__(self) -> int:
        return self.ms


class _SequencedTransport:
    """One step per request. Venue timestamps are the clock at that response."""

    def __init__(self, clock: _StepClock, step_ms: int, *, stale_mark: str = "") -> None:
        self.clock = clock
        self.step_ms = step_ms
        self.stale_mark = stale_mark
        self.calls: list[dict[str, str]] = []

    def request(self, method, url, *, params, headers, timeout, body=None):
        del headers, timeout, body
        self.clock.ms += self.step_ms
        parsed = urlparse(url)
        query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
        merged = dict(query)
        merged.update({key: str(value) for key, value in (params or {}).items()})
        self.calls.append({"method": method, "path": parsed.path, **merged})
        if method != "GET":
            raise AssertionError(method)
        if parsed.path == "/api/v5/public/position-tiers" and merged.get("instId") and not merged.get("instFamily"):
            body = json.dumps({"code": "50015", "msg": "Parameter instFamily or uly can not be empty", "data": []}).encode()
            return HttpResponse(400, {"Content-Type": "application/json"}, body, url)
        payload = {"code": "0", "data": _live_shape(parsed.path, merged, self.clock.ms, self.stale_mark)}
        return HttpResponse(200, {"Content-Type": "application/json"}, json.dumps(payload).encode(), url)


def _live_shape(path: str, query: dict[str, str], now_ms: int, stale_mark: str) -> list[dict[str, str]]:
    names = ("MU-USDT-SWAP", "LITE-USDT-SWAP", "COHR-USDT-SWAP", "QQQ-USDT-SWAP")
    marks = {"MU-USDT-SWAP": "370", "LITE-USDT-SWAP": "350", "COHR-USDT-SWAP": "280", "QQQ-USDT-SWAP": "670"}
    if path == "/api/v5/account/config":
        return [{"acctLv": "2", "posMode": "net_mode", "perm": "read_only", "level": "Lv1"}]
    if path == "/api/v5/account/positions":
        return []
    if path == "/api/v5/trade/orders-pending":
        return []
    if path == "/api/v5/account/leverage-info":
        return [{"instId": query.get("instId", ""), "mgnMode": "isolated", "posSide": "net", "lever": "2"}]
    if path == "/api/v5/public/instruments":
        return [_instrument(inst) for inst in names]
    if path == "/api/v5/public/mark-price":
        rows = []
        for inst in names:
            ts = now_ms - 6_000 if inst == stale_mark else now_ms
            rows.append({"instType": "SWAP", "instId": inst, "markPx": marks[inst], "ts": str(ts)})
        return rows
    if path == "/api/v5/account/trade-fee":
        return [{
            "instType": "SWAP",
            "level": "Lv1",
            "ts": str(now_ms),
            "maker": "-0.0002",
            "taker": "-0.0005",
            "feeGroup": [{"groupId": "g1", "maker": "-0.0002", "taker": "-0.0005"}],
        }]
    if path == "/api/v5/public/position-tiers":
        families = query.get("instFamily", "").split(",")
        return [
            {
                "instId": "",
                "instFamily": family,
                "uly": family,
                "tier": "1",
                "minSz": "0",
                "maxSz": "1000",
                "mmr": "0.01",
                "imr": "0.02",
                "maxLever": "50",
            }
            for family in families
            if family
        ]
    raise AssertionError(path)


def _instrument(inst_id: str) -> dict[str, str]:
    family = inst_id.removesuffix("-SWAP")
    return {
        "instType": "SWAP",
        "instId": inst_id,
        "instFamily": family,
        "baseCcy": family.split("-")[0],
        "quoteCcy": "USDT",
        "settleCcy": "USDT",
        "state": "live",
        "tickSz": "0.1",
        "lotSz": "0.01",
        "minSz": "0.01",
        "ctVal": "1",
        "ctMult": "1",
        "ctType": "linear",
        "groupId": "g1",
    }


def _wired_book(step_ms: int, *, stale_mark: str = "") -> tuple[PilotController, OkxPilotBook, _SequencedTransport, Path]:
    clock = _StepClock()
    transport = _SequencedTransport(clock, step_ms, stale_mark=stale_mark)
    limiter = RateLimiter(clock=FakeClock(), sleep=lambda _seconds: None)
    private, _ = _client(
        transport,
        demo=False,
        allow_orders=False,
        read_only=True,
        live_trade=False,
        pilot_plan=True,
        limiter=limiter,
    )
    public = make_client(transport, clock=FakeClock(), limiter=limiter)
    book = OkxPilotBook(private, public, now_ms=NOW, clock_ms=clock)
    return book, transport


def test_family_tiers_and_sequential_reads_pass_a_real_5s_check(tmp_path: Path) -> None:
    book, transport = _wired_book(650)
    controller = PilotController.create(tmp_path / "audit.json", _policy())
    armed = controller.arm(book, NOW)
    assert armed.phase == "armed"
    assert armed.blockers == ()
    tiers = [call for call in transport.calls if call["path"] == "/api/v5/public/position-tiers"]
    assert len(tiers) == 1
    assert tiers[0]["instFamily"] == "MU-USDT,LITE-USDT,COHR-USDT,QQQ-USDT"
    assert "instId" not in tiers[0]
    assert all(call["method"] == "GET" for call in transport.calls)
    fees = [call for call in transport.calls if call["path"] == "/api/v5/account/trade-fee"]
    assert [call["instFamily"] for call in fees] == ["MU-USDT", "LITE-USDT", "COHR-USDT", "QQQ-USDT"]


def test_a_slow_snapshot_still_rejects_old_receipts(tmp_path: Path) -> None:
    book, transport = _wired_book(900)
    controller = PilotController.create(tmp_path / "audit.json", _policy())
    with pytest.raises(PilotBlocked, match="stale") as raised:
        controller.arm(book, NOW)
    assert "stale or bad mark" in str(raised.value)
    assert all(call["method"] == "GET" for call in transport.calls)


def test_one_old_venue_timestamp_stays_stale(tmp_path: Path) -> None:
    book, transport = _wired_book(100, stale_mark="MU-USDT-SWAP")
    controller = PilotController.create(tmp_path / "audit.json", _policy())
    with pytest.raises(PilotBlocked, match="stale or bad mark:MU-USDT-SWAP") as raised:
        controller.arm(book, NOW)
    text = str(raised.value)
    assert "stale or bad mark:LITE-USDT-SWAP" not in text
    assert "stale fee" not in text
    assert all(call["method"] == "GET" for call in transport.calls)


def test_summed_receipt_and_venue_delay_exceeds_the_5s_limit() -> None:
    """Receipt delay 5s plus venue-to-receipt 5s is 10s at the decision."""
    assert _fresh(10_000, 5_000, 15_000, 5_000) is False


def test_fresh_accepts_exact_5s_and_rejects_one_ms_or_a_future_stamp() -> None:
    assert _fresh(10_000, 10_000, 15_000, 5_000) is True
    assert _fresh(12_500, 10_000, 15_000, 5_000) is True
    assert _fresh(15_000, 10_000, 15_000, 5_000) is True
    assert _fresh(10_000, 9_999, 15_000, 5_000) is False
    assert _fresh(9_999, 10_000, 15_000, 5_000) is False
    assert _fresh(12_000, 14_000, 15_000, 5_000) is True
    assert _fresh(15_000, 15_001, 15_000, 5_000) is False
    assert _fresh(15_001, 15_000, 15_000, 5_000) is False
    assert _fresh(15_000, 0, 15_000, 5_000) is False
    assert _fresh(15_000, None, 15_000, 5_000) is True
    assert _fresh(9_999, None, 15_000, 5_000) is False
