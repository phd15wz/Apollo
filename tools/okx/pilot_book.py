"""Concrete OKX mapping for one selected-plan book.

This is not a second trading client. It calls the sanctioned reads and the
existing place and cancel methods. It does not set leverage, change position
mode, withdraw, or start a monitor.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from tools.okx.client import PublicClient
from tools.okx.errors import LiveTradingDisabled, OkxError, OkxHttpError, OrdersDisabled, RiskLimitError, UnsafeEndpointError
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

    def __init__(self, private: PrivateClient, public: PublicClient, *, now_ms: int) -> None:
        if private.pilot_plan is not True:
            raise ValueError("selected-plan book requires PrivateClient(pilot_plan=True)")
        self.private = private
        self.public = public
        self.now_ms = now_ms

    def account(self) -> AccountSnap:
        row = self._read(self.private.account_config)
        return AccountSnap(row.acct_lv, row.pos_mode, row.perm, self.now_ms)

    def fee(self, inst_id: str) -> FeeSnap:
        instrument = self._instrument_row(inst_id)
        if not instrument.inst_family or not instrument.group_id:
            raise PilotTransportError("instrument has no fee family or group")
        quote = self._read(lambda: self.private.trade_fee("SWAP", inst_family=instrument.inst_family))
        matched = [group for group in quote.groups if group.group_id == instrument.group_id]
        if len(matched) != 1 or not matched[0].taker:
            raise PilotTransportError("fee group did not match the instrument")
        return FeeSnap(
            _decimal(matched[0].taker, "taker"),
            quote.level,
            matched[0].group_id,
            quote.ts_ms,
            self.now_ms,
        )

    def leverage(self, inst_id: str) -> tuple[LeverSnap, ...]:
        rows = self._read(lambda: self.private.leverage_info(inst_id, mgn_mode="isolated"))
        return tuple(LeverSnap(row.pos_side, row.lever, row.mgn_mode) for row in rows)

    def tiers(self, inst_id: str) -> tuple[TierSnap, ...]:
        rows = self._read(lambda: self.public.position_tiers("SWAP", td_mode="isolated", inst_id=inst_id))
        return tuple(
            TierSnap(
                _decimal(row.min_sz, "minSz"),
                _decimal(row.max_sz, "maxSz"),
                _decimal(row.mmr, "mmr"),
                _decimal(row.max_lever, "maxLever"),
                self.now_ms,
            )
            for row in rows
        )

    def instrument(self, inst_id: str) -> InstrumentSnap:
        row = self._instrument_row(inst_id)
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
            observed_ms=self.now_ms,
        )

    def mark(self, inst_id: str) -> MarkSnap:
        rows = self._read(lambda: self.public.mark_price("SWAP", inst_id=inst_id))
        matched = [row for row in rows if row.inst_id == inst_id]
        if len(matched) != 1:
            raise PilotTransportError("mark price row is missing")
        return MarkSnap(_decimal(str(matched[0].mark_px), "markPx"), matched[0].ts_ms, self.now_ms)

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
                    observed_ms=self.now_ms,
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
        rows = self._read(lambda: self.public.instruments("SWAP", inst_id=inst_id))
        matched = [row for row in rows if row.inst_id == inst_id]
        if len(matched) != 1:
            raise PilotTransportError("instrument row is missing")
        return matched[0]

    def _order(self, row) -> OrderSnap:
        return OrderSnap(
            inst_id=row.inst_id,
            cl_ord_id=row.cl_ord_id,
            side=row.side,
            sz=_decimal(row.sz, "sz"),
            acc_fill_sz=_decimal(row.acc_fill_sz or "0", "accFillSz"),
            state=row.state,
            observed_ms=self.now_ms,
        )

    def _read(self, fn):
        try:
            return fn()
        except _READ_ERRORS as exc:
            raise PilotTransportError(exc.__class__.__name__) from exc


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
