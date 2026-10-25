"""CLI for OKX account reads, place, and cancel.

Keys are read from the environment by ``PrivateClient``. This CLI has no key
arguments and never calls a withdrawal route. ``balance`` and ``positions``
run live when ``OKX_DEMO`` is unset (no ``--allow-orders``). Place, cancel,
and order lookup stay on demo (``OKX_DEMO=1``).

::

    python -m tools.okx.private_cli balance
    python -m tools.okx.private_cli positions --inst BTC-USDT-SWAP
    OKX_DEMO=1 python -m tools.okx.private_cli balance
    OKX_DEMO=1 python -m tools.okx.private_cli order --inst BTC-USDT-SWAP --ord-id 123
    OKX_DEMO=1 python -m tools.okx.private_cli --allow-orders cancel --inst BTC-USDT-SWAP --ord-id 123
    OKX_DEMO=1 python -m tools.okx.private_cli --allow-orders place --inst BTC-USDT-SWAP \\
        --side buy --sz 1 --td-mode cross --ord-type limit --px 60000 --margin-usd 5
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
from tools.okx.guards import require_cli_demo, resolve_demo
from tools.okx.private import PrivateClient

_LIVE_READ_CMDS = frozenset({"balance", "positions"})


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "OKX private client. balance and positions can be live; "
            "place and cancel stay on demo. Read-only unless --allow-orders. No withdrawals."
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
        help="allow place, cancel, and amend on the demo account (OKX_DEMO=1)",
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

    place = sub.add_parser("place", help="place one demo order")
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

    cancel = sub.add_parser("cancel", help="cancel one demo order")
    cancel.add_argument("--inst", required=True)
    cancel.add_argument("--ord-id", default="")
    cancel.add_argument("--cl-ord-id", default="")

    order = sub.add_parser("order", help="fetch one order")
    order.add_argument("--inst", required=True)
    order.add_argument("--ord-id", default="")
    order.add_argument("--cl-ord-id", default="")
    return parser


def cli_demo(command: str) -> bool:
    """Demo flag for a CLI command.

    Balance and positions are live when ``OKX_DEMO`` is unset or off. Every
    other command requires ``OKX_DEMO=1``. ``--allow-orders`` does not change this.
    """
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
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def _run(args: argparse.Namespace, client: PrivateClient) -> None:
    if args.cmd == "balance":
        _emit(client.balance(ccy=args.ccy))
        return
    if args.cmd == "positions":
        _emit(client.positions(inst_type=args.inst_type, inst_id=args.inst))
        return
    if args.cmd == "place":
        _emit(
            client.place_order(
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
        )
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
            client = PrivateClient(demo=cli_demo(args.cmd), **client_options(args))
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
