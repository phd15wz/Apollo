"""Offline accounting checks. Figures are synthetic and caller-supplied."""

from __future__ import annotations

from decimal import Decimal

import pytest

from tools.okx.account_ledger import (
    BillInput,
    LedgerInputError,
    cap_problems,
    commission_rate,
    gross_and_margin,
    net_pnl,
    project_isolated_liq,
    stop_price,
)

INST = frozenset({"ABC-USDT-SWAP"})
# The leverage below is an argument. This module has no default.
LEVER = Decimal("2")


def _bill(bill_id: str, bill_type: str, pnl: str, fee: str = "0", ts: int = 10) -> BillInput:
    return BillInput(bill_id, "ABC-USDT-SWAP", bill_type, ts, Decimal(pnl), Decimal(fee), "USDT")


def test_isolated_projection_uses_the_supplied_leverage() -> None:
    long = project_isolated_liq(
        direction="long",
        avg_px=Decimal("100"),
        pos=Decimal("1"),
        ct_val=Decimal("1"),
        mmr=Decimal("0.01"),
        taker_commission=Decimal("0.001"),
        leverage=LEVER,
    )
    short = project_isolated_liq(
        direction="short",
        avg_px=Decimal("100"),
        pos=Decimal("1"),
        ct_val=Decimal("1"),
        mmr=Decimal("0.01"),
        taker_commission=Decimal("0.001"),
        leverage=LEVER,
    )
    assert long == Decimal("50.1") / Decimal("0.989")
    assert short == Decimal("149.9") / Decimal("1.011")
    stop = stop_price(direction="long", entry=Decimal("100"), liq=long, distance_fraction=Decimal("0.2"))
    assert long < stop < Decimal("100")
    with pytest.raises(LedgerInputError):
        stop_price(direction="long", entry=Decimal("100"), liq=long, distance_fraction=Decimal("0"))
    with pytest.raises(TypeError):
        project_isolated_liq(
            direction="long",
            avg_px=Decimal("100"),
            pos=Decimal("1"),
            ct_val=Decimal("1"),
            mmr=Decimal("0.01"),
            taker_commission=Decimal("0.001"),
        )  # type: ignore[call-arg]
    assert commission_rate(Decimal("-0.001")) == Decimal("0.001")
    assert commission_rate(Decimal("0.001")) == Decimal("0")


def test_pnl_respects_the_caller_trigger_fee_sign_and_baseline() -> None:
    open_loss = net_pnl(
        inst_ids=INST,
        unrealized_by_inst={"ABC-USDT-SWAP": Decimal("-1")},
        bills=(),
        baseline_ms=0,
        loss_trigger=Decimal("4"),
    )
    closed = net_pnl(
        inst_ids=INST,
        unrealized_by_inst={},
        bills=(_bill("b1", "2", "-1"),),
        baseline_ms=0,
        loss_trigger=Decimal("4"),
    )
    assert open_loss.net == closed.net == Decimal("-1")
    assert open_loss.trigger_hit is False
    both = net_pnl(
        inst_ids=INST,
        unrealized_by_inst={"ABC-USDT-SWAP": Decimal("-1")},
        bills=(_bill("b1", "2", "-1"),),
        baseline_ms=0,
        loss_trigger=Decimal("4"),
    )
    assert both.net == Decimal("-2")
    with_fee = net_pnl(
        inst_ids=INST,
        unrealized_by_inst={},
        bills=(_bill("b1", "2", "-3.9", "-0.1"),),
        baseline_ms=0,
        loss_trigger=Decimal("4"),
    )
    assert with_fee.net == Decimal("-4")
    assert with_fee.trigger_hit is True
    funding_in = net_pnl(
        inst_ids=INST, unrealized_by_inst={}, bills=(_bill("f", "8", "0.4"),), baseline_ms=0, loss_trigger=Decimal("4")
    )
    funding_out = net_pnl(
        inst_ids=INST, unrealized_by_inst={}, bills=(_bill("f", "8", "-0.4"),), baseline_ms=0, loss_trigger=Decimal("4")
    )
    assert funding_in.net == Decimal("0.4")
    assert funding_out.net == Decimal("-0.4")
    before = net_pnl(
        inst_ids=INST, unrealized_by_inst={}, bills=(_bill("old", "2", "-9", ts=1),), baseline_ms=5, loss_trigger=Decimal("4")
    )
    assert before.net == Decimal("0")
    with pytest.raises(LedgerInputError):
        net_pnl(inst_ids=INST, unrealized_by_inst={}, bills=(_bill("x", "7", "-1"),), baseline_ms=0, loss_trigger=Decimal("4"))
    with pytest.raises(LedgerInputError):
        net_pnl(
            inst_ids=INST,
            unrealized_by_inst={},
            bills=(_bill("x", "8", "-1", "-0.01"),),
            baseline_ms=0,
            loss_trigger=Decimal("4"),
        )


def test_caps_are_the_callers() -> None:
    gross, margin = gross_and_margin((Decimal("10"), Decimal("6")), leverage=LEVER)
    assert gross == Decimal("16")
    assert margin == Decimal("8")
    assert cap_problems(gross, margin, gross_cap=Decimal("16"), margin_cap=Decimal("8")) == ()
    assert cap_problems(gross, margin, gross_cap=Decimal("15"), margin_cap=Decimal("8"))
