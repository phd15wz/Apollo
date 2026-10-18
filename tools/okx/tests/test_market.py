from __future__ import annotations

import inspect
import json

import pytest

from tools.okx.client import HttpResponse
from tools.okx.errors import PaginationBudgetExceeded
from tools.okx.market import candles_range, funding_rate_history_range, history_trades_range
from tools.okx.tests.conftest import FakeTransport, load_fixture, make_client
from tools.okx.types import TRADE_DTYPE, trades_to_array


def test_instruments_ticker_book_trades() -> None:
    transport = FakeTransport()
    transport.add("/api/v5/public/instruments", load_fixture("instruments.json"))
    transport.add("/api/v5/market/ticker", load_fixture("ticker.json"))
    transport.add("/api/v5/market/tickers", load_fixture("tickers.json"))
    transport.add("/api/v5/market/books", load_fixture("books.json"))
    transport.add("/api/v5/market/trades", load_fixture("trades.json"))
    client = make_client(transport)

    instruments = client.instruments("SWAP")
    assert instruments[0].inst_id == "BTC-USDT-SWAP"
    assert instruments[0].inst_family == "BTC-USDT"
    assert instruments[0].state == "live"

    ticker = client.ticker("BTC-USDT-SWAP")
    assert ticker.bid_px == 8888.88

    tickers = client.tickers("SWAP")
    assert {row.inst_id for row in tickers} == {"LTC-USD-SWAP", "BTC-USD-SWAP"}

    book = client.order_book("BTC-USDT-SWAP", depth=1)
    assert book.asks[0].price == 41006.8
    assert book.bids[0].order_count == 2
    assert book.seq_id == 3235851742

    trades = client.trades("BTC-USDT-SWAP", limit=2)
    assert trades[0].trade_id == 242720720
    assert trades[0].ts_ms == 1654161646974
    array = trades_to_array(trades)
    assert array.dtype == TRADE_DTYPE
    assert array[0]["side"] == "sell"


def test_funding_oi_mark_index() -> None:
    transport = FakeTransport()
    transport.add("/api/v5/public/funding-rate", load_fixture("funding_rate.json"))
    transport.add("/api/v5/public/funding-rate-history", load_fixture("funding_rate_history.json"))
    transport.add("/api/v5/public/open-interest", load_fixture("open_interest.json"))
    transport.add("/api/v5/public/mark-price", load_fixture("mark_price.json"))
    transport.add("/api/v5/market/index-tickers", load_fixture("index_tickers.json"))
    client = make_client(transport)

    rate = client.funding_rate("BTC-USDT-SWAP")
    assert rate.funding_rate == 0.0001
    assert rate.next_funding_time_ms == 1700028800000

    history = client.funding_rate_history("BTC-USDT-SWAP")
    assert [row.funding_time_ms for row in history] == [1700028800000, 1700000000000]

    oi = client.open_interest("SWAP", inst_id="BTC-USDT-SWAP")
    assert oi[0].oi == 12345

    mark = client.mark_price("SWAP", inst_id="BTC-USDT-SWAP")
    assert mark[0].mark_px == 67000.1

    index = client.index_tickers(inst_id="BTC-USDT")
    assert index[0].idx_px == 67001.2


def test_history_trades_pagination_by_ts_returns_chronological() -> None:
    transport = FakeTransport()
    transport.add(
        "/api/v5/market/history-trades",
        load_fixture("history_trades_p1.json"),
        params={"instId": "BTC-USDT-SWAP", "type": "2", "after": "1700000035001", "limit": "2"},
        once=True,
    )
    transport.add(
        "/api/v5/market/history-trades",
        load_fixture("history_trades_p2.json"),
        params={"instId": "BTC-USDT-SWAP", "type": "2", "after": "1700000020000", "limit": "2"},
        once=True,
    )
    client = make_client(transport)
    trades = history_trades_range(
        client,
        "BTC-USDT-SWAP",
        start_ts_ms=1700000010000,
        end_ts_ms=1700000035000,
        pagination="ts",
        page_limit=2,
    )
    assert [t.trade_id for t in trades] == [10, 20, 30]
    assert [t.ts_ms for t in trades] == [1700000010000, 1700000020000, 1700000030000]
    assert all("before" not in call["params"] for call in transport.calls)


