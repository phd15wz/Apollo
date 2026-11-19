"""Concrete OKX mapping for one selected-plan book.

This is not a second trading client. It calls the sanctioned reads and the
existing place and cancel methods. It does not set leverage, change position
mode, withdraw, or start a monitor.
"""

from __future__ import annotations

import time
from decimal import Decimal, InvalidOperation
from typing import Callable

from tools.okx.client import PublicClient
from tools.okx.errors import LiveTradingDisabled, OkxError, OkxHttpError, OrdersDisabled, RiskLimitError, UnsafeEndpointError
from tools.okx.pilot_policy import PILOT_LEG, PILOT_LEGS
from tools.okx.pilot_control import (
    AccountSnap,
    BillSnap,
    FeeSnap,
    InstrumentSnap,
    LeverSnap,
    MarkSnap,
    OrderRequest,
    OrderSnap,
    PilotNotFound,
    PilotRejected,
    PilotTransportError,
    PositionSnap,
    TierSnap,
)
from tools.okx.private import PrivateClient, open_margins_from_positions

_READ_ERRORS = (OkxError, OkxHttpError, UnsafeEndpointError, LiveTradingDisabled, ValueError, OSError)


class OkxPilotBook:
    """Read and, only when the controller asks, place or cancel through OKX."""

    def __init__(
        self,
        private: PrivateClient,
        public: PublicClient,
        *,
        now_ms: int,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        if private.pilot_plan is not True:
            raise ValueError("selected-plan book requires PrivateClient(pilot_plan=True)")
        self.private = private
        self.public = public
        self.now_ms = now_ms
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self._generation = False
        self._instrument_cache: dict | None = None
        self._instrument_observed = 0
        self._mark_cache: dict[str, MarkSnap] | None = None
        self._tier_cache: dict[str, tuple[TierSnap, ...] | None] | None = None
        self._fee_cache: dict[str, FeeSnap] = {}

    def clock_ms(self) -> int:
        return int(self._clock_ms())

    def begin_reads(self) -> None:
        """Start one snapshot. Cached rows keep the receipt time of that response."""
        self._generation = True
        self._instrument_cache = None
        self._instrument_observed = 0
        self._mark_cache = None
        self._tier_cache = None
        self._fee_cache = {}

    def account(self) -> AccountSnap:
        row = self._read(self.private.account_config)
        return AccountSnap(row.acct_lv, row.pos_mode, row.perm, self.clock_ms())

    def fee(self, inst_id: str) -> FeeSnap:
        instrument = self._instrument_row(inst_id)
        if not instrument.inst_family or not instrument.group_id:
            raise PilotTransportError("instrument has no fee family or group")
        cached = self._fee_cache.get(instrument.inst_family) if self._generation else None
        if cached is not None:
            return cached
        quote = self._read(lambda: self.private.trade_fee("SWAP", inst_family=instrument.inst_family))
        matched = [group for group in quote.groups if group.group_id == instrument.group_id]
        if len(matched) != 1 or not matched[0].taker:
            raise PilotTransportError("fee group did not match the instrument")
        snap = FeeSnap(
            _decimal(matched[0].taker, "taker"),
            quote.level,
            matched[0].group_id,
            quote.ts_ms,
            self.clock_ms(),
        )
        if self._generation:
            self._fee_cache[instrument.inst_family] = snap
        return snap

    def leverage(self, inst_id: str) -> tuple[LeverSnap, ...]:
        rows = self._read(lambda: self.private.leverage_info(inst_id, mgn_mode="isolated"))
        return tuple(LeverSnap(row.pos_side, row.lever, row.mgn_mode) for row in rows)

    def tiers(self, inst_id: str) -> tuple[TierSnap, ...]:
        if self._generation:
            if self._tier_cache is None:
                self._tier_cache = self._load_tiers()
            rows = self._tier_cache.get(inst_id)
            if not rows:
                raise PilotTransportError("maintenance tier row is missing")
            return rows
        return self._tiers_one(inst_id)

    def instrument(self, inst_id: str) -> InstrumentSnap:
        row = self._instrument_row(inst_id)
        observed = self._instrument_observed if self._generation else self.clock_ms()
        return InstrumentSnap(
            ct_val=_decimal(row.ct_val, "ctVal"),
            ct_mult=row.ct_mult,
            ct_type=row.ct_type,
            settle_ccy=row.settle_ccy,
            state=row.state,
            tick_sz=_decimal(row.tick_sz, "tickSz"),
            lot_sz=_decimal(row.lot_sz, "lotSz"),
            min_sz=_decimal(row.min_sz, "minSz"),
            group_id=row.group_id,
            observed_ms=observed,
        )

    def mark(self, inst_id: str) -> MarkSnap:
        if self._generation:
            if self._mark_cache is None:
                self._mark_cache = self._load_marks()
            snap = self._mark_cache.get(inst_id)
            if snap is None:
                raise PilotTransportError("mark price row is missing")
            return snap
        rows = self._read(lambda: self.public.mark_price("SWAP", inst_id=inst_id))
        matched = [row for row in rows if row.inst_id == inst_id]
        if len(matched) != 1:
            raise PilotTransportError("mark price row is missing")
        return self._mark_snap(matched[0], self.clock_ms())

    def positions(self) -> tuple[PositionSnap, ...]:
        rows = self._read(lambda: self.private.positions(inst_type="SWAP"))
        out: list[PositionSnap] = []
        for row in rows:
            pos = _decimal(str(row.pos), "pos")
            avg = None if pos == 0 or row.avg_px == 0 else _decimal(str(row.avg_px), "avgPx")
            liq = _decimal(row.liq_px, "liqPx") if row.liq_px else None
            out.append(
                PositionSnap(
                    inst_id=row.inst_id,
                    pos_side=row.pos_side,
                    pos=pos,
                    avg_px=avg,
                    upl=_decimal(str(row.upl), "upl"),
                    liq_px=liq,
                    mgn_mode=row.margin_mode,
                    observed_ms=self.clock_ms(),
                )
            )
        return tuple(out)

    def pending(self) -> tuple[OrderSnap, ...]:
        rows = self._read(lambda: self.private.open_orders(inst_type="SWAP"))
        return tuple(self._order(row) for row in rows)

    def order(self, inst_id: str, cl_ord_id: str) -> OrderSnap:
        try:
            row = self.private.get_order(inst_id, cl_ord_id=cl_ord_id)
        except OkxError as exc:
            if "no order" in str(exc):
                raise PilotNotFound(cl_ord_id) from exc
            raise PilotTransportError("order lookup failed") from exc
        except _READ_ERRORS as exc:
            raise PilotTransportError("order lookup failed") from exc
        return self._order(row)

    def bills(self, inst_id: str, begin_ms: int) -> tuple[BillSnap, ...]:
        rows: list[BillSnap] = []
        after = ""
        for _page in range(5):
            page = self._read(
                lambda after=after: self.private.bills(
                    inst_type="SWAP",
                    inst_id=inst_id,
                    begin=str(begin_ms),
                    after=after,
                    limit=100,
                )
            )
            if not page:
                return tuple(rows)
            for row in page:
                rows.append(
                    BillSnap(
                        row.bill_id,
                        row.inst_id,
                        row.bill_type,
                        row.ts_ms,
                        _decimal(row.pnl or "0", "pnl"),
                        _decimal(row.fee or "0", "fee"),
                        row.ccy,
                    )
                )
            if len(page) < 100:
                return tuple(rows)
            after = page[-1].bill_id
            if not after:
                raise PilotTransportError("bills page has no bill id")
        raise PilotTransportError("bills page limit; the ledger is incomplete")

    def place(self, request: OrderRequest) -> None:
        try:
            margins = open_margins_from_positions(self.private.positions(inst_type="SWAP"))
            self.private.place_order(
                request.inst_id,
                side=request.side,
                sz=request.sz,
                td_mode="isolated",
                ord_type=request.ord_type,
                px=request.px,
                cl_ord_id=request.cl_ord_id,
                pos_side=request.pos_side,
                reduce_only=request.reduce_only,
                margin_usd=None if request.margin_usd is None else float(request.margin_usd),
                open_margins=margins,
            )
        except (LiveTradingDisabled, OrdersDisabled, RiskLimitError, ValueError) as exc:
            raise PilotRejected(str(exc)) from exc
        except _READ_ERRORS as exc:
            raise PilotTransportError("place failed") from exc

    def cancel(self, inst_id: str, cl_ord_id: str) -> None:
        try:
            self.private.cancel_order(inst_id, cl_ord_id=cl_ord_id)
        except (LiveTradingDisabled, OrdersDisabled, ValueError) as exc:
            raise PilotRejected(str(exc)) from exc
        except _READ_ERRORS as exc:
            raise PilotTransportError("cancel failed") from exc

    def _instrument_row(self, inst_id: str):
        if self._generation:
            if self._instrument_cache is None:
                rows = self._read(lambda: self.public.instruments("SWAP"))
                self._instrument_observed = self.clock_ms()
                self._instrument_cache = {row.inst_id: row for row in rows}
            row = self._instrument_cache.get(inst_id)
            if row is None:
                raise PilotTransportError("instrument row is missing")
            return row
        rows = self._read(lambda: self.public.instruments("SWAP", inst_id=inst_id))
        matched = [row for row in rows if row.inst_id == inst_id]
        if len(matched) != 1:
            raise PilotTransportError("instrument row is missing")
        return matched[0]

    def _load_marks(self) -> dict[str, MarkSnap]:
        rows = self._read(lambda: self.public.mark_price("SWAP"))
        observed = self.clock_ms()
        found: dict[str, MarkSnap] = {}
        for row in rows:
            if row.inst_id not in PILOT_LEG or row.inst_id in found:
                continue
            found[row.inst_id] = self._mark_snap(row, observed)
        return found

    def _load_tiers(self) -> dict[str, tuple[TierSnap, ...] | None]:
        families: list[str] = []
        family_of: dict[str, str] = {}
        for leg in PILOT_LEGS:
            instrument = self._instrument_row(leg.inst_id)
            if not instrument.inst_family:
                raise PilotTransportError("instrument has no tier family")
            family_of[leg.inst_id] = instrument.inst_family
            if instrument.inst_family not in families:
                families.append(instrument.inst_family)
        if len(families) > 5:
            raise PilotTransportError("too many tier families")
        rows = self._read(
            lambda: self.public.position_tiers("SWAP", td_mode="isolated", inst_family=",".join(families))
        )
        observed = self.clock_ms()
        cache: dict[str, tuple[TierSnap, ...] | None] = {}
        for leg in PILOT_LEGS:
            family = family_of[leg.inst_id]
            matched = tuple(self._tier_snap(row, observed) for row in rows if _tier_applies(row, leg.inst_id, family))
            cache[leg.inst_id] = matched or None
        return cache

    def _tiers_one(self, inst_id: str) -> tuple[TierSnap, ...]:
        instrument = self._instrument_row(inst_id)
        if not instrument.inst_family:
            raise PilotTransportError("instrument has no tier family")
        family = instrument.inst_family
        rows = self._read(lambda: self.public.position_tiers("SWAP", td_mode="isolated", inst_family=family))
        observed = self.clock_ms()
        matched = tuple(self._tier_snap(row, observed) for row in rows if _tier_applies(row, inst_id, family))
        if not matched:
            raise PilotTransportError("maintenance tier row is missing")
        return matched

    def _mark_snap(self, row, observed: int) -> MarkSnap:
        return MarkSnap(_decimal(str(row.mark_px), "markPx"), row.ts_ms, observed)

    def _tier_snap(self, row, observed: int) -> TierSnap:
        return TierSnap(
            _decimal(row.min_sz, "minSz"),
            _decimal(row.max_sz, "maxSz"),
            _decimal(row.mmr, "mmr"),
            _decimal(row.max_lever, "maxLever"),
            observed,
        )

    def _order(self, row) -> OrderSnap:
        return OrderSnap(
            inst_id=row.inst_id,
            cl_ord_id=row.cl_ord_id,
            side=row.side,
            sz=_decimal(row.sz, "sz"),
            acc_fill_sz=_decimal(row.acc_fill_sz or "0", "accFillSz"),
            state=row.state,
            observed_ms=self.clock_ms(),
        )

    def _read(self, fn):
        try:
            return fn()
        except _READ_ERRORS as exc:
            raise PilotTransportError(exc.__class__.__name__) from exc


def _tier_applies(row, inst_id: str, family: str) -> bool:
    row_family = row.inst_family or row.underlying
    if row_family != family:
        return False
    return not row.inst_id or row.inst_id == inst_id


def _decimal(value: str, label: str) -> Decimal:
    text = str(value).strip()
    if not text:
        raise PilotTransportError(f"{label} is empty")
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise PilotTransportError(f"{label} is not a decimal") from exc
    if not number.is_finite():
        raise PilotTransportError(f"{label} is not finite")
    return number
