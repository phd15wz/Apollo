"""Keyless REST client for OKX public v5 endpoints.

No API keys, no signing, no private paths. GET only.
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import urljoin

import requests

from tools.okx import endpoints
from tools.okx.errors import OkxError, OkxHttpError, UnsafeEndpointError
from tools.okx.types import (
    Candle,
    FundingRate,
    IndexTicker,
    Instrument,
    MarkPrice,
    OpenInterest,
    OrderBook,
    Ticker,
    Trade,
)

Clock = Callable[[], float]
Sleeper = Callable[[float], None]


class Transport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None,
        headers: Mapping[str, str],
        timeout: float,
    ) -> "HttpResponse": ...


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes
    url: str = ""

    def json(self) -> Any:
        if not self.body:
            return {}
        return json.loads(self.body.decode("utf-8"))

    def header(self, name: str) -> str | None:
        lower = name.lower()
        for key, value in self.headers.items():
            if key.lower() == lower:
                return value
        return None


class RequestsTransport:
    def __init__(self, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()

    def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None,
        headers: Mapping[str, str],
        timeout: float,
    ) -> HttpResponse:
        response = self.session.request(
            method,
            url,
            params=params,
            headers=dict(headers),
            timeout=timeout,
        )
        return HttpResponse(
            status_code=response.status_code,
            headers=dict(response.headers),
            body=response.content,
            url=str(response.url),
        )


class RateLimiter:
    """Per-path sliding window that honours documented IP limits."""

    def __init__(
        self,
        limits: Mapping[str, tuple[int, float]] | None = None,
        *,
        clock: Clock | None = None,
        sleep: Sleeper | None = None,
    ) -> None:
        self._limits = dict(limits or endpoints.RATE_LIMITS)
        self._clock = clock or time.monotonic
        self._sleep = sleep or time.sleep
        self._hits: dict[str, list[float]] = {}

    def acquire(self, path: str) -> float:
        max_requests, window = self._limits.get(path, endpoints.DEFAULT_RATE_LIMIT)
        now = self._clock()
        stamps = self._hits.setdefault(path, [])
        cutoff = now - window
        del stamps[: next((i for i, ts in enumerate(stamps) if ts > cutoff), len(stamps))]
        waited = 0.0
        if len(stamps) >= max_requests:
            waited = max(0.0, stamps[0] + window - now)
            if waited > 0:
                self._sleep(waited)
            now = self._clock()
            cutoff = now - window
            del stamps[: next((i for i, ts in enumerate(stamps) if ts > cutoff), len(stamps))]
        stamps.append(now)
        return waited


def _clean_params(params: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not params:
        return None
    out: dict[str, Any] = {}
    for key, value in params.items():
        if value is None or value == "":
            continue
        out[key] = value
    return out or None


class PublicClient:
    """HTTP GET client for public OKX v5 data. Keys cannot be supplied."""

    def __init__(
        self,
        *,
        site: str = "global",
        base_url: str | None = None,
        timeout: float = 10.0,
        max_retries: int = 4,
        transport: Transport | None = None,
        limiter: RateLimiter | None = None,
        clock: Clock | None = None,
        sleep: Sleeper | None = None,
        user_agent: str = "apollo-okx-public/0.1",
        allow_unofficial_hosts: bool = False,
    ) -> None:
        if site not in endpoints.SITE_BASE_URLS:
            raise ValueError(f"unknown site {site!r}; expected one of {sorted(endpoints.SITE_BASE_URLS)}")
        self.site = site
        self.allow_unofficial_hosts = allow_unofficial_hosts
        self.base_url = (base_url or endpoints.SITE_BASE_URLS[site]).rstrip("/")
        endpoints.assert_okx_url(
            self.base_url + "/",
            allow_unofficial_hosts=allow_unofficial_hosts,
        )
        self.timeout = timeout
        self.max_retries = max_retries
        self._clock = clock or time.monotonic
        self._sleep = sleep or time.sleep
        self._transport = transport or RequestsTransport()
        self._limiter = limiter or RateLimiter(clock=self._clock, sleep=self._sleep)
        self._headers = {
            "Accept": "application/json",
            "Accept-Language": "en-US",
            "User-Agent": user_agent,
        }

    def get(self, path: str, params: Mapping[str, Any] | None = None) -> list[Any]:
        payload = self.get_payload(path, params)
        data = payload.get("data", [])
        if data is None:
            return []
        if not isinstance(data, list):
            raise OkxError(payload.get("code", "?"), "expected data array", payload)
        return data

    def get_payload(self, path: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        self._assert_public_get(path)
        url = urljoin(self.base_url + "/", path.lstrip("/"))
        query = _clean_params(params)
        last_error: Exception | None = None
        attempts = self.max_retries + 1
        for attempt in range(attempts):
            self._limiter.acquire(path)
            try:
                response = self._transport.request(
                    "GET",
                    url,
                    params=query,
                    headers=self._headers,
                    timeout=self.timeout,
                )
            except (requests.Timeout, requests.ConnectionError, TimeoutError, OSError) as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                self._backoff(attempt)
                continue
            if response.status_code in {429, 500, 502, 503, 504}:
                last_error = OkxHttpError(response.status_code, url, response.body.decode("utf-8", "replace"))
                if attempt >= self.max_retries:
                    raise last_error
                self._backoff(attempt, retry_after=response.header("Retry-After"))
                continue
            if response.status_code != 200:
                raise OkxHttpError(response.status_code, url, response.body.decode("utf-8", "replace"))
            try:
                payload = response.json()
            except json.JSONDecodeError as exc:
                raise OkxHttpError(response.status_code, url, "invalid JSON") from exc
            if not isinstance(payload, dict):
                raise OkxError("?", "response JSON was not an object", payload)
            code = str(payload.get("code", ""))
            if code == "50011":
                last_error = OkxError(code, str(payload.get("msg", "")), payload)
                if attempt >= self.max_retries:
                    raise last_error
                self._backoff(attempt)
                continue
            if code != "0":
                raise OkxError(code, str(payload.get("msg", "")), payload)
            return payload
        if last_error is not None:
            raise last_error
        raise OkxHttpError(0, url, "request failed")

    def _backoff(self, attempt: int, retry_after: str | None = None) -> None:
        if retry_after:
            try:
                delay = max(0.0, float(retry_after))
            except ValueError:
                delay = 0.5 * (2**attempt)
        else:
            delay = 0.5 * (2**attempt)
        delay = min(delay, 8.0)
        delay += random.uniform(0, 0.05)
        self._sleep(delay)

    def _assert_public_get(self, path: str) -> None:
        if not path.startswith("/"):
            path_norm = "/" + path
        else:
            path_norm = path
        if not path_norm.startswith("/api/v5/"):
            raise UnsafeEndpointError(f"refusing non-v5 path {path}")
        for prefix in endpoints.BLOCKED_PREFIXES:
            if path_norm.startswith(prefix):
                raise UnsafeEndpointError(f"refusing non-public path {path}")
        if path_norm not in endpoints.ALLOWED_GET:
            raise UnsafeEndpointError(f"path {path} is not on the stage-1 public allowlist")

    def instruments(self, inst_type: str, *, inst_id: str = "", inst_family: str = "") -> list[Instrument]:
        data = self.get(
            endpoints.INSTRUMENTS,
            {"instType": inst_type, "instId": inst_id, "instFamily": inst_family},
        )
        return [Instrument.from_dict(row) for row in data]

    def ticker(self, inst_id: str) -> Ticker:
        data = self.get(endpoints.TICKER, {"instId": inst_id})
        if not data:
            raise OkxError("0", f"no ticker for {inst_id}", data)
        return Ticker.from_dict(data[0])

    def tickers(self, inst_type: str, *, inst_family: str = "") -> list[Ticker]:
        data = self.get(endpoints.TICKERS, {"instType": inst_type, "instFamily": inst_family})
        return [Ticker.from_dict(row) for row in data]

    def order_book(self, inst_id: str, *, depth: int | None = None) -> OrderBook:
        params: dict[str, Any] = {"instId": inst_id}
        if depth is not None:
            params["sz"] = str(depth)
        data = self.get(endpoints.BOOKS, params)
        if not data:
            raise OkxError("0", f"no order book for {inst_id}", data)
        return OrderBook.from_dict(data[0], inst_id=inst_id)

    def trades(self, inst_id: str, *, limit: int | None = None) -> list[Trade]:
        params: dict[str, Any] = {"instId": inst_id}
        if limit is not None:
            params["limit"] = str(limit)
        return [Trade.from_dict(row) for row in self.get(endpoints.TRADES, params)]

    def history_trades(
        self,
        inst_id: str,
        *,
        pagination: str = "tradeId",
        after: str | int | None = None,
        before: str | int | None = None,
        limit: int | None = None,
    ) -> list[Trade]:
        if pagination not in {"tradeId", "ts"}:
            raise ValueError("pagination must be 'tradeId' or 'ts'")
        if pagination == "ts" and before is not None:
            raise ValueError(
                "timestamp pagination does not support 'before'; "
                "it is lossy at millisecond page boundaries unless you page by trade id"
            )
        page_type = "1" if pagination == "tradeId" else "2"
        params: dict[str, Any] = {"instId": inst_id, "type": page_type}
        if after is not None:
            params["after"] = str(after)
        if before is not None:
            params["before"] = str(before)
        if limit is not None:
            params["limit"] = str(limit)
        return [Trade.from_dict(row) for row in self.get(endpoints.HISTORY_TRADES, params)]

    def candles(
        self,
        inst_id: str,
        *,
        bar: str = "1m",
        after: str | int | None = None,
        before: str | int | None = None,
        limit: int | None = None,
        historical: bool = False,
    ) -> list[Candle]:
        path = endpoints.HISTORY_CANDLES if historical else endpoints.CANDLES
        params: dict[str, Any] = {"instId": inst_id, "bar": bar}
        if after is not None:
            params["after"] = str(after)
        if before is not None:
            params["before"] = str(before)
        if limit is not None:
            params["limit"] = str(limit)
        return [Candle.from_row(row) for row in self.get(path, params)]

    def funding_rate(self, inst_id: str) -> FundingRate:
        data = self.get(endpoints.FUNDING_RATE, {"instId": inst_id})
        if not data:
            raise OkxError("0", f"no funding rate for {inst_id}", data)
        return FundingRate.from_dict(data[0])

    def funding_rate_history(
        self,
        inst_id: str,
        *,
        after: str | int | None = None,
        before: str | int | None = None,
        limit: int | None = None,
    ) -> list[FundingRate]:
        params: dict[str, Any] = {"instId": inst_id}
        if after is not None:
            params["after"] = str(after)
        if before is not None:
            params["before"] = str(before)
        if limit is not None:
            params["limit"] = str(limit)
        return [FundingRate.from_dict(row) for row in self.get(endpoints.FUNDING_RATE_HISTORY, params)]

    def open_interest(
        self,
        inst_type: str,
        *,
        inst_id: str = "",
        inst_family: str = "",
    ) -> list[OpenInterest]:
        data = self.get(
            endpoints.OPEN_INTEREST,
            {"instType": inst_type, "instId": inst_id, "instFamily": inst_family},
        )
        return [OpenInterest.from_dict(row) for row in data]

    def mark_price(
        self,
        inst_type: str,
        *,
        inst_id: str = "",
        inst_family: str = "",
    ) -> list[MarkPrice]:
        data = self.get(
            endpoints.MARK_PRICE,
            {"instType": inst_type, "instId": inst_id, "instFamily": inst_family},
        )
        return [MarkPrice.from_dict(row) for row in data]

    def index_tickers(self, *, inst_id: str = "", quote_ccy: str = "") -> list[IndexTicker]:
        data = self.get(endpoints.INDEX_TICKERS, {"instId": inst_id, "quoteCcy": quote_ccy})
        return [IndexTicker.from_dict(row) for row in data]