def test_history_trades_pagination_by_trade_id() -> None:
    transport = FakeTransport()
    transport.add(
        "/api/v5/market/history-trades",
        load_fixture("history_trades_p1.json"),
        params={"instId": "BTC-USDT-SWAP", "type": "1", "after": "31", "limit": "2"},
        once=True,
    )
    transport.add(
        "/api/v5/market/history-trades",
        load_fixture("history_trades_p2.json"),
        params={"instId": "BTC-USDT-SWAP", "type": "1", "after": "20", "limit": "2"},
        once=True,
    )
    client = make_client(transport)
    trades = history_trades_range(
        client,
        "BTC-USDT-SWAP",
        start_trade_id=10,
        end_trade_id=30,
        page_limit=2,
    )
    assert [t.trade_id for t in trades] == [10, 20, 30]
    assert inspect.signature(history_trades_range).parameters["pagination"].default == "tradeId"


def test_candles_and_range_helper() -> None:
    transport = FakeTransport()
    page1 = {
        "code": "0",
        "data": [
            ["1700000030000", "4", "5", "3", "4", "1", "1", "1", "1"],
            ["1700000020000", "3.731", "3.799", "3.494", "3.72", "10", "20", "30", "1"],
        ],
    }
    page2 = {
        "code": "0",
        "data": [
            ["1700000010000", "3.721", "3.743", "3.677", "3.708", "8", "16", "24", "1"],
        ],
    }
    transport.add(
        "/api/v5/market/history-candles",
        page1,
        params={
            "instId": "BTC-USDT-SWAP",
            "bar": "1m",
            "after": "1700000030001",
            "limit": "2",
        },
        once=True,
    )
    transport.add(
        "/api/v5/market/history-candles",
        page2,
        params={
            "instId": "BTC-USDT-SWAP",
            "bar": "1m",
            "after": "1700000020000",
            "limit": "2",
        },
        once=True,
    )
    transport.add("/api/v5/market/candles", load_fixture("candles.json"))
    client = make_client(transport)
    recent = client.candles("BTC-USDT-SWAP", bar="1m")
    assert recent[0].ts_ms > recent[1].ts_ms
    candles = candles_range(
        client,
        "BTC-USDT-SWAP",
        start_ts_ms=1700000010000,
        end_ts_ms=1700000030000,
        bar="1m",
        historical=True,
        page_limit=2,
    )
    assert [c.ts_ms for c in candles] == [1700000010000, 1700000020000, 1700000030000]
    assert candles[0].confirm is True


def test_funding_history_range() -> None:
    transport = FakeTransport()
    transport.add("/api/v5/public/funding-rate-history", load_fixture("funding_rate_history.json"))
    client = make_client(transport)
    rows = funding_rate_history_range(
        client,
        "BTC-USDT-SWAP",
        start_ts_ms=1700000000000,
        end_ts_ms=1700028800000,
    )
    assert [r.funding_time_ms for r in rows] == [1700000000000, 1700028800000]


class _OkxHistoryTape:
    """Mimic OKX ``after``: exclusive tradeId (type=1) or exclusive ts (type=2)."""

    def __init__(self, rows: list[dict[str, str]]) -> None:
        self.rows = sorted(rows, key=lambda row: int(row["tradeId"]), reverse=True)
        self.calls: list[dict[str, str]] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, str] | None,
        headers: dict[str, str],
        timeout: float,
    ) -> HttpResponse:
        query = dict(params or {})
        self.calls.append(query)
        after = query.get("after")
        limit = int(query.get("limit") or 100)
        page_type = query.get("type", "1")
        rows = self.rows
        if after:
            if page_type == "1":
                rows = [row for row in rows if int(row["tradeId"]) < int(after)]
            else:
                rows = [row for row in rows if int(row["ts"]) < int(after)]
        page = rows[:limit]
        return HttpResponse(200, {}, json.dumps({"code": "0", "data": page}).encode(), url)


def _burst_rows() -> list[dict[str, str]]:
    # Newest-first on the wire: ids 14..10, all but id 9 share one millisecond.
    rows = []
    for trade_id in range(10, 15):
        rows.append(
            {
                "instId": "BTC-USDT-SWAP",
                "side": "buy",
                "sz": "1",
                "px": "100",
                "tradeId": str(trade_id),
                "ts": "1700000001000",
                "source": "0",
            }
        )
    rows.append(
        {
            "instId": "BTC-USDT-SWAP",
            "side": "sell",
            "sz": "1",
            "px": "99",
            "tradeId": "9",
            "ts": "1700000000999",
            "source": "0",
        }
    )
    return rows


