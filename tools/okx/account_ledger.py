"""Generic position accounting. No portfolio and no default trigger.

Callers pass the instrument set, the leverage, the loss trigger, and the
caps. A missing figure raises. A positive OKX fee rate is a rebate and
contributes no commission.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping, Sequence


class LedgerInputError(Exception):
    """A bill or price cannot be used without guessing."""


@dataclass(frozen=True, slots=True)
class BillInput:
    bill_id: str
    inst_id: str
    bill_type: str
    ts_ms: int
    pnl: Decimal
    fee: Decimal
    ccy: str


@dataclass(frozen=True, slots=True)
class PnlBreakdown:
    unrealized: Decimal
    realized_trade: Decimal
    fees: Decimal
    funding: Decimal
    liquidation: Decimal
    net: Decimal
    saw_liquidation: bool
    loss_trigger: Decimal

    @property
    def trigger_hit(self) -> bool:
        return self.net <= -self.loss_trigger


def net_pnl(
    *,
    inst_ids: frozenset[str],
    unrealized_by_inst: Mapping[str, Decimal],
    bills: Sequence[BillInput],
    baseline_ms: int,
    loss_trigger: Decimal,
) -> PnlBreakdown:
    """Combined pnl since ``baseline_ms`` for ``inst_ids`` only.

    Unrealized is mark-to-market. Trade pnl, trade fee, and funding come
    from bills. Funding uses bill pnl. A funding bill that also carries a
    fee is refused. ``loss_trigger`` is the caller's positive USDT amount.
    """
    if not inst_ids:
        raise LedgerInputError("instrument set is empty")
    trigger = _positive(loss_trigger, "loss_trigger")
    unrealized = Decimal("0")
    for inst_id, upl in unrealized_by_inst.items():
        if inst_id not in inst_ids:
            raise LedgerInputError(f"unrealized position is outside the set: {inst_id}")
        if not isinstance(upl, Decimal) or not upl.is_finite():
            raise LedgerInputError(f"unrealized pnl missing for {inst_id}")
        unrealized += upl
    realized = Decimal("0")
    fees = Decimal("0")
    funding = Decimal("0")
    liquidation = Decimal("0")
    saw_liquidation = False
    seen: dict[str, tuple[Decimal, Decimal]] = {}
    for bill in bills:
        if bill.inst_id not in inst_ids or bill.ts_ms < baseline_ms:
            continue
        if bill.ccy != "USDT":
            raise LedgerInputError(f"non-USDT bill {bill.bill_id} on {bill.inst_id}")
        previous = seen.get(bill.bill_id)
        current = (bill.pnl, bill.fee)
        if previous is not None and previous != current:
            raise LedgerInputError(f"bill {bill.bill_id} was seen twice with different amounts")
        if previous is not None:
            continue
        seen[bill.bill_id] = current
        if bill.bill_type == "2":
            realized += bill.pnl
            fees += bill.fee
        elif bill.bill_type == "8":
            if bill.fee != 0:
                raise LedgerInputError(f"funding bill {bill.bill_id} has both pnl and fee")
            funding += bill.pnl
        elif bill.bill_type in {"5", "9"}:
            saw_liquidation = True
            liquidation += bill.pnl + bill.fee
        else:
            raise LedgerInputError(f"unclassified bill type {bill.bill_type} on {bill.inst_id}")
    net = unrealized + realized + fees + funding + liquidation
    return PnlBreakdown(unrealized, realized, fees, funding, liquidation, net, saw_liquidation, trigger)


def project_isolated_liq(
    *,
    direction: str,
    avg_px: Decimal,
    pos: Decimal,
    ct_val: Decimal,
    mmr: Decimal,
    taker_commission: Decimal,
    leverage: Decimal,
) -> Decimal:
    """Isolated linear USDT liquidation price at the caller's leverage.

    Margin is notional/leverage with no added collateral. Open and close both
    use the absolute taker commission. There is no default leverage.
    """
    lever = _positive(leverage, "leverage")
    if lever <= 1:
        raise LedgerInputError("leverage must be greater than 1")
    _positive(avg_px, "avg_px")
    _positive(pos, "pos")
    _positive(ct_val, "ct_val")
    if not isinstance(mmr, Decimal) or not mmr.is_finite() or not (Decimal("0") < mmr < Decimal("1")):
        raise LedgerInputError("maintenance rate missing or outside (0, 1)")
    if (
        not isinstance(taker_commission, Decimal)
        or not taker_commission.is_finite()
        or taker_commission < 0
        or taker_commission >= Decimal("1")
    ):
        raise LedgerInputError("taker commission missing or outside [0, 1)")
    if mmr + taker_commission >= 1:
        raise LedgerInputError("maintenance rate plus taker commission is not below 1")
    quantity = pos * ct_val
    notional = avg_px * quantity
    margin = notional / lever
    open_fee = notional * taker_commission
    if direction == "long":
        price = (notional - margin + open_fee) / (quantity * (1 - mmr - taker_commission))
    elif direction == "short":
        price = (margin - open_fee + notional) / (quantity * (1 + mmr + taker_commission))
    else:
        raise LedgerInputError("direction must be long or short")
    if not price.is_finite() or price <= 0:
        raise LedgerInputError("projected liquidation price is not positive")
    if direction == "long" and price >= avg_px:
        raise LedgerInputError("projected long liquidation is not below the entry")
    if direction == "short" and price <= avg_px:
        raise LedgerInputError("projected short liquidation is not above the entry")
    return price


def stop_price(*, direction: str, entry: Decimal, liq: Decimal, distance_fraction: Decimal) -> Decimal:
    """Price strictly between entry and liquidation. The fraction is the caller's."""
    if not isinstance(distance_fraction, Decimal) or not (Decimal("0") < distance_fraction < Decimal("1")):
        raise LedgerInputError("distance fraction must be inside (0, 1)")
    if direction == "long":
        if not liq < entry:
            raise LedgerInputError("long liquidation is not below entry")
        stop = liq + distance_fraction * (entry - liq)
        if not liq < stop < entry:
            raise LedgerInputError("long stop is not strictly before liquidation")
        return stop
    if direction == "short":
        if not entry < liq:
            raise LedgerInputError("short liquidation is not above entry")
        stop = liq - distance_fraction * (liq - entry)
        if not entry < stop < liq:
            raise LedgerInputError("short stop is not strictly before liquidation")
        return stop
    raise LedgerInputError("direction must be long or short")


