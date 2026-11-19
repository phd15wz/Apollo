"""Temporary policy for the 2026-11-19 selected book.

This is not a reusable trading framework. Retire it with the pilot.
Permanent account and market reads stay on ``PrivateClient`` and
``PublicClient``. Restart state stays in ``tools.okx.pilot_audit``.

Nothing here chooses a leg-stop distance, a slippage cap, or a fee rate.
Missing policy inputs block arming. ``LIVE_CLEARANCE`` stays false.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Mapping, Sequence

from tools.okx.account_ledger import (
    BillInput,
    LedgerInputError as PnlInputError,
    cap_problems as _ledger_cap_problems,
    commission_rate,
    net_pnl as _ledger_net_pnl,
    project_isolated_liq as _ledger_project_isolated_liq,
    stop_price,
)
from tools.okx.errors import LiveTradingDisabled

PLAN_ID = "2026-11-19-mu-lite-cohr-qqq"
# Owner-approved caps. A trigger, not a guaranteed maximum loss.
GROSS_CAP_USDT = Decimal("30")
INITIAL_MARGIN_CAP_USDT = Decimal("15")
NET_LOSS_TRIGGER_USDT = Decimal("7")
LEVERAGE = Decimal("2")
LOT_STEP = Decimal("0.01")
TD_MODE = "isolated"
ORD_TYPES = frozenset({"limit", "ioc"})
# Fat-finger ceiling. Not a risk-approved slippage buffer.
MAX_SLIPPAGE = Decimal("0.25")
# A check cannot treat a read older than this as fresh. This is not the
# time between checks.
MAX_AGE_MS = 120_000
# Owner operating choice for this run. Not a scheduler and not a promise
# that a check actually runs on time. Risk between checks is accepted.
INTENDED_CHECK_INTERVAL_MS = 15 * 60 * 1000
LIVE_CLEARANCE = False

# Names Jessie or the owner must set. Values in tests are not approvals.
UNSETTLED_POLICY_INPUTS: tuple[tuple[str, str], ...] = (
    (
        "distance_to_liq_fraction",
        "Fraction of the entry-to-liquidation gap at which a leg exits, strictly between 0 and 1. "
        "This repository does not choose it.",
    ),
    (
        "max_slippage_fraction",
        "IOC/limit price cap versus the mark, strictly above 0 and at most 0.25. "
        "0.25 is a fat-finger ceiling, not a recommended buffer.",
    ),
    (
        "max_input_age_ms",
        "Maximum age of mark, position, bill, fee, tier, and leverage reads. Above 120000 ms is refused.",
    ),
    (
        "acknowledge_between_check_risk",
        "Must be true. This run is checked about every 15 minutes. Nothing watches the book between checks. "
        "A leg can liquidate and the loss trigger is not evaluated in that gap. "
        "The process does not schedule the check and does not guarantee it runs on time.",
    ),
    (
        "entry_ord_type",
        "ioc or limit. A resting limit also needs limit_rest_steps.",
    ),
    (
        "max_close_attempts",
        "Close attempts before escalation, from 1 to 5. Exhaustion stops new orders.",
    ),
    (
        "acknowledge_leg_can_liquidate_before_portfolio_stop",
        "Must be true. Each leg's isolated margin is a few USDT, so one leg can liquidate before "
        "combined net loss reaches 7. Do not increase size or add collateral to change that.",
    ),
    (
        "acknowledge_public_tier_table",
        "Must be true. Pre-entry maintenance rate comes from keyless GET /api/v5/public/position-tiers. "
        "No Read endpoint returns this account's mmr before a position exists.",
    ),
)


@dataclass(frozen=True, slots=True)
class PilotLegSpec:
    inst_id: str
    open_side: str
    close_side: str
    sz: str
    open_pos_sides: frozenset[str]
    close_pos_sides: frozenset[str]

    @property
    def size(self) -> Decimal:
        return Decimal(self.sz)

    @property
    def direction(self) -> str:
        return "long" if self.open_side == "buy" else "short"


PILOT_LEGS: tuple[PilotLegSpec, ...] = (
    PilotLegSpec("MU-USDT-SWAP", "buy", "sell", "0.01", frozenset({"", "long"}), frozenset({"", "long"})),
    PilotLegSpec("LITE-USDT-SWAP", "buy", "sell", "0.01", frozenset({"", "long"}), frozenset({"", "long"})),
    PilotLegSpec("COHR-USDT-SWAP", "buy", "sell", "0.02", frozenset({"", "long"}), frozenset({"", "long"})),
    PilotLegSpec("QQQ-USDT-SWAP", "sell", "buy", "0.01", frozenset({"", "short"}), frozenset({"", "short"})),
)
PILOT_LEG: dict[str, PilotLegSpec] = {row.inst_id: row for row in PILOT_LEGS}


@dataclass(frozen=True, slots=True)
class PilotPolicy:
    """Caller-supplied inputs. Defaults are unset and cannot arm."""

    distance_to_liq_fraction: Decimal | None = None
    max_slippage_fraction: Decimal | None = None
    max_input_age_ms: int | None = None
    entry_ord_type: str | None = None
    limit_rest_steps: int | None = None
    max_close_attempts: int | None = None
    acknowledge_leg_can_liquidate_before_portfolio_stop: bool = False
    acknowledge_public_tier_table: bool = False
    acknowledge_between_check_risk: bool = False

    def canonical(self) -> dict[str, str | int | bool | None]:
        return {
            "acknowledge_between_check_risk": self.acknowledge_between_check_risk,
            "acknowledge_leg_can_liquidate_before_portfolio_stop": self.acknowledge_leg_can_liquidate_before_portfolio_stop,
            "acknowledge_public_tier_table": self.acknowledge_public_tier_table,
            "distance_to_liq_fraction": _canon_decimal(self.distance_to_liq_fraction),
            "entry_ord_type": self.entry_ord_type,
            "intended_check_interval_ms": INTENDED_CHECK_INTERVAL_MS,
            "limit_rest_steps": self.limit_rest_steps,
            "max_close_attempts": self.max_close_attempts,
            "max_input_age_ms": self.max_input_age_ms,
            "max_slippage_fraction": _canon_decimal(self.max_slippage_fraction),
            "plan_id": PLAN_ID,
        }


def policy_fingerprint(policy: PilotPolicy) -> str:
    payload = json.dumps(policy.canonical(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def policy_problems(policy: PilotPolicy) -> tuple[str, ...]:
    """Return why this policy cannot arm. Empty means the inputs are present and in range."""
    problems: list[str] = []
    fraction = policy.distance_to_liq_fraction
    if not isinstance(fraction, Decimal) or not fraction.is_finite() or not (Decimal("0") < fraction < Decimal("1")):
        problems.append("distance_to_liq_fraction is unset or outside (0, 1); this code does not choose it")
    slip = policy.max_slippage_fraction
    if not isinstance(slip, Decimal) or not slip.is_finite() or not (Decimal("0") < slip <= MAX_SLIPPAGE):
        problems.append("max_slippage_fraction is unset or outside (0, 0.25]")
    age = policy.max_input_age_ms
    if not isinstance(age, int) or isinstance(age, bool) or not 0 < age <= MAX_AGE_MS:
        problems.append("max_input_age_ms is unset or outside 1..120000")
    if policy.entry_ord_type not in {"ioc", "limit"}:
        problems.append("entry_ord_type must be ioc or limit")
    steps = policy.limit_rest_steps
    if policy.entry_ord_type == "limit":
        if not isinstance(steps, int) or isinstance(steps, bool) or not 1 <= steps <= 20:
            problems.append("limit_rest_steps is required for a limit entry and must be 1..20")
    elif steps is not None:
        problems.append("limit_rest_steps is only used when entry_ord_type is limit")
    attempts = policy.max_close_attempts
    if not isinstance(attempts, int) or isinstance(attempts, bool) or not 1 <= attempts <= 5:
        problems.append("max_close_attempts must be an integer from 1 to 5")
    if policy.acknowledge_leg_can_liquidate_before_portfolio_stop is not True:
        problems.append(
            "acknowledge_leg_can_liquidate_before_portfolio_stop is not true; "
            "a leg can be liquidated before combined net loss reaches 7 USDT"
        )
    if policy.acknowledge_public_tier_table is not True:
        problems.append(
            "acknowledge_public_tier_table is not true; pre-entry mmr is the public tier table only"
        )
    if policy.acknowledge_between_check_risk is not True:
        problems.append(
            "acknowledge_between_check_risk is not true; checks are intended every 15 minutes, "
            "nothing runs between them, and the schedule is not guaranteed"
        )
    return tuple(problems)


def net_pnl(
    *,
    unrealized_by_inst: Mapping[str, Decimal],
    bills: Sequence[BillInput],
    baseline_ms: int,
):
    """Plan pnl. The arithmetic lives in ``account_ledger``; the trigger is 7 USDT."""
    return _ledger_net_pnl(
        inst_ids=frozenset(PILOT_LEG),
        unrealized_by_inst=unrealized_by_inst,
        bills=bills,
        baseline_ms=baseline_ms,
        loss_trigger=NET_LOSS_TRIGGER_USDT,
    )


def project_isolated_liq(
    *,
    direction: str,
    avg_px: Decimal,
    pos: Decimal,
    ct_val: Decimal,
    mmr: Decimal,
    taker_commission: Decimal,
) -> Decimal:
    """Isolated projection locked at this plan's leverage of 2. Not exchange ``liqPx``."""
    return _ledger_project_isolated_liq(
        direction=direction,
        avg_px=avg_px,
        pos=pos,
        ct_val=ct_val,
        mmr=mmr,
        taker_commission=taker_commission,
        leverage=LEVERAGE,
    )


