from __future__ import annotations

import inspect

import pytest

from tools.okx.hygiene import (
    aggregate_by_timestamp_side,
    handle_ties,
    report_ties,
    stable_within_ms,
)
from tools.okx.types import Trade


def _t(trade_id: int, ts_ms: int, side: str, price: float, size: float) -> Trade:
    return Trade(
        inst_id="BTC-USDT-SWAP",
        trade_id=trade_id,
        price=price,
        size=size,
        side=side,
        ts_ms=ts_ms,
    )


SAMPLE = [
    _t(3, 1_700_000_000_002, "sell", 101.0, 2.0),
    _t(1, 1_700_000_000_001, "buy", 100.0, 1.0),
    _t(2, 1_700_000_000_002, "buy", 100.5, 1.0),
    _t(4, 1_700_000_000_002, "buy", 100.0, 3.0),
]


def test_report_ties_counts_collisions() -> None:
    report = report_ties(SAMPLE)
    assert report.n_trades == 4
    assert report.n_timestamps == 2
    assert report.n_tied_timestamps == 1
    assert report.n_tied_trades == 3
    assert report.max_tie_size == 3


def test_stable_within_ms_uses_trade_id_not_arrival_order() -> None:
    ordered = stable_within_ms(SAMPLE)
    assert [t.trade_id for t in ordered] == [1, 2, 3, 4]
    assert [t.ts_ms for t in ordered] == [
        1_700_000_000_001,
        1_700_000_000_002,
        1_700_000_000_002,
        1_700_000_000_002,
    ]


def test_aggregate_by_timestamp_side() -> None:
    collapsed, report = handle_ties(SAMPLE, method="aggregate_by_timestamp_side")
    assert report.n_tied_timestamps == 1
    assert [t.trade_id for t in collapsed] == [1, 2, 3]
    buy = next(t for t in collapsed if t.ts_ms == 1_700_000_000_002 and t.side == "buy")
    assert buy.size == 4.0
    assert buy.price == pytest.approx((100.5 * 1 + 100.0 * 3) / 4)
    assert buy.trade_id == 2


def test_default_handle_ties_does_not_drop_prints() -> None:
    out, report = handle_ties(SAMPLE)
    assert len(out) == 4
    assert report.n_trades == 4


def test_no_jitter_api() -> None:
    source = inspect.getsource(handle_ties)
    assert "jitter" in source.lower()
    with pytest.raises(ValueError, match="jitter is not available"):
        handle_ties(SAMPLE, method="jitter")  # type: ignore[arg-type]
    assert all(t.ts_ms in {1_700_000_000_001, 1_700_000_000_002} for t in stable_within_ms(SAMPLE))
