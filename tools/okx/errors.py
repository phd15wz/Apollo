"""Errors for the keyless OKX public client."""

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


class LowDiskError(Exception):
    """Free disk space on the cache filesystem is below the configured floor."""

    def __init__(self, message: str, *, free: int, floor: int, path: str = "") -> None:
        self.free = free
        self.floor = floor
        self.path = path
        super().__init__(message)
