"""Public v5 paths, site base URLs, and documented rate limits.

Stage 1 allowlists GET-only, keyless endpoints. Private, account, order,
and withdrawal paths are not present and are rejected by the client.

Rate limits come from the per-endpoint tables in the OKX v5 docs
(https://www.okx.com/docs-v5/en/), cross-checked against the public-GET
costs in CCXT's OKX adapter (cost 1 = 20 requests / 2 s). Public REST
limits are per IP.
"""

from __future__ import annotations

import os
import re
from urllib.parse import urlparse, urlunparse

SITE_BASE_URLS: dict[str, str] = {
    "global": "https://www.okx.com",
    "eea": "https://eea.okx.com",
    "us": "https://us.okx.com",
}

# Documented REST/CDN hosts from the v5 docs (regional domains + static CDN).
ALLOWED_HOSTS: frozenset[str] = frozenset(
    {
        "www.okx.com",
        "openapi.okx.com",
        "eea.okx.com",
        "us.okx.com",
        "my.okx.com",
        "app.okx.com",
        "static.okx.com",
        "aws.okx.com",
        "ws.okx.com",
        "wsaws.okx.com",
        "wspap.okx.com",
        "wseea.okx.com",
        "wsus.okx.com",
        "tr.okx.com",
        "okx.com",
    }
)

# Public / business WebSocket hosts (production, AWS, demo, regional).
ALLOWED_WS_HOSTS: frozenset[str] = frozenset(
    {
        "ws.okx.com",
        "wsaws.okx.com",
        "wspap.okx.com",
        "wseea.okx.com",
        "wsus.okx.com",
    }
)

DEFAULT_PUBLIC_WS_URL = "wss://ws.okx.com:8443/ws/v5/public"
DEFAULT_BUSINESS_WS_URL = "wss://ws.okx.com:8443/ws/v5/business"
PUBLIC_WS_URL_ENV = "OKX_WS_PUBLIC_URL"
BUSINESS_WS_URL_ENV = "OKX_WS_BUSINESS_URL"

PUBLIC_WS_PATHS: frozenset[str] = frozenset({"/ws/v5/public", "/ws/v5/business"})
PRIVATE_WS_PATH = "/ws/v5/private"

# Public, keyless channels this package will subscribe to.
PUBLIC_WS_CHANNELS: frozenset[str] = frozenset(
    {
        "trades",
        "tickers",
        "books",
        "books5",
        "bbo-tbt",
        "funding-rate",
        "mark-price",
    }
)

CANDLE_CHANNEL_RE = re.compile(
    r"^candle(?:1s|3m|1m|5m|15m|30m|1H|2H|4H|6H|12H|1D|2D|3D|5D|1W|1M|3M)(?:utc)?$"
)

# Names we refuse with a private-channel error even if someone asks.
PRIVATE_WS_CHANNELS: frozenset[str] = frozenset(
    {
        "account",
        "positions",
        "orders",
        "orders-algo",
        "orders-algo-advance",
        "balance_and_position",
        "balance-and-position",
        "account-greeks",
        "liquidation-warning",
        "account-risk-data",
        "deposit-info",
        "withdrawal-info",
        "grid-orders-spot",
        "grid-orders-contract",
        "grid-orders-moon",
        "algo-advance",
        "price-limit",
        "fills",
    }
)

# Connection attempts per IP, subscribe/unsubscribe/login ops per connection.
WS_CONNECT_RATE: tuple[int, float] = (3, 1.0)
WS_SUBSCRIBE_RATE: tuple[int, float] = (480, 3600.0)

# Path-safe instrument ids. Hyphen *and* underscore show up on live FUTURES
# (e.g. BTC-USD_UM-261030). Still rejects '/', '\\', '.', and empty strings.
INST_ID_RE = re.compile(r"^[A-Z0-9]+(?:[-_][A-Z0-9]+)*$")

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


def validate_inst_id(inst_id: str) -> str:
    if not inst_id or not INST_ID_RE.fullmatch(inst_id):
        raise ValueError(
            f"invalid inst_id {inst_id!r}; expected pattern {INST_ID_RE.pattern}"
        )
    return inst_id


