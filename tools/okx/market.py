"""Pagination helpers over public market endpoints.

OKX returns newest-first pages. Helpers that take a time range reverse that
and yield chronological records (trade_id / candle ts ascending).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Literal

from tools.okx.client import PublicClient
from tools.okx.types import Candle, FundingRate, Trade

Pagination = Literal["tradeId", "ts"]


def history_trades_range(
    client: PublicClient,
    inst_id: str,
    *,
    start_ts_ms: int | None = None,
    end_ts_ms: int | None = None,
    start_trade_id: int | None = None,
    end_trade_id: int | None = None,
    pagination: Pagination = "ts",
    page_limit: int = 100,
    max_pages: int = 500,
) -> list[Trade]:
    """Page ``/api/v5/market/history-trades`` over a window (last 3 months).

    Use ``pagination='tradeId'`` to walk by exchange trade id, or ``'ts'``
    to walk by millisecond timestamp. Results are chronological and keep
    exchange trade-id order within a millisecond. For ranges older than
    three months use :mod:`tools.okx.archive`.
    """
    if pagination not in {"tradeId", "ts"}:
        raise ValueError("pagination must be 'tradeId' or 'ts'")
    collected: list[Trade] = []
    after: str | int | None
    if pagination == "tradeId":
        after = None if end_trade_id is None else end_trade_id + 1
    else:
        after = None if end_ts_ms is None else end_ts_ms + 1
    seen: set[int] = set()
    for _ in range(max_pages):
        page = client.history_trades(
            inst_id,
            pagination=pagination,
            after=after,
            limit=page_limit,
        )
        if not page:
            break
        stop = False
        for trade in page:
            if trade.trade_id in seen:
                continue
            if start_trade_id is not None and trade.trade_id < start_trade_id:
                stop = True
                continue
            if end_trade_id is not None and trade.trade_id > end_trade_id:
                continue
            if start_ts_ms is not None and trade.ts_ms < start_ts_ms:
                stop = True
                continue
            if end_ts_ms is not None and trade.ts_ms > end_ts_ms:
                continue
            seen.add(trade.trade_id)
            collected.append(trade)
        oldest = page[-1]
        if pagination == "tradeId":
            next_after = oldest.trade_id
        else:
            next_after = oldest.ts_ms
        if after is not None and str(next_after) == str(after):
            break
        after = next_after
        if stop or len(page) < page_limit:
            break
    collected.sort(key=lambda t: (t.ts_ms, t.trade_id))
    return collected


def candles_range(
    client: PublicClient,
    inst_id: str,
    *,
    start_ts_ms: int,
    end_ts_ms: int,
    bar: str = "1m",
    historical: bool = True,
    page_limit: int = 300,
    max_pages: int = 500,
) -> list[Candle]:
    """Page candles from ``end_ts_ms`` backward until ``start_ts_ms``."""
    collected: list[Candle] = []
    after: int | None = end_ts_ms + 1
    seen: set[int] = set()
    for _ in range(max_pages):
        page = client.candles(
            inst_id,
            bar=bar,
            after=after,
            limit=page_limit,
            historical=historical,
        )
        if not page:
            break
        stop = False
        for candle in page:
            if candle.ts_ms in seen:
                continue
            if candle.ts_ms < start_ts_ms:
                stop = True
                continue
            if candle.ts_ms > end_ts_ms:
                continue
            seen.add(candle.ts_ms)
            collected.append(candle)
        oldest = page[-1].ts_ms
        if after is not None and oldest == after:
            break
        after = oldest
        if stop or len(page) < page_limit:
            break
    collected.sort(key=lambda c: c.ts_ms)
    return collected


def funding_rate_history_range(
    client: PublicClient,
    inst_id: str,
    *,
    start_ts_ms: int | None = None,
    end_ts_ms: int | None = None,
    page_limit: int = 100,
    max_pages: int = 200,
) -> list[FundingRate]:
    collected: list[FundingRate] = []
    after: int | None = None if end_ts_ms is None else end_ts_ms + 1
    seen: set[int] = set()
    for _ in range(max_pages):
        page = client.funding_rate_history(inst_id, after=after, limit=page_limit)
        if not page:
            break
        stop = False
        for row in page:
            stamp = row.funding_time_ms or 0
            if stamp in seen:
                continue
            if start_ts_ms is not None and stamp < start_ts_ms:
                stop = True
                continue
            if end_ts_ms is not None and stamp > end_ts_ms:
                continue
            seen.add(stamp)
            collected.append(row)
        oldest = page[-1].funding_time_ms
        if oldest is None or oldest == after:
            break
        after = oldest
        if stop or len(page) < page_limit:
            break
    collected.sort(key=lambda r: r.funding_time_ms or 0)
    return collected


def iter_history_trades(
    client: PublicClient,
    inst_id: str,
    **kwargs: object,
) -> Iterator[Trade]:
    yield from history_trades_range(client, inst_id, **kwargs)  # type: ignore[arg-type]
