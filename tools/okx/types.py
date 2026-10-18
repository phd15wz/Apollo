"""Typed public-market records. Timestamps stay integer milliseconds."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import numpy as np

TRADE_DTYPE = np.dtype(
    [
        ("ts_ms", np.int64),
        ("trade_id", np.int64),
        ("price", np.float64),
        ("size", np.float64),
        ("side", "U4"),
    ]
)

CANDLE_DTYPE = np.dtype(
    [
        ("ts_ms", np.int64),
        ("open", np.float64),
        ("high", np.float64),
        ("low", np.float64),
        ("close", np.float64),
        ("volume", np.float64),
        ("volume_ccy", np.float64),
        ("volume_ccy_quote", np.float64),
        ("confirm", np.bool_),
    ]
)


def utc_from_ms(ts_ms: int) -> datetime:
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)


def _s(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _i(value: Any, default: int = 0) -> int:
    text = _s(value).strip()
    if text == "":
        return default
    return int(text)


def parse_trade_id(value: Any) -> int:
    """Parse an exchange trade id with ``int()`` only — never ``float``.

    ``int(float(id))`` rounds integers larger than 2**53, which would scramble
    exchange order.
    """
    if isinstance(value, bool):
        raise ValueError(f"invalid trade id {value!r}")
    if isinstance(value, int):
        if value < 0:
            raise ValueError(f"invalid trade id {value!r}")
        return value
    text = _s(value).strip()
    if text.startswith("+"):
        text = text[1:]
    if not text.isdigit():
        raise ValueError(f"invalid trade id {value!r}")
    return int(text)


def _f(value: Any, default: float = float("nan")) -> float:
    text = _s(value).strip()
    if text == "":
        return default
    return float(text)


def _opt_i(value: Any) -> int | None:
    text = _s(value).strip()
    if text == "":
        return None
    return int(text)


@dataclass(frozen=True, slots=True)
class Instrument:
    inst_type: str
    inst_id: str
    inst_family: str
    base_ccy: str
    quote_ccy: str
    settle_ccy: str
    state: str
    tick_sz: str
    lot_sz: str
    min_sz: str
    ct_val: str
    ct_mult: str
    list_time_ms: int | None
    exp_time_ms: int | None

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> Instrument:
        return cls(
            inst_type=_s(row.get("instType")),
            inst_id=_s(row.get("instId")),
            inst_family=_s(row.get("instFamily")),
            base_ccy=_s(row.get("baseCcy")),
            quote_ccy=_s(row.get("quoteCcy")),
            settle_ccy=_s(row.get("settleCcy")),
            state=_s(row.get("state")),
            tick_sz=_s(row.get("tickSz")),
            lot_sz=_s(row.get("lotSz")),
            min_sz=_s(row.get("minSz")),
            ct_val=_s(row.get("ctVal")),
            ct_mult=_s(row.get("ctMult")),
            list_time_ms=_opt_i(row.get("listTime")),
            exp_time_ms=_opt_i(row.get("expTime")),
        )


@dataclass(frozen=True, slots=True)
class Ticker:
    inst_type: str
    inst_id: str
    last: float
    last_sz: float
    ask_px: float
    ask_sz: float
    bid_px: float
    bid_sz: float
    open_24h: float
    high_24h: float
    low_24h: float
    vol_24h: float
    vol_ccy_24h: float
    ts_ms: int

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> Ticker:
        return cls(
            inst_type=_s(row.get("instType")),
            inst_id=_s(row.get("instId")),
            last=_f(row.get("last")),
            last_sz=_f(row.get("lastSz")),
            ask_px=_f(row.get("askPx")),
            ask_sz=_f(row.get("askSz")),
            bid_px=_f(row.get("bidPx")),
            bid_sz=_f(row.get("bidSz")),
            open_24h=_f(row.get("open24h")),
            high_24h=_f(row.get("high24h")),
            low_24h=_f(row.get("low24h")),
            vol_24h=_f(row.get("vol24h")),
            vol_ccy_24h=_f(row.get("volCcy24h")),
            ts_ms=_i(row.get("ts")),
        )


@dataclass(frozen=True, slots=True)
class BookLevel:
    price: float
    size: float
    order_count: int


@dataclass(frozen=True, slots=True)
class OrderBook:
    inst_id: str
    ts_ms: int
    seq_id: int | None
    asks: tuple[BookLevel, ...]
    bids: tuple[BookLevel, ...]

    @classmethod
    def from_dict(cls, row: dict[str, Any], inst_id: str = "") -> OrderBook:
        def levels(raw: Any) -> tuple[BookLevel, ...]:
            out: list[BookLevel] = []
            for item in raw or []:
                price = _f(item[0])
                size = _f(item[1])
                count = _i(item[-1]) if len(item) >= 3 else 0
                out.append(BookLevel(price=price, size=size, order_count=count))
            return tuple(out)

        seq = row.get("seqId")
        return cls(
            inst_id=inst_id,
            ts_ms=_i(row.get("ts")),
            seq_id=int(seq) if seq is not None and str(seq) != "" else None,
            asks=levels(row.get("asks")),
            bids=levels(row.get("bids")),
        )


@dataclass(frozen=True, slots=True)
class Trade:
    inst_id: str
    trade_id: int
    price: float
    size: float
    side: str
    ts_ms: int
    source: str = ""

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> Trade:
        return cls(
            inst_id=_s(row.get("instId")),
            trade_id=parse_trade_id(row.get("tradeId", row.get("trade_id"))),
            price=_f(row.get("px", row.get("price"))),
            size=_f(row.get("sz", row.get("size"))),
            side=_s(row.get("side")).lower(),
            ts_ms=_i(row.get("ts", row.get("timestamp_ms"))),
            source=_s(row.get("source")),
        )


@dataclass(frozen=True, slots=True)
class Candle:
    ts_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    volume_ccy: float
    volume_ccy_quote: float
    confirm: bool

    @classmethod
    def from_row(cls, row: list[Any]) -> Candle:
        confirm = _s(row[8] if len(row) > 8 else "1") == "1"
        return cls(
            ts_ms=_i(row[0]),
            open=_f(row[1]),
            high=_f(row[2]),
            low=_f(row[3]),
            close=_f(row[4]),
            volume=_f(row[5]),
            volume_ccy=_f(row[6] if len(row) > 6 else 0.0, default=0.0),
            volume_ccy_quote=_f(row[7] if len(row) > 7 else 0.0, default=0.0),
            confirm=confirm,
        )


@dataclass(frozen=True, slots=True)
class FundingRate:
    inst_id: str
    funding_rate: float
    next_funding_rate: float | None
    funding_time_ms: int | None
    next_funding_time_ms: int | None
    ts_ms: int | None = None

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> FundingRate:
        nxt = _s(row.get("nextFundingRate")).strip()
        return cls(
            inst_id=_s(row.get("instId")),
            funding_rate=_f(row.get("fundingRate", row.get("realizedRate"))),
            next_funding_rate=float(nxt) if nxt else None,
            funding_time_ms=_opt_i(row.get("fundingTime")),
            next_funding_time_ms=_opt_i(row.get("nextFundingTime")),
            ts_ms=_opt_i(row.get("ts")),
        )


@dataclass(frozen=True, slots=True)
class OpenInterest:
    inst_type: str
    inst_id: str
    oi: float
    oi_ccy: float
    ts_ms: int

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> OpenInterest:
        return cls(
            inst_type=_s(row.get("instType")),
            inst_id=_s(row.get("instId")),
            oi=_f(row.get("oi")),
            oi_ccy=_f(row.get("oiCcy")),
            ts_ms=_i(row.get("ts")),
        )


@dataclass(frozen=True, slots=True)
class MarkPrice:
    inst_type: str
    inst_id: str
    mark_px: float
    ts_ms: int

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> MarkPrice:
        return cls(
            inst_type=_s(row.get("instType")),
            inst_id=_s(row.get("instId")),
            mark_px=_f(row.get("markPx")),
            ts_ms=_i(row.get("ts")),
        )


@dataclass(frozen=True, slots=True)
class IndexTicker:
    inst_id: str
    idx_px: float
    high_24h: float
    low_24h: float
    open_24h: float
    ts_ms: int

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> IndexTicker:
        return cls(
            inst_id=_s(row.get("instId")),
            idx_px=_f(row.get("idxPx")),
            high_24h=_f(row.get("high24h")),
            low_24h=_f(row.get("low24h")),
            open_24h=_f(row.get("open24h")),
            ts_ms=_i(row.get("ts")),
        )


@dataclass(frozen=True, slots=True)
class MaintenanceWindow:
    title: str
    state: str
    begin_utc: datetime
    end_utc: datetime | None
    begin_ms: int
    end_ms: int | None
    service_type: str
    services: tuple[str, ...]
    href: str
    description: str
    maint_type: str
    system: str


@dataclass(frozen=True, slots=True)
class Announcement:
    title: str
    ann_type: str
    published_ms: int
    url: str


@dataclass(frozen=True, slots=True)
class ArchiveFile:
    inst_id: str
    filename: str
    url: str
    size_mb: float | None
    date_ts_ms: int | None
    date: str


def trades_to_array(trades: list[Trade]) -> np.ndarray:
    rows = np.empty(len(trades), dtype=TRADE_DTYPE)
    for i, trade in enumerate(trades):
        rows[i] = (trade.ts_ms, trade.trade_id, trade.price, trade.size, trade.side)
    return rows


def candles_to_array(candles: list[Candle]) -> np.ndarray:
    rows = np.empty(len(candles), dtype=CANDLE_DTYPE)
    for i, candle in enumerate(candles):
        rows[i] = (
            candle.ts_ms,
            candle.open,
            candle.high,
            candle.low,
            candle.close,
            candle.volume,
            candle.volume_ccy,
            candle.volume_ccy_quote,
            candle.confirm,
        )
    return rows