def isolated_loss_to_liq(*, direction: str, entry: Decimal, liq: Decimal, pos: Decimal, ct_val: Decimal) -> Decimal:
    quantity = pos * ct_val
    if direction == "long":
        loss = (entry - liq) * quantity
    elif direction == "short":
        loss = (liq - entry) * quantity
    else:
        raise PnlInputError("direction must be long or short")
    if loss <= 0:
        raise PnlInputError("liquidation loss is not positive")
    return loss


def portfolio_amounts(
    rows: Sequence[tuple[str, Decimal, Decimal]],
) -> tuple[Decimal, Decimal, Decimal]:
    """Return gross, initial margin at 2x, and net long notional.

    Each row is ``(inst_id, mark, ct_val)``. Size and side come from the plan.
    """
    gross = Decimal("0")
    net = Decimal("0")
    seen: set[str] = set()
    for inst_id, mark, ct_val in rows:
        leg = PILOT_LEG.get(inst_id)
        if leg is None:
            raise PnlInputError(f"notional row is outside the plan: {inst_id}")
        if inst_id in seen:
            raise PnlInputError(f"duplicate notional row for {inst_id}")
        seen.add(inst_id)
        _require_positive(mark, "mark")
        _require_positive(ct_val, "ct_val")
        notional = mark * ct_val * leg.size
        gross += notional
        net += notional if leg.direction == "long" else -notional
    if seen != set(PILOT_LEG):
        raise PnlInputError("notional rows do not cover the four selected legs")
    return gross, gross / LEVERAGE, net