def commission_rate(okx_signed_taker: Decimal) -> Decimal:
    """OKX commission is negative. A rebate contributes 0, not a credit."""
    if not isinstance(okx_signed_taker, Decimal) or not okx_signed_taker.is_finite():
        raise LedgerInputError("taker fee rate missing")
    if okx_signed_taker < 0:
        return -okx_signed_taker
    return Decimal("0")


def gross_and_margin(notionals: Sequence[Decimal], *, leverage: Decimal) -> tuple[Decimal, Decimal]:
    lever = _positive(leverage, "leverage")
    gross = Decimal("0")
    for notional in notionals:
        gross += _positive(notional, "notional")
    return gross, gross / lever


def cap_problems(
    gross: Decimal,
    initial_margin: Decimal,
    *,
    gross_cap: Decimal,
    margin_cap: Decimal,
) -> tuple[str, ...]:
    problems: list[str] = []
    if gross > _positive(gross_cap, "gross_cap"):
        problems.append(f"gross {gross} is above the caller cap {gross_cap}")
    if initial_margin > _positive(margin_cap, "margin_cap"):
        problems.append(f"initial margin {initial_margin} is above the caller cap {margin_cap}")
    return tuple(problems)


def _positive(value: Decimal, label: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise LedgerInputError(f"{label} must be a positive decimal")
    return value
