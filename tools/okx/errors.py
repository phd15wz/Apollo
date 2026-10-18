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
    """Paged through ``max_pages`` without reaching the start of the window."""

    def __init__(self, message: str, *, pages: int, max_pages: int) -> None:
        self.pages = pages
        self.max_pages = max_pages
        super().__init__(message)