def test_trade_id_paging_keeps_ties_across_page_boundary() -> None:
    tape = _OkxHistoryTape(_burst_rows())
    client = make_client(tape)  # type: ignore[arg-type]
    trades = history_trades_range(client, "BTC-USDT-SWAP", page_limit=2)
    ids = [t.trade_id for t in trades]
    assert ids == [9, 10, 11, 12, 13, 14]
    assert len(ids) == len(set(ids))
    assert all(call.get("type") == "1" for call in tape.calls)
    assert all("before" not in call for call in tape.calls)


def test_timestamp_paging_is_lossy_at_ms_boundary() -> None:
    tape = _OkxHistoryTape(_burst_rows())
    client = make_client(tape)  # type: ignore[arg-type]
    trades = history_trades_range(
        client,
        "BTC-USDT-SWAP",
        pagination="ts",
        page_limit=2,
    )
    ids = [t.trade_id for t in trades]
    assert 14 in ids and 13 in ids
    assert ids != [9, 10, 11, 12, 13, 14]
    assert 10 not in ids or 11 not in ids or 12 not in ids
    assert all(call.get("type") == "2" for call in tape.calls)
    assert all("before" not in call for call in tape.calls)


def test_timestamp_mode_refuses_before_parameter() -> None:
    client = make_client(FakeTransport())
    with pytest.raises(ValueError, match="before"):
        client.history_trades("BTC-USDT-SWAP", pagination="ts", before="1")


_SEQ_TS0 = 1_700_000_000_000


def _sequential_rows(count: int, *, start_id: int = 1) -> list[dict[str, str]]:
    rows = []
    for offset in range(count):
        trade_id = start_id + offset
        rows.append(
            {
                "instId": "BTC-USDT-SWAP",
                "side": "buy",
                "sz": "1",
                "px": "100",
                "tradeId": str(trade_id),
                "ts": str(_SEQ_TS0 + offset),
                "source": "0",
            }
        )
    return rows


def test_trade_id_range_locates_past_window_behind_newer_trades() -> None:
    """A 100-trade window behind 250 newer trades used to return [] at max_pages=4."""
    tape = _OkxHistoryTape(_sequential_rows(400))
    client = make_client(tape)  # type: ignore[arg-type]
    start_ts = _SEQ_TS0 + 49  # id 50
    end_ts = _SEQ_TS0 + 149  # id 150
    trades = history_trades_range(
        client,
        "BTC-USDT-SWAP",
        start_ts_ms=start_ts,
        end_ts_ms=end_ts,
        page_limit=50,
        max_pages=4,
    )
    ids = [t.trade_id for t in trades]
    assert ids == list(range(50, 151))
    assert len(ids) == len(set(ids))
    assert tape.calls[0].get("type") == "2"
    assert tape.calls[0].get("after") == str(end_ts + 1)
    assert all(call.get("type") == "1" for call in tape.calls[1:])


def test_trade_id_range_raises_when_page_budget_exhausted() -> None:
    tape = _OkxHistoryTape(_sequential_rows(400))
    client = make_client(tape)  # type: ignore[arg-type]
    with pytest.raises(PaginationBudgetExceeded, match="max_pages"):
        history_trades_range(
            client,
            "BTC-USDT-SWAP",
            start_ts_ms=_SEQ_TS0,
            end_ts_ms=_SEQ_TS0 + 399,
            page_limit=10,
            max_pages=5,
        )


def test_duplicate_trade_id_on_consecutive_pages_returned_once() -> None:
    rows = {
        int(row["tradeId"]): row
        for row in _sequential_rows(5)
    }

    class _OverlapTape:
        def request(
            self,
            method: str,
            url: str,
            *,
            params: dict[str, str] | None,
            headers: dict[str, str],
            timeout: float,
        ) -> HttpResponse:
            query = dict(params or {})
            after = query.get("after")
            if not after:
                page = [rows[5], rows[4], rows[3]]
            elif after == "3":
                page = [rows[3], rows[2], rows[1]]
            else:
                page = []
            return HttpResponse(200, {}, json.dumps({"code": "0", "data": page}).encode(), url)

    client = make_client(_OverlapTape())  # type: ignore[arg-type]
    trades = history_trades_range(client, "BTC-USDT-SWAP", page_limit=3)
    ids = [t.trade_id for t in trades]
    assert ids == [1, 2, 3, 4, 5]
    assert len(ids) == len(set(ids))