def assert_okx_url(url: str, *, allow_unofficial_hosts: bool = False) -> None:
    """Require https and an official OKX host, unless tests opt out."""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(f"refusing non-https URL {url!r}")
    host = (parsed.hostname or "").lower()
    if allow_unofficial_hosts:
        return
    if host not in ALLOWED_HOSTS:
        raise ValueError(
            f"refusing host {host!r}; official OKX hosts are {sorted(ALLOWED_HOSTS)}"
        )


def assert_okx_ws_url(url: str, *, allow_unofficial_hosts: bool = False) -> None:
    """Require wss and a documented OKX public/business WebSocket host."""
    from tools.okx.errors import UnsafeEndpointError

    parsed = urlparse(url)
    path = (parsed.path or "").rstrip("/") or "/"
    if PRIVATE_WS_PATH in path or path.endswith("/private"):
        raise UnsafeEndpointError(f"refusing private WebSocket path {path}")
    if allow_unofficial_hosts:
        if parsed.scheme not in {"ws", "wss"}:
            raise ValueError(f"refusing non-ws URL {url!r}")
        return
    if parsed.scheme != "wss":
        raise ValueError(f"refusing non-wss URL {url!r}")
    host = (parsed.hostname or "").lower()
    if host not in ALLOWED_WS_HOSTS:
        raise ValueError(
            f"refusing host {host!r}; official OKX WebSocket hosts are "
            f"{sorted(ALLOWED_WS_HOSTS)}"
        )
    if path not in PUBLIC_WS_PATHS:
        raise UnsafeEndpointError(f"refusing WebSocket path {path}")


def is_candle_channel(channel: str) -> bool:
    return bool(CANDLE_CHANNEL_RE.fullmatch(channel))


def is_public_ws_channel(channel: str) -> bool:
    return channel in PUBLIC_WS_CHANNELS or is_candle_channel(channel)


def resolve_ws_url(
    explicit: str | None,
    *,
    env_name: str,
    default: str,
    allow_unofficial_hosts: bool = False,
) -> str:
    """Constructor arg, then env, then default. Env cannot enable unofficial hosts."""
    if explicit is not None and str(explicit).strip():
        url = str(explicit).strip()
    else:
        url = os.environ.get(env_name, "").strip() or default
    assert_okx_ws_url(url, allow_unofficial_hosts=allow_unofficial_hosts)
    return url


def ws_url_on_port(url: str, port: int) -> str:
    """Return ``url`` with only the port changed. Host, path, and scheme stay."""
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        raise ValueError(f"WebSocket URL has no host: {url!r}")
    userinfo = ""
    if parsed.username:
        userinfo = parsed.username
        if parsed.password:
            userinfo += f":{parsed.password}"
        userinfo += "@"
    host_part = f"[{host}]" if ":" in host else host
    candidate = urlunparse(parsed._replace(netloc=f"{userinfo}{host_part}:{int(port)}"))
    assert_same_ws_host_path(url, candidate)
    return candidate


def assert_same_ws_host_path(original: str, candidate: str) -> None:
    """Refuse a fallback candidate that changes scheme, host, or path."""
    left = urlparse(original)
    right = urlparse(candidate)
    if (left.scheme or "") != (right.scheme or ""):
        raise ValueError("WebSocket fallback must not change scheme")
    if (left.hostname or "").lower() != (right.hostname or "").lower():
        raise ValueError("WebSocket fallback must not change host")
    if (left.path or "") != (right.path or ""):
        raise ValueError("WebSocket fallback must not change path")


def fallback_ws_port_443(url: str, *, allow_unofficial_hosts: bool = False) -> str | None:
    """If ``url`` uses port 8443, return the same host/path on port 443.

    Re-validates the allowlist. Returns ``None`` when the URL is not on 8443.
    """
    parsed = urlparse(url)
    if parsed.port != 8443:
        return None
    candidate = ws_url_on_port(url, 443)
    assert_same_ws_host_path(url, candidate)
    if urlparse(candidate).port != 443:
        raise ValueError("WebSocket fallback may only switch to port 443")
    assert_okx_ws_url(candidate, allow_unofficial_hosts=allow_unofficial_hosts)
    return candidate
