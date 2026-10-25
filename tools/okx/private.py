"""OKX v5 private REST client.

Public market data stays on ``PublicClient`` and never uses this module.
Demo calls send ``x-simulated-trading: 1``. Live mode (``demo=False``) signs
balance, positions, and funding balances only and omits that header. Place,
amend, and cancel stay on demo. Withdrawal, deposit, transfer, and every
other asset route are refused. Writes need an explicit go flag; see
``tools.okx.guards``.
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import urlencode, urljoin

import requests

from tools.okx import endpoints
from tools.okx.auth import OkxCredentials, access_headers, load_credentials, redact_secrets
from tools.okx.client import HttpResponse, RateLimiter, RequestsTransport
from tools.okx.errors import OkxError, OkxHttpError, RiskLimitError
from tools.okx.guards import (
    FUNDING_BALANCE_PATH,
    PRIVATE_RATE_LIMITS,
    RiskGuards,
    assert_live_read_only,
    assert_private_request,
    resolve_allow_orders,
    resolve_demo,
    resolve_risk_gate,
)

# OKX demo trading uses the production REST host plus the simulated-trading header.
# There is no separate demo REST hostname.
DEMO_REST_BASE_URL = "https://www.okx.com"

Clock = Callable[[], float]
Sleeper = Callable[[float], None]
Timestamp = Callable[[], str]

_SIDES = frozenset({"buy", "sell"})
_ORD_TYPES = frozenset({"market", "limit", "post_only", "fok", "ioc"})
_TD_MODES = frozenset({"cash", "cross", "isolated"})
_POS_SIDES = frozenset({"", "net", "long", "short"})
_INST_TYPES = frozenset({"SPOT", "MARGIN", "SWAP", "FUTURES", "OPTION"})
_PX_REQUIRED = frozenset({"limit", "post_only", "fok", "ioc"})


class _BodyTransport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None,
        headers: Mapping[str, str],
        timeout: float,
        body: bytes | None = None,
    ) -> HttpResponse: ...


def _s(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _f(value: Any, default: float = 0.0) -> float:
    text = _s(value).strip()
    if text == "":
        return default
    return float(text)


def _i(value: Any, default: int = 0) -> int:
    text = _s(value).strip()
    if text == "":
        return default
    return int(text)


def _iso_timestamp() -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def _decimal(name: str, value: str) -> str:
    text = str(value).strip()
    if not text or text.startswith("+"):
        raise ValueError(f"invalid {name} {value!r}")
    whole, dot, frac = text.partition(".")
    if dot and (not frac or not frac.isdigit() or not whole.isdigit()):
        raise ValueError(f"invalid {name} {value!r}")
    if not dot and not whole.isdigit():
        raise ValueError(f"invalid {name} {value!r}")
    if float(text) <= 0:
        raise ValueError(f"{name} must be positive")
    return text


def _choice(name: str, value: str, allowed: frozenset[str]) -> str:
    text = value.strip()
    if text not in allowed:
        raise ValueError(f"invalid {name} {value!r}; expected one of {sorted(a for a in allowed if a)}")
    return text


def _optional_id(name: str, value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    if not text.isdigit():
        raise ValueError(f"invalid {name} {value!r}")
    return text


def _cl_ord_id(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    if len(text) > 32 or not text.isalnum():
        raise ValueError("cl_ord_id must be 1-32 letters or digits")
    return text


def _ccy_query(ccy: str, *, limit: int | None = None) -> list[tuple[str, str]]:
    text = ccy.strip()
    if not text:
        return []
    parts = text.split(",")
    if limit is not None and len(parts) > limit:
        raise ValueError(f"ccy accepts at most {limit} currencies")
    for part in parts:
        if not part.isalnum() or not part.isupper() or not 2 <= len(part) <= 16:
            raise ValueError(f"invalid ccy {ccy!r}")
    return [("ccy", text)]


def _clean_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in payload.items():
        if value is None or value == "":
            continue
        out[key] = value
    return out


@dataclass(frozen=True, slots=True)
class CurrencyBalance:
    ccy: str
    cash_bal: float
    available_bal: float
    equity: float
    frozen_bal: float
    updated_ms: int

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> CurrencyBalance:
        return cls(
            ccy=_s(row.get("ccy")),
            cash_bal=_f(row.get("cashBal")),
            available_bal=_f(row.get("availBal")),
            equity=_f(row.get("eq")),
            frozen_bal=_f(row.get("frozenBal")),
            updated_ms=_i(row.get("uTime")),
        )


@dataclass(frozen=True, slots=True)
class AccountBalance:
    total_eq_usd: float
    updated_ms: int
    details: tuple[CurrencyBalance, ...]

    @classmethod
    def from_rows(cls, rows: list[Any]) -> AccountBalance:
        if not rows:
            return cls(total_eq_usd=0.0, updated_ms=0, details=())
        row = rows[0]
        if not isinstance(row, dict):
            raise OkxError("0", "balance row was not an object", rows)
        details = tuple(
            CurrencyBalance.from_dict(item)
            for item in (row.get("details") or [])
            if isinstance(item, dict)
        )
        return cls(total_eq_usd=_f(row.get("totalEq")), updated_ms=_i(row.get("uTime")), details=details)


@dataclass(frozen=True, slots=True)
class FundingBalance:
    """One currency row from ``GET /api/v5/asset/balances``."""

    ccy: str
    bal: float
    avail_bal: float
    frozen_bal: float

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> FundingBalance:
        return cls(
            ccy=_s(row.get("ccy")),
            bal=_f(row.get("bal")),
            avail_bal=_f(row.get("availBal")),
            frozen_bal=_f(row.get("frozenBal")),
        )


@dataclass(frozen=True, slots=True)
class Position:
    inst_id: str
    inst_type: str
    pos_side: str
    pos: float
    avg_px: float
    upl: float
    margin: float
    margin_mode: str
    lever: float
    updated_ms: int

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> Position:
        return cls(
            inst_id=_s(row.get("instId")),
            inst_type=_s(row.get("instType")),
            pos_side=_s(row.get("posSide")),
            pos=_f(row.get("pos")),
            avg_px=_f(row.get("avgPx")),
            upl=_f(row.get("upl")),
            margin=_f(row.get("margin")),
            margin_mode=_s(row.get("mgnMode")),
            lever=_f(row.get("lever")),
            updated_ms=_i(row.get("uTime")),
        )


@dataclass(frozen=True, slots=True)
class OrderAck:
    ord_id: str
    cl_ord_id: str
    s_code: str
    s_msg: str

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> OrderAck:
        return cls(
            ord_id=_s(row.get("ordId")),
            cl_ord_id=_s(row.get("clOrdId")),
            s_code=_s(row.get("sCode") or "0"),
            s_msg=_s(row.get("sMsg")),
        )


@dataclass(frozen=True, slots=True)
class OrderRecord:
    inst_id: str
    ord_id: str
    cl_ord_id: str
    side: str
    ord_type: str
    px: str
    sz: str
    acc_fill_sz: str
    avg_px: str
    state: str
    td_mode: str
    created_ms: int
    updated_ms: int

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> OrderRecord:
        return cls(
            inst_id=_s(row.get("instId")),
            ord_id=_s(row.get("ordId")),
            cl_ord_id=_s(row.get("clOrdId")),
            side=_s(row.get("side")),
            ord_type=_s(row.get("ordType")),
            px=_s(row.get("px")),
            sz=_s(row.get("sz")),
            acc_fill_sz=_s(row.get("accFillSz")),
            avg_px=_s(row.get("avgPx")),
            state=_s(row.get("state")),
            td_mode=_s(row.get("tdMode")),
            created_ms=_i(row.get("cTime")),
            updated_ms=_i(row.get("uTime")),
        )


def open_margins_from_positions(positions: list[Position]) -> dict[str, float]:
    """Sum position ``margin`` by instrument.

    This does not convert margin currency and does not call the exchange.
    Pass the result as ``open_margins`` when the numbers are already USD-like.
    """
    totals: dict[str, float] = {}
    for position in positions:
        totals[position.inst_id] = totals.get(position.inst_id, 0.0) + max(0.0, position.margin)
    return totals


class PrivateClient:
    """Signed REST client. Demo unless ``demo=False``; orders stay demo-only."""

    def __init__(
        self,
        *,
        credentials: OkxCredentials | None = None,
        demo: bool | None = None,
        allow_orders: bool | None = None,
        read_only: bool | None = None,
        risk_gate: bool | None = None,
        site: str = "global",
        base_url: str | None = None,
        timeout: float = 10.0,
        max_retries: int = 4,
        transport: _BodyTransport | None = None,
        limiter: RateLimiter | None = None,
        guards: RiskGuards | None = None,
        clock: Clock | None = None,
        sleep: Sleeper | None = None,
        timestamp: Timestamp | None = None,
        user_agent: str = "apollo-okx-private/0.2",
        allow_unofficial_hosts: bool = False,
    ) -> None:
        self.demo = resolve_demo(demo)
        self.allow_orders = resolve_allow_orders(allow_orders=allow_orders, read_only=read_only)
        self.risk_gate = resolve_risk_gate(risk_gate)
        if site not in endpoints.SITE_BASE_URLS and base_url is None:
            raise ValueError(f"unknown site {site!r}; expected one of {sorted(endpoints.SITE_BASE_URLS)}")
        self.site = site
        self.base_url = (base_url or endpoints.SITE_BASE_URLS.get(site) or DEMO_REST_BASE_URL).rstrip("/")
        endpoints.assert_okx_url(self.base_url + "/", allow_unofficial_hosts=allow_unofficial_hosts)
        self.timeout = timeout
        self.max_retries = max_retries
        self._credentials = credentials if credentials is not None else load_credentials()
        redact_secrets(
            self._credentials.api_key,
            self._credentials.api_secret,
            self._credentials.passphrase,
        )
        self._clock = clock or time.monotonic
        self._sleep = sleep or time.sleep
        self._timestamp = timestamp or _iso_timestamp
        self._transport = transport or RequestsTransport()
        self._limiter = limiter or RateLimiter(
            PRIVATE_RATE_LIMITS,
            clock=self._clock,
            sleep=self._sleep,
        )
        self._guards = guards or RiskGuards(allow_orders=self.allow_orders, risk_gate=self.risk_gate)
        self._guards.allow_orders = self.allow_orders
        self._guards.risk_gate = self.risk_gate
        self._headers = {
            "Accept": "application/json",
            "Accept-Language": "en-US",
            "User-Agent": user_agent,
        }

    def __repr__(self) -> str:
        return (
            f"PrivateClient(site={self.site!r}, demo={self.demo!r}, "
            f"allow_orders={self.allow_orders!r}, risk_gate={self.risk_gate!r})"
        )

    def balance(self, ccy: str = "") -> AccountBalance:
        payload = self._request("GET", "/api/v5/account/balance", query=_ccy_query(ccy))
        return AccountBalance.from_rows(_data_list(payload))

    def funding(self, ccy: str = "") -> list[FundingBalance]:
        """Funding-account balances. ``ccy`` is optional (at most 20, comma-separated)."""
        payload = self._request(
            "GET",
            FUNDING_BALANCE_PATH,
            query=_ccy_query(ccy, limit=20),
        )
        return [FundingBalance.from_dict(row) for row in _data_list(payload)]

    def positions(self, inst_type: str = "", inst_id: str = "") -> list[Position]:
        query: list[tuple[str, str]] = []
        if inst_type:
            query.append(("instType", _choice("inst_type", inst_type, _INST_TYPES)))
        if inst_id:
            query.append(("instId", endpoints.validate_inst_id(inst_id)))
        payload = self._request("GET", "/api/v5/account/positions", query=query)
        return [Position.from_dict(row) for row in _data_list(payload)]

    def _refuse_live_write(self, path: str) -> None:
        assert_live_read_only(self.demo, "POST", path)

    def place_order(
        self,
        inst_id: str,
        *,
        side: str,
        sz: str,
        td_mode: str,
        ord_type: str,
        margin_usd: float | None = None,
        px: str | None = None,
        cl_ord_id: str = "",
        pos_side: str = "",
        reduce_only: bool = False,
        open_margins: Mapping[str, float] | None = None,
    ) -> OrderAck:
        self._refuse_live_write("/api/v5/trade/order")
        inst = endpoints.validate_inst_id(inst_id)
        side_n = _choice("side", side, _SIDES)
        ord_type_n = _choice("ord_type", ord_type, _ORD_TYPES)
        td_mode_n = _choice("td_mode", td_mode, _TD_MODES)
        pos_side_n = _choice("pos_side", pos_side, _POS_SIDES)
        self._guards.assert_orders_enabled()
        sz_n = _decimal("sz", sz)
        px_n = ""
        if ord_type_n in _PX_REQUIRED:
            if px is None or str(px).strip() == "":
                raise ValueError(f"px is required for ord_type {ord_type_n}")
            px_n = _decimal("px", str(px))
        elif px not in (None, ""):
            px_n = _decimal("px", str(px))
        self._check_new_margin(inst, margin_usd, reduce_only=reduce_only, open_margins=open_margins)
        payload = _clean_payload(
            {
                "instId": inst,
                "tdMode": td_mode_n,
                "side": side_n,
                "ordType": ord_type_n,
                "sz": sz_n,
                "px": px_n,
                "clOrdId": _cl_ord_id(cl_ord_id),
                "posSide": pos_side_n,
                "reduceOnly": True if reduce_only else None,
            }
        )
        body = self._request("POST", "/api/v5/trade/order", payload=payload)
        return _one_ack(body)

    def cancel_order(self, inst_id: str, *, ord_id: str = "", cl_ord_id: str = "") -> OrderAck:
        self._refuse_live_write("/api/v5/trade/cancel-order")
        self._guards.assert_orders_enabled()
        inst = endpoints.validate_inst_id(inst_id)
        ord_n = _optional_id("ord_id", ord_id)
        cl_n = _cl_ord_id(cl_ord_id)
        if not ord_n and not cl_n:
            raise ValueError("cancel_order requires ord_id or cl_ord_id")
        body = self._request(
            "POST",
            "/api/v5/trade/cancel-order",
            payload=_clean_payload({"instId": inst, "ordId": ord_n, "clOrdId": cl_n}),
        )
        return _one_ack(body)

    def amend_order(
        self,
        inst_id: str,
        *,
        ord_id: str = "",
        cl_ord_id: str = "",
        new_px: str | None = None,
        new_sz: str | None = None,
        margin_usd: float | None = None,
        reduce_only: bool = False,
        open_margins: Mapping[str, float] | None = None,
    ) -> OrderAck:
        self._refuse_live_write("/api/v5/trade/amend-order")
        self._guards.assert_orders_enabled()
        inst = endpoints.validate_inst_id(inst_id)
        ord_n = _optional_id("ord_id", ord_id)
        cl_n = _cl_ord_id(cl_ord_id)
        if not ord_n and not cl_n:
            raise ValueError("amend_order requires ord_id or cl_ord_id")
        px_n = _decimal("new_px", new_px) if new_px not in (None, "") else ""
        sz_n = _decimal("new_sz", new_sz) if new_sz not in (None, "") else ""
        if not px_n and not sz_n:
            raise ValueError("amend_order requires new_px or new_sz")
        if sz_n:
            self._check_new_margin(inst, margin_usd, reduce_only=reduce_only, open_margins=open_margins)
        body = self._request(
            "POST",
            "/api/v5/trade/amend-order",
            payload=_clean_payload(
                {
                    "instId": inst,
                    "ordId": ord_n,
                    "clOrdId": cl_n,
                    "newPx": px_n,
                    "newSz": sz_n,
                }
            ),
        )
        return _one_ack(body)

    def get_order(self, inst_id: str, *, ord_id: str = "", cl_ord_id: str = "") -> OrderRecord:
        inst = endpoints.validate_inst_id(inst_id)
        ord_n = _optional_id("ord_id", ord_id)
        cl_n = _cl_ord_id(cl_ord_id)
        if not ord_n and not cl_n:
            raise ValueError("get_order requires ord_id or cl_ord_id")
        payload = self._request(
            "GET",
            "/api/v5/trade/order",
            query=[("instId", inst), ("ordId", ord_n), ("clOrdId", cl_n)],
        )
        rows = _data_list(payload)
        if not rows:
            raise OkxError("0", f"no order for {inst}", payload)
        return OrderRecord.from_dict(rows[0])

    def open_orders(self, inst_type: str = "", inst_id: str = "") -> list[OrderRecord]:
        query: list[tuple[str, str]] = []
        if inst_type:
            query.append(("instType", _choice("inst_type", inst_type, _INST_TYPES)))
        if inst_id:
            query.append(("instId", endpoints.validate_inst_id(inst_id)))
        payload = self._request("GET", "/api/v5/trade/orders-pending", query=query)
        return [OrderRecord.from_dict(row) for row in _data_list(payload)]

    def order_history(
        self,
        inst_type: str,
        *,
        inst_id: str = "",
        state: str = "",
        limit: int | None = None,
        after: str = "",
        before: str = "",
        archived: bool = False,
    ) -> list[OrderRecord]:
        query = [("instType", _choice("inst_type", inst_type, _INST_TYPES))]
        if inst_id:
            query.append(("instId", endpoints.validate_inst_id(inst_id)))
        if state:
            if not state.replace("_", "").isalpha() or not state.islower():
                raise ValueError(f"invalid state {state!r}")
            query.append(("state", state))
        if limit is not None:
            if limit < 1 or limit > 100:
                raise ValueError("limit must be between 1 and 100")
            query.append(("limit", str(limit)))
        if after:
            query.append(("after", _optional_id("after", after)))
        if before:
            query.append(("before", _optional_id("before", before)))
        path = "/api/v5/trade/orders-history-archive" if archived else "/api/v5/trade/orders-history"
        payload = self._request("GET", path, query=query)
        return [OrderRecord.from_dict(row) for row in _data_list(payload)]

    def _check_new_margin(
        self,
        inst_id: str,
        margin_usd: float | None,
        *,
        reduce_only: bool,
        open_margins: Mapping[str, float] | None,
    ) -> None:
        if reduce_only:
            if margin_usd is not None:
                self._guards.check_margin(inst_id, margin_usd, open_margins)
            return
        if margin_usd is None:
            raise RiskLimitError(
                "margin_usd is required for new risk. "
                "Pass incremental USD margin, or set reduce_only=True to flatten."
            )
        if margin_usd <= 0:
            raise RiskLimitError("margin_usd must be > 0 unless reduce_only=True")
        self._guards.check_margin(inst_id, margin_usd, open_margins)

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: list[tuple[str, str]] | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        path_only = assert_private_request(method, path)
        assert_live_read_only(self.demo, method, path_only)
        if method.upper() == "POST":
            self._guards.assert_orders_enabled()
        pairs = [(key, value) for key, value in (query or []) if value not in (None, "")]
        query_s = urlencode(pairs)
        request_path = path_only + (("?" + query_s) if query_s else "")
        body_text = ""
        body_bytes: bytes | None = None
        if payload is not None:
            body_text = json.dumps(payload, separators=(",", ":"), sort_keys=True)
            body_bytes = body_text.encode("utf-8")
        timestamp = self._timestamp()
        signed = access_headers(
            self._credentials,
            timestamp=timestamp,
            method=method,
            request_path=request_path,
            body=body_text,
            demo=self.demo,
        )
        if self.demo:
            if signed.get("x-simulated-trading") != "1":
                raise OkxError("demo", "simulated trading header missing", None)
        elif "x-simulated-trading" in signed:
            raise OkxError("live", "live read must not send the simulated trading header", None)
        headers = dict(self._headers)
        headers.update(signed)
        if body_bytes is not None:
            headers["Content-Type"] = "application/json"
        url = urljoin(self.base_url + "/", path_only.lstrip("/"))
        if query_s:
            url = f"{url}?{query_s}"
        last_error: Exception | None = None
        attempts = self.max_retries + 1
        for attempt in range(attempts):
            self._limiter.acquire(path_only)
            try:
                response = self._transport.request(
                    method.upper(),
                    url,
                    params=None,
                    headers=headers,
                    timeout=self.timeout,
                    body=body_bytes,
                )
            except (requests.Timeout, requests.ConnectionError, TimeoutError, OSError) as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                self._backoff(attempt)
                continue
            if response.status_code in {429, 500, 502, 503, 504}:
                last_error = OkxHttpError(
                    response.status_code,
                    url,
                    response.body.decode("utf-8", "replace"),
                )
                if attempt >= self.max_retries:
                    raise last_error
                self._backoff(attempt, retry_after=response.header("Retry-After"))
                continue
            if response.status_code != 200:
                raise OkxHttpError(response.status_code, url, response.body.decode("utf-8", "replace"))
            try:
                parsed = response.json()
            except json.JSONDecodeError as exc:
                raise OkxHttpError(response.status_code, url, "invalid JSON") from exc
            if not isinstance(parsed, dict):
                raise OkxError("?", "response JSON was not an object", parsed)
            code = str(parsed.get("code", ""))
            if code == "50011":
                last_error = OkxError(code, str(parsed.get("msg", "")), parsed)
                if attempt >= self.max_retries:
                    raise last_error
                self._backoff(attempt)
                continue
            if code != "0":
                _raise_business(parsed, code)
            _raise_subcodes(parsed)
            return parsed
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


def _data_list(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data", [])
    if data is None:
        return []
    if not isinstance(data, list):
        raise OkxError(str(payload.get("code", "?")), "expected data array", payload)
    return [row for row in data if isinstance(row, dict)]


def _raise_business(payload: Mapping[str, Any], code: str) -> None:
    data = payload.get("data")
    if isinstance(data, list) and data and isinstance(data[0], dict) and data[0].get("sCode"):
        raise OkxError(str(data[0].get("sCode")), _s(data[0].get("sMsg")), payload)
    raise OkxError(code, _s(payload.get("msg")), payload)


def _raise_subcodes(payload: Mapping[str, Any]) -> None:
    data = payload.get("data")
    if not isinstance(data, list):
        return
    for row in data:
        if not isinstance(row, dict) or "sCode" not in row:
            continue
        scode = _s(row.get("sCode")) or "0"
        if scode != "0":
            raise OkxError(scode, _s(row.get("sMsg")), payload)


def _one_ack(payload: Mapping[str, Any]) -> OrderAck:
    rows = _data_list(payload)
    if not rows:
        raise OkxError(str(payload.get("code", "?")), "empty order acknowledgement", payload)
    return OrderAck.from_dict(rows[0])
