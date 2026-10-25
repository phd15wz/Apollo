"""Errors for the OKX public client and the demo private client."""

from __future__ import annotations


class OkxError(Exception):
    """OKX returned a JSON body whose ``code`` is not ``'0'``."""

    def __init__(self, code: str, msg: str, payload: object | None = None) -> None:
        self.code = str(code)
        self.msg = msg
        self.payload = payload
        super().__init__(f"OKX error {self.code}: {msg}")


class OkxHttpError(Exception):
    """Non-success HTTP status from OKX or the historical-data CDN."""

    def __init__(self, status_code: int, url: str, body: str = "") -> None:
        self.status_code = status_code
        self.url = url
        self.body = body
        snippet = body[:300].replace("\n", " ")
        super().__init__(f"HTTP {status_code} for {url}: {snippet}")


class UnsafeEndpointError(Exception):
    """A path that is not on the stage-1 public allowlist was requested."""


class CacheBudgetExceeded(Exception):
    """A download would exceed the configured cache or per-run byte budget."""

    def __init__(self, message: str, *, used: int, budget: int, extra: int = 0) -> None:
        self.used = used
        self.budget = budget
        self.extra = extra
        super().__init__(message)


class UnsafeArchiveError(Exception):
    """Zip member, path, or compression limits were violated."""


class PaginationBudgetExceeded(Exception):
    """Paged through ``max_pages`` without reaching the start of the window.

    ``trades`` holds the records already fetched (chronological). Callers that
    asked for an open-ended window (no start) can keep that partial tape
    instead of discarding it.
    """

    def __init__(
        self,
        message: str,
        *,
        pages: int,
        max_pages: int,
        trades: list | None = None,
    ) -> None:
        self.pages = pages
        self.max_pages = max_pages
        self.trades = list(trades or [])
        super().__init__(message)


class HistoryWindowTooOldWarning(UserWarning):
    """REST history-trades will miss the older part of this window."""


class HistoryWindowTooOld(Exception):
    """REST ``/market/history-trades`` does not cover this window.

    OKX keeps about three months of trades on that endpoint. Older ticks
    must be loaded from the bulk archive (``GET /api/v5/public/market-data-history``
    / ``download_daily_trades``).
    """

    def __init__(
        self,
        message: str,
        *,
        start_ts_ms: int | None = None,
        end_ts_ms: int | None = None,
    ) -> None:
        self.start_ts_ms = start_ts_ms
        self.end_ts_ms = end_ts_ms
        super().__init__(message)


class PrivateChannelError(UnsafeEndpointError):
    """A login op or private WebSocket channel was requested."""


class LiveTradingDisabled(UnsafeEndpointError):
    """Live Trade is closed, or this live call is outside the two smokes.

    The smokes are BTC-USDT cash and one BTC-USDT-SWAP limit. Live reads
    remain balance, positions, and funding balances. Live place and cancel
    need ``OKX_LIVE_TRADE=1`` and still cannot withdraw or change leverage.
    """


class OrdersDisabled(UnsafeEndpointError):
    """A private write was refused because the client is read-only."""


class MissingCredentials(Exception):
    """Required OKX API environment variables are missing or empty.

    The message names the variables. It does not include their values.
    """

    def __init__(self, missing: list[str]) -> None:
        self.missing = list(missing)
        names = ", ".join(self.missing)
        super().__init__(f"missing OKX credentials in the environment: {names}")


class RiskLimitError(Exception):
    """An order would exceed a demo risk cap (per name, shared group, or gate)."""


class LowDiskError(Exception):
    """Free disk space on the cache filesystem is below the configured floor."""

    def __init__(self, message: str, *, free: int, floor: int, path: str = "") -> None:
        self.free = free
        self.floor = floor
        self.path = path
        super().__init__(message)
