"""Selected-plan controller.

Opens happen only in ``enter(..., send_orders=True)`` and only for the four
exact legs. ``step`` can cancel and send reduce-only closes. It does not
open risk and it does not start a thread. The intended cadence is one
operator call about every 15 minutes. Time since the last call does not
flatten the book. A stale quote or an unknown live order does not get a
priced close, and the other legs still can.
``LIVE_CLEARANCE`` stays false.

The audit file is the recovery record. The policy object is temporary
configuration and is not rewritten into that file except as a fingerprint.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from tools.okx.pilot_audit import AuditOrder, PilotAudit, create_audit, load_audit, save_audit
from tools.okx.pilot_policy import (
    LIVE_CLEARANCE,
    PILOT_LEG,
    PILOT_LEGS,
    BillInput,
    PilotPolicy,
    PnlInputError,
    cap_problems,
    commission_rate,
    isolated_loss_to_liq,
    net_pnl,
    policy_fingerprint,
    policy_problems,
    portfolio_amounts,
    project_isolated_liq,
    protected_limit_px,
    stop_price,
)

CLIENT_PREFIX = "APL6065"


class PilotBlocked(Exception):
    def __init__(self, message: str, blockers: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.blockers = blockers


class PilotNotFound(Exception):
    pass


class PilotRejected(Exception):
    pass


class PilotTransportError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class AccountSnap:
    acct_lv: str
    pos_mode: str
    perm: str
    observed_ms: int


@dataclass(frozen=True, slots=True)
class MarkSnap:
    mark_px: Decimal
    exchange_ts_ms: int
    observed_ms: int


@dataclass(frozen=True, slots=True)
class InstrumentSnap:
    ct_val: Decimal
    ct_mult: str
    ct_type: str
    settle_ccy: str
    state: str
    tick_sz: Decimal
    lot_sz: Decimal
    min_sz: Decimal
    group_id: str
    observed_ms: int


@dataclass(frozen=True, slots=True)
class FeeSnap:
    taker: Decimal
    level: str
    group_id: str
    ts_ms: int
    observed_ms: int


@dataclass(frozen=True, slots=True)
class LeverSnap:
    pos_side: str
    lever: str
    mgn_mode: str


@dataclass(frozen=True, slots=True)
class TierSnap:
    min_sz: Decimal
    max_sz: Decimal
    mmr: Decimal
    max_lever: Decimal
    observed_ms: int


@dataclass(frozen=True, slots=True)
class PositionSnap:
    inst_id: str
    pos_side: str
    pos: Decimal
    avg_px: Decimal | None
    upl: Decimal
    liq_px: Decimal | None
    mgn_mode: str
    observed_ms: int


@dataclass(frozen=True, slots=True)
class OrderSnap:
    inst_id: str
    cl_ord_id: str
    side: str
    sz: Decimal
    acc_fill_sz: Decimal
    state: str
    observed_ms: int


@dataclass(frozen=True, slots=True)
class BillSnap:
    bill_id: str
    inst_id: str
    bill_type: str
    ts_ms: int
    pnl: Decimal
    fee: Decimal
    ccy: str


@dataclass(frozen=True, slots=True)
class OrderRequest:
    inst_id: str
    side: str
    sz: str
    px: str
    ord_type: str
    reduce_only: bool
    pos_side: str
    cl_ord_id: str
    margin_usd: str | None
    intent: str


@dataclass(frozen=True, slots=True)
class PilotResult:
    phase: str
    action: str
    net_pnl: str | None
    reason: str
    blockers: tuple[str, ...]
    live_clearance: bool

    def __post_init__(self) -> None:
        if self.live_clearance is not False:
            raise PilotBlocked("live clearance cannot be set")


@dataclass
class _Facts:
    blockers: list[str]
    account: AccountSnap | None
    marks: dict[str, MarkSnap]
    instruments: dict[str, InstrumentSnap]
    fees: dict[str, FeeSnap]
    levers: dict[str, str]
    mmrs: dict[str, Decimal]
    positions: tuple[PositionSnap, ...] | None
    pending: tuple[OrderSnap, ...] | None
    bills: list[BillSnap] | None
    gross: Decimal | None
    initial_margin: Decimal | None


class PilotController:
    def __init__(self, path: Path, policy: PilotPolicy, audit: PilotAudit, *, resumed: bool) -> None:
        self.path = path
        self.policy = policy
        self.audit = audit
        self._resumed = resumed
        self._reason = audit.escalation_reason
        self._net: Decimal | None = None
        self._blockers: tuple[str, ...] = ()

    @classmethod
    def create(cls, path: Path, policy: PilotPolicy) -> PilotController:
        problems = policy_problems(policy)
        if problems:
            raise PilotBlocked("policy is not settled", problems)
        audit = create_audit(path, policy_fingerprint=policy_fingerprint(policy))
        return cls(path, policy, audit, resumed=False)

    @classmethod
    def load(cls, path: Path, policy: PilotPolicy) -> PilotController:
        problems = policy_problems(policy)
        if problems:
            raise PilotBlocked("policy is not settled", problems)
        audit = load_audit(path, policy_fingerprint=policy_fingerprint(policy))
        return cls(path, policy, audit, resumed=True)

    def assess(self, book: Any, now_ms: int) -> PilotResult:
        facts = self._facts(book, now_ms, need_bills=False, require_trade=False)
        self._blockers = tuple(facts.blockers)
        return self._result("assess")

    def arm(self, book: Any, now_ms: int) -> PilotResult:
        if self.audit.phase != "unarmed":
            raise PilotBlocked("arm is only available from an unarmed audit")
        facts = self._facts(book, now_ms, need_bills=False, require_trade=False)
        self._blockers = tuple(facts.blockers)
        if facts.blockers:
            raise PilotBlocked("not ready to arm: " + "; ".join(facts.blockers), tuple(facts.blockers))
        self.audit.phase = "armed"
        self.audit.baseline_ms = now_ms
        self._save()
        return self._result("arm")

    def enter(self, book: Any, now_ms: int, *, send_orders: bool = False) -> PilotResult:
        if send_orders is not True:
            raise PilotBlocked(
                "enter does not send orders unless send_orders=True. This is not live clearance."
            )
        if self.audit.phase != "armed" or self.audit.entries_sealed:
            raise PilotBlocked("entries are sealed or the audit is not armed")
        facts = self._facts(book, now_ms, need_bills=False, require_trade=True)
        self._blockers = tuple(facts.blockers)
        if facts.blockers:
            raise PilotBlocked("not ready to enter: " + "; ".join(facts.blockers), tuple(facts.blockers))
        self._resumed = False
        self.audit.phase = "entering"
        self._save()
        self._continue_entry(book, now_ms)
        return self._result("enter")

    def step(self, book: Any, now_ms: int) -> PilotResult:
        phase = self.audit.phase
        if phase in {"unarmed", "armed", "flat", "escalated"}:
            return self._result("idle")
        if self.audit.phase == "entering" and self._resumed:
            self._begin_unwind("restart interrupted an entry; remaining opens are not sent")
        if self.audit.phase == "entering":
            self._continue_entry(book, now_ms)
        if self.audit.phase == "monitoring":
            self._monitor(book, now_ms)
        if self.audit.phase == "unwinding":
            self._unwind(book, now_ms)
        if self.audit.phase in {"monitoring", "entering"}:
            self.audit.last_step_ms = now_ms
            self._save()
        return self._result("step")

    def _continue_entry(self, book: Any, now_ms: int) -> None:
        for leg in PILOT_LEGS:
            if self.audit.phase != "entering":
                return
            status = self._entry_leg(book, now_ms, leg)
            if status == "unknown":
                return
            if status == "pending":
                self._save()
                return
            if status != "filled":
                self._begin_unwind(f"entry failed on {leg.inst_id}: {status}")
                self._unwind(book, now_ms)
                return
        self.audit.phase = "monitoring"
        self.audit.entries_sealed = True
        self.audit.last_step_ms = now_ms
        self._reason = "all four legs filled; not an atomic fill"
        self._save()

    def _entry_leg(self, book: Any, now_ms: int, leg: object) -> str:
        assert hasattr(leg, "inst_id")
        existing = self._intent_order(leg.inst_id, "entry")
        if existing is None:
            if self.audit.entries_sealed:
                return "sealed"
            try:
                request = self._entry_request(book, now_ms, leg)
            except (PnlInputError, PilotTransportError, PilotBlocked) as exc:
                return f"unpriced:{exc.__class__.__name__}"
            self._submit(book, request)
            existing = self._intent_order(leg.inst_id, "entry")
        if existing is None:
            return "not-submitted"
        view = self._lookup(book, leg.inst_id, existing.cl_ord_id)
        if view == "unknown":
            return "unknown"
        if view is None:
            return "missing-after-submit"
        if view.state == "filled" and view.acc_fill_sz == leg.size:
            return "filled"
        if view.acc_fill_sz > 0 and view.acc_fill_sz < leg.size:
            return "partial"
        if view.state in {"canceled", "mmp_canceled"}:
            return "partial" if view.acc_fill_sz > 0 else "unfilled"
        if view.state in {"live", "partially_filled"}:
            if self.policy.entry_ord_type == "ioc":
                return "resting-ioc"
            waits = existing.waits
            limit = self.policy.limit_rest_steps or 0
            if waits >= limit:
                return "rested-too-long"
            existing.waits = waits + 1
            self._save()
            return "pending"
        return f"bad-state:{view.state}"

    def _monitor(self, book: Any, now_ms: int) -> None:
        facts = self._facts(book, now_ms, need_bills=True, require_trade=False)
        self._blockers = tuple(facts.blockers)
        if facts.positions is None or facts.pending is None:
            self._escalate("positions or pending orders are not visible")
            return
        exposed = self._signed(facts.positions)
        if exposed is None:
            self._escalate("selected-leg position is reversed, duplicated, or not a decimal")
            return
        open_book = any(qty != 0 for qty in exposed.values())
        live_orders = self._legs_with_live_orders(facts.pending)
        if facts.bills is None and open_book:
            self._begin_unwind("pnl ledger is incomplete; loss is not treated as zero")
            return
        reason = self._exit_reason(facts, exposed) if facts.bills is not None else None
        if reason and open_book:
            self._begin_unwind(reason)
            return
        if not open_book and not live_orders:
            self.audit.phase = "flat"
            self.audit.entries_sealed = True
            self.audit.escalation_reason = ""
            if reason:
                self._reason = reason
            elif not self._reason:
                self._reason = "flat"
            self._save()
            return
        notes = self._uncertainty(exposed, facts, live_orders)
        if notes:
            self._remember_unresolved(notes)
        elif self.audit.escalation_reason:
            self.audit.escalation_reason = ""
            self._save()

    def _exit_reason(self, facts: _Facts, exposed: dict[str, Decimal]) -> str | None:
        if facts.bills is None or self.audit.baseline_ms is None:
            return None
        try:
            breakdown = net_pnl(
                unrealized_by_inst={inst: self._upl(facts.positions or (), inst) for inst in exposed},
                bills=tuple(
                    BillInput(row.bill_id, row.inst_id, row.bill_type, row.ts_ms, row.pnl, row.fee, row.ccy)
                    for row in facts.bills
                ),
                baseline_ms=self.audit.baseline_ms,
            )
        except PnlInputError as exc:
            return f"pnl inputs are not usable: {exc}"
        self._net = breakdown.net
        if breakdown.saw_liquidation:
            return "exchange liquidation or ADL bill; remaining legs are unwound"
        if breakdown.trigger_hit:
            return "combined net pnl reached the -7 USDT exit trigger"
        for inst_id, signed in exposed.items():
            if signed == 0:
                continue
            mark = facts.marks.get(inst_id)
            if mark is None:
                continue
            stop = self._stop_for(facts, inst_id, signed)
            if stop is None:
                return f"leg protection for {inst_id} is not verifiable"
            if signed > 0 and mark.mark_px <= stop:
                return f"leg stop on {inst_id}"
            if signed < 0 and mark.mark_px >= stop:
                return f"leg stop on {inst_id}"
        return None

    def _unwind(self, book: Any, now_ms: int) -> None:
        guard = 0
        limit = int(self.policy.max_close_attempts or 1) + 1
        while self.audit.phase == "unwinding" and guard < limit:
            if self._unwind_once(book, now_ms):
                return
            guard += 1
        if self.audit.phase == "unwinding":
            self._escalate("unwind did not finish inside the close bound")

    def _unwind_once(self, book: Any, now_ms: int) -> bool:
        """Try one reduce-only pass. True means this step should not loop again."""
        if self.audit.phase != "unwinding":
            return True
        if self.audit.close_attempts >= int(self.policy.max_close_attempts or 0):
            self._escalate(self._exhausted_reason())
            return True
        facts = self._facts(book, now_ms, need_bills=False, require_trade=False)
        if facts.positions is None or facts.pending is None:
            self._escalate("cannot flatten because the book is not visible")
            return True
        self._cancel_ours(book, facts.pending)
        facts = self._facts(book, now_ms, need_bills=False, require_trade=False)
        if facts.positions is None or facts.pending is None:
            self._escalate("cannot reconcile after cancel")
            return True
        exposed = self._signed(facts.positions)
        if exposed is None:
            self._escalate("position sign does not match the selected plan")
            return True
        live_orders = self._legs_with_live_orders(facts.pending)
        if not any(qty != 0 for qty in exposed.values()) and not live_orders:
            self.audit.phase = "flat"
            self.audit.entries_sealed = True
            self._reason = self._reason or "flat"
            self._save()
            return True
        unresolved = self._uncertainty(exposed, facts, live_orders)
        closable = self._closable_legs(exposed, facts)
        for inst_id in live_orders:
            closable.pop(inst_id, None)
        if not closable:
            self._remember_unresolved(unresolved)
            return True
        self.audit.close_attempts += 1
        self.audit.entries_sealed = True
        self._save()
        for leg in PILOT_LEGS:
            signed = exposed.get(leg.inst_id, Decimal("0"))
            if signed == 0 or leg.inst_id not in closable:
                continue
            if (signed > 0) != (leg.direction == "long"):
                self._escalate(f"{leg.inst_id} exposure is the wrong direction")
                return True
            try:
                request = self._close_request(facts, leg, closable[leg.inst_id], now_ms)
            except (PnlInputError, PilotTransportError) as exc:
                self._reason = f"close price unavailable: {exc.__class__.__name__}"
                continue
            self._submit(book, request)
        self._remember_unresolved(unresolved)
        facts = self._facts(book, now_ms, need_bills=False, require_trade=False)
        if facts.positions is None:
            self._escalate("position read failed after a close attempt")
            return True
        exposed = self._signed(facts.positions)
        live_after = self._legs_with_live_orders(facts.pending or ())
        if exposed is not None and not any(qty != 0 for qty in exposed.values()) and not live_after:
            self.audit.phase = "flat"
            self.audit.escalation_reason = ""
            self._reason = self._reason or "flat"
            self._save()
            return True
        if unresolved:
            return True
        if self.audit.close_attempts >= int(self.policy.max_close_attempts or 0):
            self._escalate(self._exhausted_reason())
        return False

    def _facts(self, book: Any, now_ms: int, *, need_bills: bool, require_trade: bool) -> _Facts:
        begin = getattr(book, "begin_reads", None)
        if callable(begin):
            begin()
        facts = _Facts([], None, {}, {}, {}, {}, {}, None, None, [] if need_bills else None, None, None)
        # Reads that are not age-checked stay outside the freshness window.
        facts.positions = _call(book.positions, facts.blockers, "positions")
        facts.pending = _call(book.pending, facts.blockers, "pending")
        if need_bills and self.audit.baseline_ms is not None:
            rows: list[BillSnap] = []
            for leg in PILOT_LEGS:
                page = _call(lambda inst=leg.inst_id: book.bills(inst, self.audit.baseline_ms or 0), facts.blockers, f"bills:{leg.inst_id}")
                if page is None:
                    facts.bills = None
                    break
                rows.extend(page)
            else:
                facts.bills = rows
        lever_rows: dict[str, tuple[LeverSnap, ...] | None] = {}
        for leg in PILOT_LEGS:
            lever_rows[leg.inst_id] = _call(
                lambda inst=leg.inst_id: book.leverage(inst), facts.blockers, f"leverage:{leg.inst_id}"
            )
        facts.account = _call(book.account, facts.blockers, "account")
        snapped: dict[str, tuple[Any, Any, Any, Any]] = {}
        for leg in PILOT_LEGS:
            snapped[leg.inst_id] = (
                _call(lambda inst=leg.inst_id: book.mark(inst), facts.blockers, f"mark:{leg.inst_id}"),
                _call(lambda inst=leg.inst_id: book.instrument(inst), facts.blockers, f"instrument:{leg.inst_id}"),
                _call(lambda inst=leg.inst_id: book.fee(inst), facts.blockers, f"fee:{leg.inst_id}"),
                _call(lambda inst=leg.inst_id: book.tiers(inst), facts.blockers, f"tier:{leg.inst_id}"),
            )
        decision_ms = _decision_ms(book, now_ms)
        age = self.policy.max_input_age_ms
        for leg in PILOT_LEGS:
            mark, instrument, fee, tiers = snapped[leg.inst_id]
            levers = lever_rows[leg.inst_id]
            if mark is not None and age is not None and _fresh(mark.observed_ms, mark.exchange_ts_ms, decision_ms, age):
                facts.marks[leg.inst_id] = mark
            elif mark is not None:
                facts.blockers.append(f"stale or bad mark:{leg.inst_id}")
            if instrument is not None and age is not None and _fresh(instrument.observed_ms, None, decision_ms, age):
                problems = _instrument_problems(instrument)
                facts.blockers.extend(f"{leg.inst_id} {item}" for item in problems)
                if not problems:
                    facts.instruments[leg.inst_id] = instrument
            if fee is not None and age is not None and _fresh(fee.observed_ms, fee.ts_ms, decision_ms, age):
                if fee.group_id and instrument is not None and fee.group_id == instrument.group_id:
                    facts.fees[leg.inst_id] = fee
                else:
                    facts.blockers.append(f"fee group unmatched:{leg.inst_id}")
            elif fee is not None:
                facts.blockers.append(f"stale fee:{leg.inst_id}")
            if tiers is not None and instrument is not None:
                tier = _matching_tier(tiers, leg.size)
                if tier is None or age is None or not _fresh(tier.observed_ms, None, decision_ms, age):
                    facts.blockers.append(f"maintenance tier missing or stale:{leg.inst_id}")
                elif tier.max_lever < Decimal("2"):
                    facts.blockers.append(f"tier max leverage below 2:{leg.inst_id}")
                else:
                    facts.mmrs[leg.inst_id] = tier.mmr
            if levers is not None and facts.account is not None:
                lever = _lever_for(levers, facts.account.pos_mode, leg.direction)
                if lever != "2":
                    facts.blockers.append(f"leverage is not isolated 2:{leg.inst_id}")
                else:
                    facts.levers[leg.inst_id] = lever
        self._account_blockers(facts, require_trade, decision_ms)
        self._book_blockers(facts)
        if self.audit.phase in {"unarmed", "armed", "entering"}:
            self._cap_blockers(facts)
        return facts

    def _account_blockers(self, facts: _Facts, require_trade: bool, now_ms: int) -> None:
        account = facts.account
        if account is None:
            return
        age = self.policy.max_input_age_ms
        if age is None or not _fresh(account.observed_ms, None, now_ms, age):
            facts.blockers.append("account config is stale")
        if account.acct_lv != "2":
            facts.blockers.append("acctLv is not futures mode (2); isolated leg liquidation is not assumed")
        if account.pos_mode not in {"net_mode", "long_short_mode"}:
            facts.blockers.append("posMode is not net_mode or long_short_mode")
        tokens = {part.strip() for part in account.perm.split(",")}
        if "read_only" not in tokens:
            facts.blockers.append("API key permission does not include read_only")
        if require_trade and "trade" not in tokens:
            facts.blockers.append("API key permission does not include trade; Trade is not enabled by a read")

    def _book_blockers(self, facts: _Facts) -> None:
        if self.audit.phase not in {"unarmed", "armed"}:
            return
        if facts.positions is not None:
            for row in facts.positions:
                if row.pos != 0:
                    facts.blockers.append(f"position is not flat before entry:{row.inst_id}")
                    break
        if facts.pending:
            facts.blockers.append("pending orders are not empty")

    def _cap_blockers(self, facts: _Facts) -> None:
        if len(facts.marks) != 4 or len(facts.instruments) != 4:
            return
        try:
            gross, margin, _net = portfolio_amounts(
                tuple((leg.inst_id, facts.marks[leg.inst_id].mark_px, facts.instruments[leg.inst_id].ct_val) for leg in PILOT_LEGS)
            )
        except PnlInputError as exc:
            facts.blockers.append(str(exc))
            return
        facts.gross = gross
        facts.initial_margin = margin
        facts.blockers.extend(cap_problems(gross, margin))

    def _entry_request(self, book: Any, now_ms: int, leg: object) -> OrderRequest:
        facts = self._facts(book, now_ms, need_bills=False, require_trade=True)
        if facts.blockers:
            raise PilotBlocked("entry inputs changed", tuple(facts.blockers))
        assert facts.account is not None
        mark = facts.marks[leg.inst_id]
        instrument = facts.instruments[leg.inst_id]
        slip = self.policy.max_slippage_fraction
        assert slip is not None and self.policy.entry_ord_type is not None
        price = protected_limit_px(side=leg.open_side, mark=mark.mark_px, slippage=slip, tick=instrument.tick_sz)
        notional = mark.mark_px * instrument.ct_val * leg.size
        return OrderRequest(
            inst_id=leg.inst_id,
            side=leg.open_side,
            sz=leg.sz,
            px=price,
            ord_type=self.policy.entry_ord_type,
            reduce_only=False,
            pos_side=_pos_side(facts.account.pos_mode, leg.direction, close=False),
            cl_ord_id=self._next_id(),
            margin_usd=format(notional / Decimal("2"), "f"),
            intent="entry",
        )

    def _close_request(self, facts: _Facts, leg: object, size: Decimal, now_ms: int) -> OrderRequest:
        del now_ms
        position = _position_row(facts.positions or (), leg.inst_id)
        if position is None:
            raise PilotTransportError("position disappeared before the close")
        if position.pos_side in {"long", "short"}:
            pos_side = position.pos_side
        elif position.pos_side in {"", "net"}:
            pos_side = ""
        else:
            raise PilotTransportError("position side is not usable")
        mark = facts.marks[leg.inst_id]
        instrument = facts.instruments[leg.inst_id]
        slip = self.policy.max_slippage_fraction
        assert slip is not None
        price = protected_limit_px(side=leg.close_side, mark=mark.mark_px, slippage=slip, tick=instrument.tick_sz)
        return OrderRequest(
            inst_id=leg.inst_id,
            side=leg.close_side,
            sz=_dec_text(size),
            px=price,
            ord_type="ioc",
            reduce_only=True,
            pos_side=pos_side,
            cl_ord_id=self._next_id(),
            margin_usd=None,
            intent="close",
        )

    def _submit(self, book: Any, request: OrderRequest) -> None:
        if request.intent == "entry" and (self.audit.entries_sealed or self.audit.phase != "entering"):
            raise PilotBlocked("refusing a new entry")
        if request.intent == "close" and request.reduce_only is not True:
            raise PilotBlocked("close is not reduce-only")
        if self.audit.order_by_client_id(request.cl_ord_id) is not None:
            return
        self.audit.orders.append(
            AuditOrder(request.cl_ord_id, request.inst_id, request.intent, submitted=True)
        )
        self._save()
        try:
            book.place(request)
        except (PilotRejected, PilotTransportError, PilotBlocked):
            return

    def _lookup(self, book: Any, inst_id: str, cl_ord_id: str) -> OrderSnap | None | str:
        try:
            return book.order(inst_id, cl_ord_id)
        except PilotNotFound:
            return None
        except PilotTransportError:
            self._escalate(f"order lookup failed for {cl_ord_id}")
            return "unknown"

    def _cancel_ours(self, book: Any, pending: tuple[OrderSnap, ...]) -> None:
        for row in pending:
            if not self._ours(row):
                continue
            try:
                book.cancel(row.inst_id, row.cl_ord_id)
            except (PilotRejected, PilotTransportError, PilotBlocked):
                continue

    def _stop_for(self, facts: _Facts, inst_id: str, signed: Decimal) -> Decimal | None:
        leg = PILOT_LEG[inst_id]
        position = _position_row(facts.positions or (), inst_id)
        mark = facts.marks.get(inst_id)
        fee = facts.fees.get(inst_id)
        mmr = facts.mmrs.get(inst_id)
        instrument = facts.instruments.get(inst_id)
        fraction = self.policy.distance_to_liq_fraction
        if position is None or position.avg_px is None or mark is None or fraction is None:
            return None
        projected = None
        if fee is not None and mmr is not None and instrument is not None:
            try:
                projected = project_isolated_liq(
                    direction=leg.direction,
                    avg_px=position.avg_px,
                    pos=abs(signed),
                    ct_val=instrument.ct_val,
                    mmr=mmr,
                    taker_commission=commission_rate(fee.taker),
                )
            except PnlInputError:
                projected = None
        reference = _conservative_liq(leg.direction, projected, position.liq_px, mark.mark_px)
        if reference is None:
            return None
        try:
            return stop_price(
                direction=leg.direction,
                entry=position.avg_px,
                liq=reference,
                distance_fraction=fraction,
            )
        except PnlInputError:
            return None

    def _signed(self, positions: tuple[PositionSnap, ...]) -> dict[str, Decimal] | None:
        signed = {leg.inst_id: Decimal("0") for leg in PILOT_LEGS}
        for row in positions:
            if row.inst_id not in PILOT_LEG or row.pos == 0:
                continue
            qty = row.pos if row.pos_side in {"", "net", "long"} else -row.pos
            if row.pos_side == "short":
                qty = -abs(row.pos)
            elif row.pos_side == "long":
                qty = abs(row.pos)
            elif row.pos_side in {"", "net"}:
                qty = row.pos
            else:
                return None
            if signed[row.inst_id] != 0:
                return None
            signed[row.inst_id] = qty
        return signed

    def _upl(self, positions: tuple[PositionSnap, ...], inst_id: str) -> Decimal:
        for row in positions:
            if row.inst_id == inst_id and row.pos != 0:
                return row.upl
        return Decimal("0")

    def _ours(self, row: OrderSnap) -> bool:
        return row.inst_id in PILOT_LEG and row.cl_ord_id.startswith(CLIENT_PREFIX)

    def _intent_order(self, inst_id: str, intent: str) -> AuditOrder | None:
        for row in reversed(self.audit.orders):
            if row.inst_id == inst_id and row.intent == intent:
                return row
        return None

    def _next_id(self) -> str:
        return f"{CLIENT_PREFIX}{len(self.audit.orders) + 1:04d}"

    def _begin_unwind(self, reason: str) -> None:
        self.audit.phase = "unwinding"
        self.audit.entries_sealed = True
        self._reason = reason
        self._save()

    def _closable_legs(
        self, exposed: dict[str, Decimal], facts: _Facts
    ) -> dict[str, Decimal]:
        chosen: dict[str, Decimal] = {}
        for leg in PILOT_LEGS:
            signed = exposed.get(leg.inst_id, Decimal("0"))
            if signed == 0 or (signed > 0) != (leg.direction == "long"):
                continue
            if leg.inst_id not in facts.marks:
                continue
            instrument = facts.instruments.get(leg.inst_id)
            if instrument is None:
                continue
            sized = _closable(abs(signed), leg.size, instrument.lot_sz, instrument.min_sz)
            if isinstance(sized, Decimal):
                chosen[leg.inst_id] = sized
        return chosen

    def _unresolved_legs(self, exposed: dict[str, Decimal], facts: _Facts) -> tuple[str, ...]:
        notes: list[str] = []
        for leg in PILOT_LEGS:
            signed = exposed.get(leg.inst_id, Decimal("0"))
            if signed == 0:
                continue
            if (signed > 0) != (leg.direction == "long"):
                notes.append(f"{leg.inst_id} exposure is the wrong direction")
                continue
            if leg.inst_id not in facts.marks:
                notes.append(f"{leg.inst_id} quote is stale or missing; no close price was sent")
                continue
            instrument = facts.instruments.get(leg.inst_id)
            if instrument is None:
                notes.append(f"{leg.inst_id} instrument is missing")
                continue
            sized = _closable(abs(signed), leg.size, instrument.lot_sz, instrument.min_sz)
            if sized == "oversized":
                notes.append(f"{leg.inst_id} position is larger than the plan; it was not closed larger")
            elif sized == "dust":
                notes.append(f"{leg.inst_id} remainder is below the lot and was not rounded up")
        return tuple(notes)

    def _legs_with_live_orders(self, pending: tuple[OrderSnap, ...]) -> dict[str, str]:
        found: dict[str, str] = {}
        for row in pending:
            if row.inst_id not in PILOT_LEG or row.inst_id in found:
                continue
            if self._ours(row):
                found[row.inst_id] = (
                    f"{row.inst_id} still has live order {row.cl_ord_id}; no second order was sent"
                )
            else:
                found[row.inst_id] = (
                    f"{row.inst_id} has an unknown live order; it was not cancelled and no second order was sent"
                )
        return found

    def _uncertainty(
        self,
        exposed: dict[str, Decimal],
        facts: _Facts,
        live_orders: dict[str, str],
    ) -> tuple[str, ...]:
        notes = list(self._unresolved_legs(exposed, facts))
        for inst_id, note in live_orders.items():
            if inst_id not in {item.split(" ", 1)[0] for item in notes}:
                notes.append(note)
        return tuple(notes)

    def _remember_unresolved(self, notes: tuple[str, ...]) -> None:
        if not notes:
            return
        self.audit.escalation_reason = "; ".join(notes)
        self._reason = self.audit.escalation_reason
        self._save()

    def _exhausted_reason(self) -> str:
        recorded = self.audit.escalation_reason
        if recorded:
            return recorded + "; close attempts exhausted"
        return "close attempts exhausted"

    def _escalate(self, reason: str) -> None:
        self.audit.phase = "escalated"
        self.audit.entries_sealed = True
        self.audit.escalation_reason = reason
        self._reason = reason
        self._save()

    def _save(self) -> None:
        save_audit(self.path, self.audit)

    def _result(self, action: str) -> PilotResult:
        net = None if self._net is None else format(self._net, "f")
        return PilotResult(
            phase=self.audit.phase,
            action=action,
            net_pnl=net,
            reason=self._reason,
            blockers=self._blockers,
            live_clearance=LIVE_CLEARANCE,
        )


def projection_ready(
    *,
    direction: str,
    mark: Decimal,
    pos: Decimal,
    ct_val: Decimal,
    mmr: Decimal,
    taker_signed: Decimal,
    distance_fraction: Decimal,
) -> tuple[Decimal, Decimal, Decimal]:
    """Return liquidation, stop, and isolated loss. No position and no order."""
    liq = project_isolated_liq(
        direction=direction,
        avg_px=mark,
        pos=pos,
        ct_val=ct_val,
        mmr=mmr,
        taker_commission=commission_rate(taker_signed),
    )
    stop = stop_price(direction=direction, entry=mark, liq=liq, distance_fraction=distance_fraction)
    loss = isolated_loss_to_liq(direction=direction, entry=mark, liq=liq, pos=pos, ct_val=ct_val)
    return liq, stop, loss


def _call(fn, blockers: list[str], label: str):
    try:
        return fn()
    except (PilotTransportError, PilotNotFound, PilotBlocked, PnlInputError) as exc:
        blockers.append(f"{label}: {exc.__class__.__name__}")
        return None


def _decision_ms(book: Any, now_ms: int) -> int:
    """Time the freshness check uses after the reads inside this call.

    The caller's ``now_ms`` is taken before those reads. A venue timestamp
    from during the read is then newer than that frozen value and looks
    stale. The book's clock, read after the responses, is the check time.
    A clock behind the caller is not used.
    """
    clock = getattr(book, "clock_ms", None)
    if not callable(clock):
        return now_ms
    current = int(clock())
    if current < now_ms:
        return now_ms
    return current


def _fresh(observed_ms: int, exchange_ts_ms: int | None, now_ms: int, max_age: int) -> bool:
    """True when both the receipt and the venue time are inside ``max_age`` of the check.

    ``now_ms`` is the decision time. ``observed_ms`` is when this process
    received the row. Venue age is ``now_ms - exchange_ts_ms``, not the
    receipt delay and the venue-to-receipt gap measured separately. A
    timestamp after the decision is rejected. Values are not rewritten.
    """
    if observed_ms > now_ms or now_ms - observed_ms > max_age:
        return False
    if exchange_ts_ms is None:
        return True
    if exchange_ts_ms <= 0 or exchange_ts_ms > now_ms:
        return False
    return now_ms - exchange_ts_ms <= max_age


def _instrument_problems(row: InstrumentSnap) -> tuple[str, ...]:
    problems: list[str] = []
    if row.ct_type != "linear":
        problems.append("ctType is not linear")
    if row.ct_mult not in {"", "1"}:
        problems.append("ctMult is not 1")
    if row.settle_ccy != "USDT":
        problems.append("settleCcy is not USDT")
    if row.state != "live":
        problems.append("instrument is not live")
    if row.ct_val <= 0 or row.tick_sz <= 0 or row.lot_sz <= 0 or row.min_sz <= 0:
        problems.append("contract size is missing")
    if not row.group_id:
        problems.append("fee group id is missing")
    return tuple(problems)


def _matching_tier(tiers: tuple[TierSnap, ...], size: Decimal) -> TierSnap | None:
    matches = [row for row in tiers if row.min_sz <= size <= row.max_sz and row.mmr > 0]
    if len(matches) != 1:
        return None
    return matches[0]


def _lever_for(rows: tuple[LeverSnap, ...], pos_mode: str, direction: str) -> str | None:
    if pos_mode == "net_mode":
        found = [
            row.lever
            for row in rows
            if row.mgn_mode == "isolated" and row.pos_side in {"", "net"} and row.lever == "2"
        ]
    else:
        found = [
            row.lever
            for row in rows
            if row.mgn_mode == "isolated" and row.pos_side == direction and row.lever == "2"
        ]
    if len(found) != 1:
        return None
    return found[0]


def _position_row(positions: tuple[PositionSnap, ...], inst_id: str) -> PositionSnap | None:
    rows = [row for row in positions if row.inst_id == inst_id and row.pos != 0]
    if len(rows) != 1:
        return None
    return rows[0]


def _conservative_liq(direction: str, projected: Decimal | None, exchange: Decimal | None, mark: Decimal) -> Decimal | None:
    candidates = [price for price in (projected, exchange) if price is not None and price > 0]
    if direction == "long":
        usable = [price for price in candidates if price < mark]
        return max(usable) if usable else None
    if direction == "short":
        usable = [price for price in candidates if price > mark]
        return min(usable) if usable else None
    return None


def _closable(qty: Decimal, plan_sz: Decimal, lot: Decimal, min_sz: Decimal) -> Decimal | str:
    if qty > plan_sz:
        return "oversized"
    steps = (qty / lot).to_integral_value(rounding="ROUND_DOWN")
    size = steps * lot
    if size <= 0 or size < min_sz or size > qty or size > plan_sz:
        return "dust"
    return size


def _pos_side(pos_mode: str, direction: str, *, close: bool) -> str:
    del close
    if pos_mode == "net_mode":
        return ""
    return direction


def _dec_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text
