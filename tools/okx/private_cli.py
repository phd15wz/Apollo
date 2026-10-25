"""CLI for OKX account reads, place, and cancel.

Keys are read from the environment by ``PrivateClient``. This CLI has no key
arguments and never calls a withdrawal route. ``balance``, ``positions``, and
``funding`` run live when ``OKX_DEMO`` is unset (no ``--allow-orders``).
Demo place, cancel, and order lookup stay on ``OKX_DEMO=1``.

The live place/cancel smoke also needs ``OKX_LIVE_TRADE=1`` and
``--allow-orders``, with ``OKX_DEMO`` unset or off. ``smoke`` places one
resting BTC-USDT cash limit (size 0.001, px at most 40000) and cancels it
in that same process. A live ``place`` of that shape cancels before exit.
Amend and order lookup stay on demo. Withdraw stays off.

::

    python -m tools.okx.private_cli balance
    python -m tools.okx.private_cli positions --inst BTC-USDT-SWAP
    python -m tools.okx.private_cli funding --ccy USDT
    OKX_DEMO=1 python -m tools.okx.private_cli balance
    OKX_DEMO=1 python -m tools.okx.private_cli order --inst BTC-USDT-SWAP --ord-id 123
    OKX_DEMO=1 python -m tools.okx.private_cli --allow-orders cancel --inst BTC-USDT-SWAP --ord-id 123
    OKX_DEMO=1 python -m tools.okx.private_cli --allow-orders place --inst BTC-USDT-SWAP \\
        --side buy --sz 1 --td-mode cross --ord-type limit --px 60000 --margin-usd 5
    OKX_LIVE_TRADE=1 python -m tools.okx.private_cli --allow-orders smoke
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, is_dataclass
from typing import Any, Sequence

from tools.okx.errors import (
    LiveTradingDisabled,
    MissingCredentials,
    OkxError,
    OkxHttpError,
    OrdersDisabled,
    RiskLimitError,
    UnsafeEndpointError,
)
from tools.okx.guards import env_flag, require_cli_demo, resolve_demo
from tools.okx.private import OrderAck, PrivateClient

_LIVE_READ_CMDS = frozenset({"balance", "positions", "funding"})
_LIVE_ORDER_CMDS = frozenset({"place", "cancel", "smoke"})


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "OKX private client. balance, positions, and funding can be live; "
            "demo place and cancel need OKX_DEMO=1. The live BTC-USDT smoke needs "
            "OKX_LIVE_TRADE=1 and --allow-orders. Read-only unless --allow-orders. "
            "No withdrawals."
        ),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--read-only",
        action="store_true",
        help="force the kill switch even if OKX_ALLOW_ORDERS=1",
    )
    mode.add_argument(
        "--allow-orders",
        action="store_true",
        help=(
            "allow demo place, cancel, and amend (OKX_DEMO=1), or the live "
            "BTC-USDT place/cancel smoke (OKX_LIVE_TRADE=1, OKX_DEMO unset)"
        ),
    )
    parser.add_argument(
        "--risk-gate",
        action="store_true",
        help="half the $10 margin ceiling (manual stand-in for the macro hard gate)",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    balance = sub.add_parser("balance", help="account balance (live when OKX_DEMO is unset)")
    balance.add_argument("--ccy", default="")

    positions = sub.add_parser("positions", help="positions (live when OKX_DEMO is unset)")
    positions.add_argument("--inst-type", default="")
    positions.add_argument("--inst", default="")

    funding = sub.add_parser("funding", help="funding balances (live when OKX_DEMO is unset)")
    funding.add_argument("--ccy", default="")

    place = sub.add_parser("place", help="place one order (demo, or the live smoke which then cancels)")
    place.add_argument("--inst", required=True)
    place.add_argument("--side", required=True)
    place.add_argument("--sz", required=True)
    place.add_argument("--td-mode", required=True)
    place.add_argument("--ord-type", required=True)
    place.add_argument("--px", default="")
    place.add_argument("--margin-usd", type=float, default=None)
    place.add_argument("--cl-ord-id", default="")
    place.add_argument("--pos-side", default="")
    place.add_argument("--reduce-only", action="store_true")

    cancel = sub.add_parser("cancel", help="cancel one order (demo, or the live BTC-USDT smoke)")
    cancel.add_argument("--inst", required=True)
    cancel.add_argument("--ord-id", default="")
    cancel.add_argument("--cl-ord-id", default="")

    order = sub.add_parser("order", help="fetch one demo order")
    order.add_argument("--inst", required=True)
    order.add_argument("--ord-id", default="")
    order.add_argument("--cl-ord-id", default="")

    smoke = sub.add_parser(
        "smoke",
        help="place the BTC-USDT min-size resting limit and cancel it in this process",
    )
    smoke.add_argument("--px", default="40000")
    smoke.add_argument("--margin-usd", type=float, default=1.0)
    smoke.add_argument("--cl-ord-id", default="")
    return parser


def cli_demo(command: str, *, allow_orders: bool = False) -> bool:
    """Demo flag for a CLI command.

    Balance, positions, and funding are live when ``OKX_DEMO`` is unset or off.
    Demo place, cancel, and order lookup require ``OKX_DEMO=1``. Live place,
    cancel, and ``smoke`` require ``OKX_LIVE_TRADE=1``, ``--allow-orders``,
    and ``OKX_DEMO`` unset or off. ``--allow-orders`` alone does not make a
    write live.
    """
    if command in _LIVE_ORDER_CMDS and env_flag("OKX_LIVE_TRADE"):
        if not allow_orders:
            raise LiveTradingDisabled(
                "Live place and cancel require --allow-orders and OKX_LIVE_TRADE=1. "
                "OKX_DEMO must be unset or off. Withdraw stays off."
            )
        raw_demo = os.environ.get("OKX_DEMO", "").strip().lower()
        if raw_demo in {"1", "true", "yes", "on"}:
            raise LiveTradingDisabled(
                "OKX_LIVE_TRADE=1 conflicts with OKX_DEMO on. Unset OKX_DEMO for the live smoke."
            )
        return resolve_demo(False)
    if command not in _LIVE_READ_CMDS:
        require_cli_demo()
        return True
    raw = os.environ.get("OKX_DEMO")
    if raw is None or raw.strip() == "":
        return resolve_demo(False)
    return resolve_demo(None)


def client_options(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "allow_orders": True if args.allow_orders else None,
        "read_only": True if args.read_only else None,
        "risk_gate": True if args.risk_gate else None,
    }


def _emit(value: Any) -> None:
    print(json.dumps(_jsonable(value), indent=2, sort_keys=True))


def _jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def _cancel_live_place(client: PrivateClient, inst: str, placed: OrderAck) -> OrderAck:
    print(f"cancelling live order {placed.ord_id}", file=sys.stderr)
    return client.cancel_order(inst, ord_id=placed.ord_id, cl_ord_id=placed.cl_ord_id)


def _run(args: argparse.Namespace, client: PrivateClient) -> None:
    if args.cmd == "balance":
        _emit(client.balance(ccy=args.ccy))
        return
    if args.cmd == "positions":
        _emit(client.positions(inst_type=args.inst_type, inst_id=args.inst))
        return
    if args.cmd == "funding":
        _emit(client.funding(ccy=args.ccy))
        return
    if args.cmd == "place":
        placed = client.place_order(
            args.inst,
            side=args.side,
            sz=args.sz,
            td_mode=args.td_mode,
            ord_type=args.ord_type,
            px=args.px or None,
            margin_usd=args.margin_usd,
            cl_ord_id=args.cl_ord_id,
            pos_side=args.pos_side,
            reduce_only=args.reduce_only,
        )
        if client.demo:
            _emit(placed)
            return
        _emit({"placed": placed, "cancelled": _cancel_live_place(client, args.inst, placed)})
        return
    if args.cmd == "smoke":
        placed, cancelled = client.smoke_place_cancel(
            px=args.px,
            margin_usd=args.margin_usd,
            cl_ord_id=args.cl_ord_id,
        )
        _emit({"placed": placed, "cancelled": cancelled})
        return
    if args.cmd == "cancel":
        _emit(client.cancel_order(args.inst, ord_id=args.ord_id, cl_ord_id=args.cl_ord_id))
        return
    if args.cmd == "order":
        _emit(client.get_order(args.inst, ord_id=args.ord_id, cl_ord_id=args.cl_ord_id))
        return
    raise ValueError(f"unknown command {args.cmd}")


def main(argv: Sequence[str] | None = None, *, client: PrivateClient | None = None) -> int:
    args = _parser().parse_args(list(argv) if argv is not None else None)
    try:
        if client is None:
            demo = cli_demo(args.cmd, allow_orders=bool(args.allow_orders))
            options = client_options(args)
            if not demo and args.cmd in _LIVE_ORDER_CMDS:
                options["live_trade"] = True
            client = PrivateClient(demo=demo, **options)
        _run(args, client)
    except (
        LiveTradingDisabled,
        OrdersDisabled,
        MissingCredentials,
        RiskLimitError,
        UnsafeEndpointError,
        ValueError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (OkxError, OkxHttpError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