def cap_problems(gross: Decimal, initial_margin: Decimal) -> tuple[str, ...]:
    return _ledger_cap_problems(
        gross,
        initial_margin,
        gross_cap=GROSS_CAP_USDT,
        margin_cap=INITIAL_MARGIN_CAP_USDT,
    )


def protected_limit_px(*, side: str, mark: Decimal, slippage: Decimal, tick: Decimal) -> str:
    """Floor the cap to the tick so the limit never pays through the slippage cap."""
    _require_positive(mark, "mark")
    _require_positive(tick, "tick")
    if not isinstance(slippage, Decimal) or not (Decimal("0") < slippage <= MAX_SLIPPAGE):
        raise PnlInputError("slippage cap missing")
    cap = mark * (Decimal("1") + slippage) if side == "buy" else mark * (Decimal("1") - slippage)
    units = (cap / tick).to_integral_value(rounding="ROUND_DOWN")
    price = units * tick
    if price <= 0 or price > cap:
        raise PnlInputError("protected price is outside the cap")
    return _dec_str(price)


def pilot_plan_message() -> str:
    caps = ", ".join(
        f"{row.inst_id} open {row.open_side} sz={row.sz} close {row.close_side} reduceOnly sz<={row.sz}"
        for row in PILOT_LEGS
    )
    return (
        "Selected-plan place is the 2026-11-19 book only: isolated, limit or ioc, "
        "exact open size, reduce-only close at or under that size. "
        f"Legs: {caps}. "
        "COHR is not a mild-3.1 stub name. pilot_plan is an explicit client flag, "
        "not an environment variable, and it is not portfolio clearance."
    )


def assert_pilot_plan_order(
    *,
    inst_id: str,
    side: str,
    sz: str,
    td_mode: str,
    ord_type: str,
    px: str,
    pos_side: str = "",
    reduce_only: bool = False,
) -> None:
    """Refuse a live place that is not an exact selected-plan leg."""
    row = PILOT_LEG.get(inst_id)
    if row is None or td_mode != TD_MODE or ord_type not in ORD_TYPES or reduce_only not in (True, False):
        raise LiveTradingDisabled(pilot_plan_message())
    expected_side = row.close_side if reduce_only else row.open_side
    expected_pos = row.close_pos_sides if reduce_only else row.open_pos_sides
    if side != expected_side or pos_side not in expected_pos:
        raise LiveTradingDisabled(pilot_plan_message())
    size = _plain_decimal(sz, "sz")
    _plain_decimal(px, "px")
    if reduce_only:
        if size > row.size or (size % LOT_STEP) != 0:
            raise LiveTradingDisabled(pilot_plan_message())
        return
    if size != row.size:
        raise LiveTradingDisabled(pilot_plan_message())


def _plain_decimal(value: str, label: str) -> Decimal:
    text = str(value).strip()
    if not text or text.startswith("+") or "e" in text.lower():
        raise LiveTradingDisabled(f"selected-plan {label} must be a plain positive decimal")
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise LiveTradingDisabled(f"selected-plan {label} must be a plain positive decimal") from exc
    if not number.is_finite() or number <= 0:
        raise LiveTradingDisabled(f"selected-plan {label} must be a plain positive decimal")
    return number


def _require_positive(value: Decimal, label: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise PnlInputError(f"{label} must be a positive decimal")


def _canon_decimal(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value, "f")


def _dec_str(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text
