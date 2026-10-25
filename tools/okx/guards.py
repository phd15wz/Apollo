"""Risk guards for the stage-2 demo private client.

Enforced in this package
------------------------
* Read-only by default. Private POSTs refuse unless ``allow_orders=True`` or
  ``OKX_ALLOW_ORDERS=1``. ``read_only=True``, ``--read-only``, and
  ``OKX_READ_ONLY=1`` win over that go flag (kill switch). Cancel is a write.
* Demo only. ``demo=False`` and any ``OKX_DEMO`` value other than on raise
  ``LiveTradingDisabled``. ``LIVE_TRADE_ENABLED`` is a tripwire: setting it
  ``True`` does not enable live trading; the client refuses to start until a
  later change removes the tripwire. Every signed request carries
  ``x-simulated-trading: 1``.
* Allowlisted account and trade routes only. Asset, finance, withdrawal,
  deposit, and transfer paths raise ``UnsafeEndpointError``.
* Soft cap: incremental ``margin_usd`` plus caller-supplied ``open_margins``
  must stay within ``NAME_MARGIN_CAP_USD`` (10) per instrument. Instruments in
  a ``shared_cap`` group from ``config/candidate_pool.yaml`` share that same
  dollar ceiling across the group. ``OKX_RISK_GATE=1`` or ``risk_gate=True``
  multiplies the ceiling by one half (gate half-size). Missing ``margin_usd``
  on a new risk order is refused.

Deferred (not enforced here)
----------------------------
* Read-only live keys, then live Trade. Both need a later PR.
* Automatic margin from mark price, contract value, and leverage. Callers pass
  ``margin_usd``. Position ``margin`` is not converted or auto-fetched.
* A persistent shared-cap ledger across processes. ``open_margins`` is an
  in-memory stub the caller fills. ``open_margins_from_positions`` only sums
  the ``margin`` field already on those objects.
* Wiring ``OKX_RISK_GATE`` to the macro hard gate (``DGS10`` / ``DFII10``).
  The flag is manual. While it is on, this package applies the half ceiling
  rather than blocking every new long until the gate clears.
* Names outside the candidate pool are solo-capped at the same $10. Pool
  membership is not an order allowlist.
* Price-only amends do not recompute margin. Batch, algo, and private
  WebSocket orders are not implemented.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from tools.okx.errors import LiveTradingDisabled, OrdersDisabled, RiskLimitError, UnsafeEndpointError

# Tripwire. Flipping this to True does not place live orders; ``resolve_demo``
# refuses to start so a one-line edit cannot turn demo off.
LIVE_TRADE_ENABLED = False

NAME_MARGIN_CAP_USD = 10.0
GATE_SIZE_FRACTION = 0.5

_ON = frozenset({"1", "true", "yes", "on"})

_WITHDRAW_MARKERS = ("withdraw", "transfer", "deposit")
_BLOCKED_PREFIXES = ("/api/v5/asset", "/api/v5/finance")

# Exact method + path. No batch, algo, asset, or account-config routes.
ALLOWED_PRIVATE: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/api/v5/account/balance"),
        ("GET", "/api/v5/account/positions"),
        ("GET", "/api/v5/trade/order"),
        ("GET", "/api/v5/trade/orders-pending"),
        ("GET", "/api/v5/trade/orders-history"),
        ("GET", "/api/v5/trade/orders-history-archive"),
        ("POST", "/api/v5/trade/order"),
        ("POST", "/api/v5/trade/cancel-order"),
        ("POST", "/api/v5/trade/amend-order"),
    }
)

# Documented private limits (requests, window seconds). Same path shares a bucket.
PRIVATE_RATE_LIMITS: dict[str, tuple[int, float]] = {
    "/api/v5/account/balance": (10, 2.0),
    "/api/v5/account/positions": (10, 2.0),
    "/api/v5/trade/order": (60, 2.0),
    "/api/v5/trade/orders-pending": (60, 2.0),
    "/api/v5/trade/orders-history": (40, 2.0),
    "/api/v5/trade/orders-history-archive": (20, 2.0),
    "/api/v5/trade/cancel-order": (60, 2.0),
    "/api/v5/trade/amend-order": (60, 2.0),
}


def env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _ON


def require_cli_demo() -> None:
    """CLI entry point: the operator must set ``OKX_DEMO`` on, then pass the tripwire."""
    raw = os.environ.get("OKX_DEMO", "").strip().lower()
    if raw not in _ON:
        raise LiveTradingDisabled("CLI requires OKX_DEMO=1. Live trading is not enabled.")
    resolve_demo(True)


def resolve_demo(demo: bool | None) -> bool:
    """Force demo mode. Live values and ``demo=False`` raise.

    ``LIVE_TRADE_ENABLED`` is not an unlock. If it is ever set, startup fails
    until a later change adds a reviewed live path and removes this tripwire.
    """
    raw = os.environ.get("OKX_DEMO")
    if raw is not None and raw.strip() != "" and raw.strip().lower() not in _ON:
        raise LiveTradingDisabled(
            "OKX_DEMO must stay on. Live Trade is not enabled in this package."
        )
    if demo is False:
        raise LiveTradingDisabled(
            "demo=False is refused. Live Trade is not enabled; signed requests stay on demo."
        )
    if LIVE_TRADE_ENABLED:
        raise LiveTradingDisabled(
            "LIVE_TRADE_ENABLED is set but this package has no live trading path. Refusing to start."
        )
    return True


def resolve_allow_orders(*, allow_orders: bool | None, read_only: bool | None) -> bool:
    """Return whether private POSTs may run. The kill switch wins."""
    if read_only is True or env_flag("OKX_READ_ONLY"):
        return False
    if allow_orders is True:
        return True
    if allow_orders is False:
        return False
    return env_flag("OKX_ALLOW_ORDERS")


def resolve_risk_gate(risk_gate: bool | None) -> bool:
    if risk_gate is None:
        return env_flag("OKX_RISK_GATE")
    return bool(risk_gate)


def margin_ceiling(*, gate_on: bool) -> float:
    if gate_on:
        return NAME_MARGIN_CAP_USD * GATE_SIZE_FRACTION
    return NAME_MARGIN_CAP_USD


def assert_private_request(method: str, path: str) -> str:
    """Return the normalized path, or raise if it is not an allowlisted trade/account call."""
    path_only = path.split("?", 1)[0].split("#", 1)[0]
    if not path_only.startswith("/"):
        path_only = "/" + path_only
    lowered = path_only.lower()
    if not lowered.startswith("/api/v5/"):
        raise UnsafeEndpointError(f"refusing non-v5 path {path_only}")
    for marker in _WITHDRAW_MARKERS:
        if marker in lowered:
            raise UnsafeEndpointError(f"refusing withdrawal or transfer path {path_only}")
    for prefix in _BLOCKED_PREFIXES:
        if lowered.startswith(prefix):
            raise UnsafeEndpointError(f"refusing non-trade path {path_only}")
    method_u = method.upper()
    if method_u not in {"GET", "POST"}:
        raise UnsafeEndpointError(f"refusing method {method_u} for {path_only}")
    if (method_u, path_only) not in ALLOWED_PRIVATE:
        raise UnsafeEndpointError(f"{method_u} {path_only} is not on the stage-2 private allowlist")
    return path_only


@dataclass(frozen=True, slots=True)
class CapGroup:
    name: str
    shared_cap: bool
    inst_ids: frozenset[str]


@dataclass(frozen=True, slots=True)
class CapBook:
    """Shared-cap groups derived from ``config/candidate_pool.yaml``."""

    groups: tuple[CapGroup, ...]
    inst_groups: dict[str, tuple[str, ...]]

    def groups_for(self, inst_id: str) -> tuple[CapGroup, ...]:
        names = self.inst_groups.get(inst_id, ())
        by_name = {group.name: group for group in self.groups}
        return tuple(by_name[name] for name in names if name in by_name)


def default_pool_path() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / "candidate_pool.yaml"


def load_cap_book(path: Path | None = None) -> CapBook:
    """Parse candidate-pool groups without a YAML dependency."""
    pool = path or default_pool_path()
    text = pool.read_text(encoding="utf-8")
    return parse_candidate_pool(text)


def parse_candidate_pool(text: str) -> CapBook:
    groups_raw: dict[str, dict[str, object]] = {}
    instruments: list[dict[str, object]] = []
    section: str | None = None
    current_group: str | None = None
    current_inst: dict[str, object] | None = None

    for raw in text.splitlines():
        if not raw.strip() or raw.strip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        content = raw.strip()
        if indent == 0 and content.endswith(":"):
            section = content[:-1]
            current_group = None
            if current_inst is not None:
                instruments.append(current_inst)
                current_inst = None
            continue
        if section == "groups":
            if indent == 2 and content.endswith(":"):
                current_group = content[:-1]
                groups_raw[current_group] = {"shared_cap": False, "tickers": []}
                continue
            if current_group is None or indent < 4 or ":" not in content:
                continue
            key, value = content.split(":", 1)
            key = key.strip()
            value = value.strip()
            if key == "shared_cap":
                groups_raw[current_group]["shared_cap"] = value.lower() == "true"
            elif key == "tickers":
                groups_raw[current_group]["tickers"] = _bracket_list(value)
        elif section == "instruments":
            if content.startswith("- ticker:"):
                if current_inst is not None:
                    instruments.append(current_inst)
                current_inst = {
                    "ticker": content.split(":", 1)[1].strip(),
                    "okx_inst_id": "",
                    "groups": [],
                }
                continue
            if current_inst is None or ":" not in content:
                continue
            key, value = content.split(":", 1)
            key = key.strip()
            value = value.strip()
            if key == "okx_inst_id":
                current_inst["okx_inst_id"] = value
            elif key == "groups":
                current_inst["groups"] = _bracket_list(value)

    if current_inst is not None:
        instruments.append(current_inst)

    ticker_to_inst: dict[str, str] = {}
    inst_groups: dict[str, tuple[str, ...]] = {}
    for row in instruments:
        ticker = str(row.get("ticker") or "")
        inst_id = str(row.get("okx_inst_id") or "")
        names = tuple(str(name) for name in row.get("groups") or [])
        if not inst_id:
            continue
        if ticker:
            ticker_to_inst[ticker] = inst_id
        inst_groups[inst_id] = names
        for name in names:
            if name not in groups_raw:
                raise ValueError(f"candidate pool instrument {inst_id} references unknown group {name}")

    cap_groups: list[CapGroup] = []
    for name, spec in groups_raw.items():
        inst_ids: set[str] = set()
        for ticker in spec.get("tickers") or []:
            inst_id = ticker_to_inst.get(str(ticker))
            if inst_id:
                inst_ids.add(inst_id)
        for inst_id, names in inst_groups.items():
            if name in names:
                inst_ids.add(inst_id)
        cap_groups.append(
            CapGroup(name=name, shared_cap=bool(spec.get("shared_cap")), inst_ids=frozenset(inst_ids))
        )
    return CapBook(groups=tuple(cap_groups), inst_groups=inst_groups)


def _bracket_list(value: str) -> list[str]:
    inner = value.strip()
    if inner.startswith("[") and inner.endswith("]"):
        inner = inner[1:-1]
    return [part.strip() for part in inner.split(",") if part.strip()]


def _finite_margin(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RiskLimitError(f"{label} must be a finite number of USD")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise RiskLimitError(f"{label} must be a finite number >= 0")
    return number


def assert_order_margin(
    book: CapBook,
    inst_id: str,
    margin_usd: float,
    open_margins: Mapping[str, float],
    *,
    gate_on: bool,
) -> float:
    """Refuse when incremental margin plus open margin exceeds the ceiling.

    ``margin_usd`` is the incremental USD margin of this order. ``open_margins``
    is existing margin by instrument and must not already include this order.
    Returns the ceiling that was applied.
    """
    margin = _finite_margin(margin_usd, "margin_usd")
    ceiling = margin_ceiling(gate_on=gate_on)
    existing = _finite_margin(open_margins.get(inst_id, 0.0), f"open margin for {inst_id}")
    projected = existing + margin
    if projected > ceiling + 1e-9:
        gate = "gate half-size " if gate_on else ""
        raise RiskLimitError(
            f"{inst_id} margin {projected:.4f} USD exceeds the {gate}cap of {ceiling:.2f} USD"
        )
    for group in book.groups_for(inst_id):
        if not group.shared_cap:
            continue
        used = 0.0
        for member in sorted(group.inst_ids):
            used += _finite_margin(open_margins.get(member, 0.0), f"open margin for {member}")
        projected_group = used + margin
        if projected_group > ceiling + 1e-9:
            raise RiskLimitError(
                f"{inst_id} shared group {group.name} margin {projected_group:.4f} USD "
                f"exceeds the {ceiling:.2f} USD shared cap"
            )
    return ceiling


class RiskGuards:
    """Per-client copy of the kill switch, the gate flag, and the cap book."""

    def __init__(
        self,
        *,
        allow_orders: bool,
        risk_gate: bool,
        book: CapBook | None = None,
        pool_path: Path | None = None,
    ) -> None:
        self.allow_orders = allow_orders
        self.risk_gate = risk_gate
        self.book = book if book is not None else load_cap_book(pool_path)

    def assert_orders_enabled(self) -> None:
        if not self.allow_orders:
            raise OrdersDisabled(
                "private writes are refused (read-only kill switch). "
                "Set OKX_ALLOW_ORDERS=1 or allow_orders=True. "
                "OKX_READ_ONLY=1 and read_only=True override that flag. Cancel is a write."
            )

    def check_margin(
        self,
        inst_id: str,
        margin_usd: float,
        open_margins: Mapping[str, float] | None = None,
    ) -> float:
        return assert_order_margin(
            self.book,
            inst_id,
            margin_usd,
            open_margins or {},
            gate_on=self.risk_gate,
        )
