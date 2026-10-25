from __future__ import annotations

import inspect
import json

import pytest

from tools.okx.client import HttpResponse
from tools.okx.errors import HistoryWindowTooOld, PaginationBudgetExceeded
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
    assert instruments[0].min_sz == "0.01"
    assert instruments[0].lot_sz == "0.01"
    assert instruments[0].ct_val == "0.01"

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
        now_ms=1700000035000,
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
        now_ms=end_ts,
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
            now_ms=_SEQ_TS0 + 399,
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


def test_window_end_uses_highest_trade_id_not_page_order() -> None:
    """Same-timestamp trades returned oldest-first must still pick the max id."""
    end_ts = _SEQ_TS0 + 20
    rows = [
        {
            "instId": "BTC-USDT-SWAP",
            "side": "buy",
            "sz": "1",
            "px": "100",
            "tradeId": "10",
            "ts": str(end_ts),
            "source": "0",
        },
        {
            "instId": "BTC-USDT-SWAP",
            "side": "buy",
            "sz": "1",
            "px": "100",
            "tradeId": "12",
            "ts": str(end_ts),
            "source": "0",
        },
        {
            "instId": "BTC-USDT-SWAP",
            "side": "buy",
            "sz": "1",
            "px": "100",
            "tradeId": "11",
            "ts": str(end_ts),
            "source": "0",
        },
        {
            "instId": "BTC-USDT-SWAP",
            "side": "sell",
            "sz": "1",
            "px": "99",
            "tradeId": "9",
            "ts": str(end_ts - 1),
            "source": "0",
        },
    ]

    class _OldestFirstTape:
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
            page_type = query.get("type", "1")
            after = query.get("after")
            if page_type == "2":
                page = [rows[0], rows[2], rows[1], rows[3]]  # ids 10, 11, 12, 9
            elif after == "13":
                page = [rows[1], rows[2], rows[0], rows[3]]  # 12, 11, 10, 9
            else:
                page = []
            return HttpResponse(200, {}, json.dumps({"code": "0", "data": page}).encode(), url)

    client = make_client(_OldestFirstTape())  # type: ignore[arg-type]
    trades = history_trades_range(
        client,
        "BTC-USDT-SWAP",
        start_ts_ms=end_ts - 1,
        end_ts_ms=end_ts,
        page_limit=4,
        now_ms=end_ts,
    )
    assert [t.trade_id for t in trades] == [9, 10, 11, 12]


def test_history_window_older_than_three_months_raises() -> None:
    transport = FakeTransport()
    client = make_client(transport)
    now = 1_800_000_000_000
    end = now - (100 * 24 * 60 * 60 * 1000)
    with pytest.raises(HistoryWindowTooOld, match="market-data-history") as exc:
        history_trades_range(
            client,
            "BTC-USDT-SWAP",
            start_ts_ms=end - 1000,
            end_ts_ms=end,
            now_ms=now,
        )
    assert exc.value.end_ts_ms == end
    assert transport.calls == []


def test_history_window_start_beyond_three_months_warns() -> None:
    from tools.okx.errors import HistoryWindowTooOldWarning

    transport = FakeTransport()
    transport.add("/api/v5/market/history-trades", {"code": "0", "data": []})
    client = make_client(transport)
    now = 1_800_000_000_000
    start = now - (120 * 24 * 60 * 60 * 1000)
    end = now - (10 * 24 * 60 * 60 * 1000)
    with pytest.warns(HistoryWindowTooOldWarning, match="market-data-history"):
        history_trades_range(
            client,
            "BTC-USDT-SWAP",
            start_ts_ms=start,
            end_ts_ms=end,
            now_ms=now,
            pagination="ts",
        )


def test_history_range_cancel_stops_paging() -> None:
    import threading
    import time

    from tools.okx.client import HttpResponse

    ev = threading.Event()

    class SlowTape:
        def __init__(self) -> None:
            self.n = 0
            self.next_id = 80

        def request(self, method, url, *, params, headers, timeout):
            self.n += 1
            time.sleep(0.15)
            tid = self.next_id
            self.next_id -= 1
            body = json.dumps(
                {
                    "code": "0",
                    "data": [
                        {
                            "instId": "BTC-USDT-SWAP",
                            "tradeId": str(tid),
                            "px": "1",
                            "sz": "1",
                            "side": "buy",
                            "ts": str(1_700_000_000_000 + tid),
                        }
                    ],
                }
            ).encode()
            return HttpResponse(200, {}, body, url)

    client = make_client(SlowTape())  # type: ignore[arg-type]
    result: dict[str, object] = {}

    def run() -> None:
        try:
            history_trades_range(
                client,
                "BTC-USDT-SWAP",
                start_trade_id=1,
                end_trade_id=80,
                page_limit=1,
                max_pages=40,
                should_cancel=ev.is_set,
            )
            result["ok"] = True
        except InterruptedError:
            result["cancelled"] = True

    thread = threading.Thread(target=run)
    thread.start()
    time.sleep(0.2)
    ev.set()
    thread.join(timeout=0.8)
    assert not thread.is_alive()
    assert result.get("cancelled") is True
    assert client._transport.n <= 4  # type: ignore[attr-defined]


def test_empty_window_end_lookup_raises_archive_hint() -> None:
    transport = FakeTransport()
    transport.add(
        "/api/v5/market/history-trades",
        {"code": "0", "data": []},
        params={
            "instId": "BTC-USDT-SWAP",
            "type": "2",
            "after": "1700000000001",
            "limit": "100",
        },
    )
    client = make_client(transport)
    with pytest.raises(HistoryWindowTooOld, match="market-data-history"):
        history_trades_range(
            client,
            "BTC-USDT-SWAP",
            end_ts_ms=1_700_000_000_000,
            now_ms=1_700_000_000_000,
        )


def test_page_budget_without_start_keeps_partial_trades() -> None:
    tape = _OkxHistoryTape(_sequential_rows(80))
    client = make_client(tape)  # type: ignore[arg-type]
    with pytest.raises(PaginationBudgetExceeded) as exc:
        history_trades_range(
            client,
            "BTC-USDT-SWAP",
            page_limit=10,
            max_pages=3,
        )
    ids = [t.trade_id for t in exc.value.trades]
    assert ids == list(range(51, 81))
    assert exc.value.pages == 3
    assert len(exc.value.trades) == 30
