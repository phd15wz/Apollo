"""Public v5 paths, site base URLs, and documented rate limits.

Stage 1 allowlists GET-only, keyless endpoints. Private, account, order,
and withdrawal paths are not present and are rejected by the client.

Rate limits come from the per-endpoint tables in the OKX v5 docs
(https://www.okx.com/docs-v5/en/), cross-checked against the public-GET
costs in CCXT's OKX adapter (cost 1 = 20 requests / 2 s). Public REST
limits are per IP.
"""

from __future__ import annotations

SITE_BASE_URLS: dict[str, str] = {
    "global": "https://www.okx.com",
    "eea": "https://eea.okx.com",
    "us": "https://us.okx.com",
}

# Optional alias; docs still treat www.okx.com as valid for global REST.
GLOBAL_OPENAPI = "https://openapi.okx.com"

INSTRUMENTS = "/api/v5/public/instruments"
FUNDING_RATE = "/api/v5/public/funding-rate"
FUNDING_RATE_HISTORY = "/api/v5/public/funding-rate-history"
OPEN_INTEREST = "/api/v5/public/open-interest"
MARK_PRICE = "/api/v5/public/mark-price"
MARKET_DATA_HISTORY = "/api/v5/public/market-data-history"
SERVER_TIME = "/api/v5/public/time"

TICKERS = "/api/v5/market/tickers"
TICKER = "/api/v5/market/ticker"
BOOKS = "/api/v5/market/books"
CANDLES = "/api/v5/market/candles"
HISTORY_CANDLES = "/api/v5/market/history-candles"
TRADES = "/api/v5/market/trades"
HISTORY_TRADES = "/api/v5/market/history-trades"
INDEX_TICKERS = "/api/v5/market/index-tickers"

SYSTEM_STATUS = "/api/v5/system/status"
ANNOUNCEMENTS = "/api/v5/support/announcements"
ANNOUNCEMENT_TYPES = "/api/v5/support/announcement-types"

# Official bulk listing. Each row's ``url`` points at the public CDN:
# https://static.okx.com/cdn/okex/traderecords/...
# Landing page: https://www.okx.com/historical-data
# Docs: https://www.okx.com/docs-v5/en/#public-data-rest-api-get-historical-market-data
HISTORICAL_DATA_LANDING = "https://www.okx.com/historical-data"
HISTORICAL_DATA_DOCS = (
    "https://www.okx.com/docs-v5/en/#public-data-rest-api-get-historical-market-data"
)
HISTORICAL_DATA_TERMS = "https://www.okx.com/en-us/help/historicaldata-terms-and-conditions"
CDN_HOST_SUFFIX = "static.okx.com"

# module=1 is tick-by-tick trade history on Get historical market data.
ARCHIVE_MODULE_TRADES = "1"
# Changelog 2026: daily listing windows are at most 10 days.
ARCHIVE_DAILY_WINDOW_DAYS = 10

ALLOWED_GET: frozenset[str] = frozenset(
    {
        INSTRUMENTS,
        FUNDING_RATE,
        FUNDING_RATE_HISTORY,
        OPEN_INTEREST,
        MARK_PRICE,
        MARKET_DATA_HISTORY,
        SERVER_TIME,
        TICKERS,
        TICKER,
        BOOKS,
        CANDLES,
        HISTORY_CANDLES,
        TRADES,
        HISTORY_TRADES,
        INDEX_TICKERS,
        SYSTEM_STATUS,
        ANNOUNCEMENTS,
        ANNOUNCEMENT_TYPES,
    }
)

# (max_requests, window_seconds)
RATE_LIMITS: dict[str, tuple[int, float]] = {
    TICKERS: (20, 2.0),
    TICKER: (20, 2.0),
    BOOKS: (40, 2.0),
    CANDLES: (40, 2.0),
    HISTORY_CANDLES: (20, 2.0),
    TRADES: (100, 2.0),
    HISTORY_TRADES: (10, 2.0),
    INDEX_TICKERS: (20, 2.0),
    INSTRUMENTS: (20, 2.0),
    OPEN_INTEREST: (20, 2.0),
    FUNDING_RATE: (10, 2.0),
    FUNDING_RATE_HISTORY: (10, 2.0),
    MARK_PRICE: (10, 2.0),
    MARKET_DATA_HISTORY: (5, 2.0),
    SERVER_TIME: (10, 2.0),
    SYSTEM_STATUS: (1, 5.0),
    ANNOUNCEMENTS: (5, 2.0),
    ANNOUNCEMENT_TYPES: (1, 2.0),
}

DEFAULT_RATE_LIMIT: tuple[int, float] = (10, 2.0)

# Anything matching these prefixes is trading/account/funding and is refused.
BLOCKED_PREFIXES: tuple[str, ...] = (
    "/api/v5/account",
    "/api/v5/trade",
    "/api/v5/asset",
    "/api/v5/users",
    "/api/v5/broker",
    "/api/v5/rfq",
    "/api/v5/copytrading",
    "/api/v5/tradingBot",
    "/api/v5/finance",
)
