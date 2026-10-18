"""Trade-tape hygiene for Hawkes research.

Keeps millisecond timestamps and exchange trade-id order. Same-millisecond
prints are reported, optionally aggregated by (timestamp, side), or given a
stable within-ms order. Random jitter is not offered: Filimonov–Sornette
jitter on coarse stamps biases kernel parameters (see
``skills/hawkes-process/SKILL.md``).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Literal, Sequence

from tools.okx.types import Trade, trades_to_array

TieMethod = Literal["report", "aggregate_by_timestamp_side", "stable_within_ms"]


@dataclass(frozen=True, slots=True)
class TieReport:
    n_trades: int
    n_timestamps: int
    n_tied_timestamps: int
    n_tied_trades: int
    max_tie_size: int
    methods: tuple[str, ...] = (
        "report",
        "aggregate_by_timestamp_side",
        "stable_within_ms",
    )


def _as_trades(trades: Sequence[Trade] | Iterable[Trade]) -> list[Trade]:
    return list(trades)


def report_ties(trades: Sequence[Trade]) -> TieReport:
    """Count same-millisecond collisions without changing the tape."""
    rows = _as_trades(trades)
    by_ts: dict[int, int] = defaultdict(int)
    for trade in rows:
        by_ts[trade.ts_ms] += 1
    tied_ts = [n for n in by_ts.values() if n > 1]
    tied_trades = sum(n for n in tied_ts)
    return TieReport(
        n_trades=len(rows),
        n_timestamps=len(by_ts),
        n_tied_timestamps=len(tied_ts),
        n_tied_trades=tied_trades,
        max_tie_size=max(tied_ts, default=0),
    )


def stable_within_ms(trades: Sequence[Trade]) -> list[Trade]:
    """Sort by ``(ts_ms, trade_id)``. Does not drop or jitter prints."""
    return sorted(_as_trades(trades), key=lambda t: (t.ts_ms, t.trade_id))


def aggregate_by_timestamp_side(trades: Sequence[Trade]) -> list[Trade]:
    """One event per (millisecond, aggressor side).

    Size is summed. Price is size-weighted. ``trade_id`` is the first id in
    exchange order. Use this when a simple Hawkes process cannot have ties.
    """
    groups: dict[tuple[int, str], list[Trade]] = defaultdict(list)
    for trade in stable_within_ms(trades):
        groups[(trade.ts_ms, trade.side)].append(trade)
    out: list[Trade] = []
    for (_ts, _side), bucket in groups.items():
        total = sum(t.size for t in bucket)
        if total > 0:
            price = sum(t.price * t.size for t in bucket) / total
        else:
            price = bucket[0].price
        first = bucket[0]
        out.append(
            Trade(
                inst_id=first.inst_id,
                trade_id=first.trade_id,
                price=price,
                size=total,
                side=first.side,
                ts_ms=first.ts_ms,
                source=first.source,
            )
        )
    out.sort(key=lambda t: (t.ts_ms, t.trade_id))
    return out


def handle_ties(
    trades: Sequence[Trade],
    method: TieMethod = "report",
) -> tuple[list[Trade], TieReport]:
    """Report ties and optionally collapse them. Default does not mutate.

    ``method``:
    - ``report``: leave prints as they are (still sorted stably for output).
    - ``stable_within_ms``: sort by trade id inside a millisecond.
    - ``aggregate_by_timestamp_side``: one event per (ts, side).
    """
    report = report_ties(trades)
    if method == "report":
        return stable_within_ms(trades), report
    if method == "stable_within_ms":
        return stable_within_ms(trades), report
    if method == "aggregate_by_timestamp_side":
        return aggregate_by_timestamp_side(trades), report
    raise ValueError(
        f"unknown method {method!r}; jitter is not available (it biases Hawkes kernels)"
    )


__all__ = [
    "TieReport",
    "aggregate_by_timestamp_side",
    "handle_ties",
    "report_ties",
    "stable_within_ms",
    "trades_to_array",
]
