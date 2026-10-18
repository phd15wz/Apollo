from __future__ import annotations

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
        pagination="tradeId",
        page_limit=2,
    )
    assert [t.trade_id for t in trades] == [10, 20, 30]


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
