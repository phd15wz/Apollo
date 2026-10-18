"""Print live public trades / top-of-book. Recording is off unless ``--record``.

Usage::

    python -m tools.okx.stream_demo --inst SPCX-USDT-SWAP --channels trades,books5 --seconds 60
    python -m tools.okx.stream_demo --inst SPCX-USDT-SWAP --channels trades,books5 --seconds 60 --record
"""

from __future__ import annotations

import argparse
import asyncio
import time
from collections import Counter
from typing import Iterable

from tools.okx import endpoints
from tools.okx.archive import default_data_dir
from tools.okx.client import PublicClient
from tools.okx.record import StreamRecorder
from tools.okx.stream import OkxPublicStream, StreamMessage
from tools.okx.types import Trade


def _parse_channels(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


def _format_trade(trade: Trade) -> str:
    return (
        f"trade {trade.inst_id} id={trade.trade_id} {trade.side} "
        f"{trade.size} @ {trade.price} ts={trade.ts_ms}"
    )


def _format_book(msg: StreamMessage) -> str:
    data = msg.data or {}
    if isinstance(data, list) and data:
        data = data[0]
    bids = data.get("bids") if isinstance(data, dict) else None
    asks = data.get("asks") if isinstance(data, dict) else None
    bid = bids[0][:2] if bids else ["?", "?"]
    ask = asks[0][:2] if asks else ["?", "?"]
    return f"{msg.channel} {msg.inst_id} bid={bid[0]} x {bid[1]} ask={ask[0]} x {ask[1]}"


def _print_message(msg: StreamMessage) -> None:
    if msg.kind == "gap":
        gap = msg.gap
        print(
            f"GAP {msg.channel} {msg.inst_id} reason={gap.reason if gap else '?'} "
            f"ids={gap.first_missing_trade_id if gap else None}-"
            f"{gap.last_missing_trade_id if gap else None} dropped={gap.dropped if gap else 0}"
        )
        return
    if msg.kind in {"stale", "recovered", "book_resync", "low_disk", "record_error", "lag", "lag_cleared"}:
        print(f"{msg.kind.upper()} {msg.channel} {msg.inst_id} {msg.data or ''}")
        return
    if msg.channel == "trades" and isinstance(msg.data, Trade):
        print(_format_trade(msg.data))
        return
    if msg.channel in {"books", "books5", "bbo-tbt"}:
        print(_format_book(msg))
        return
    print(f"{msg.channel} {msg.inst_id} {msg.kind}")


def _throughput_lines(recorder: StreamRecorder, counts: Counter[str], elapsed: float) -> list[str]:
    lines = [f"elapsed_s={elapsed:.2f}"]
    report = recorder.throughput_report(elapsed)
    if not report:
        minutes = max(elapsed, 1e-9) / 60.0
        for name, n in sorted(counts.items()):
            lines.append(f"{name}: messages_per_minute={n / minutes:.2f} (not recording)")
        return lines
    for key, row in report.items():
        extra = counts.get(key.split(":", 1)[-1], 0)
        lines.append(
            f"{key}: messages_per_minute={row['messages_per_minute']:.2f} "
            f"raw_bytes_per_minute={row['raw_bytes_per_minute']:.1f} "
            f"compressed_bytes_per_minute={row['compressed_bytes_per_minute']:.1f}"
            + (f" (live_count={extra})" if extra else "")
        )
    return lines


async def _run(args: argparse.Namespace) -> int:
    channels = _parse_channels(args.channels)
    subs = [(channel, args.inst) for channel in channels]
    cache_dir = args.data_dir or default_data_dir()
    record_channels = _parse_channels(args.record_channels) if args.record_channels else None
    recorder = StreamRecorder(
        cache_dir=cache_dir,
        enabled=bool(args.record or record_channels),
        rotation=args.rotation,
        books_snapshot_interval=args.books_snapshot_interval,
        retention_max_age_s=args.retention_age,
        retention_max_bytes=args.retention_bytes,
        channels=frozenset(record_channels) if record_channels else None,
    )
    rest = PublicClient()

    def _okx_time_ms() -> int | None:
        rows = rest.get(endpoints.SERVER_TIME)
        if not rows:
            return None
        return int(rows[0]["ts"])

    counts: Counter[str] = Counter()
    started = time.monotonic()
    async with OkxPublicStream(
        subs,
        rest=rest,
        recorder=recorder,
        stale_timeout=args.stale_timeout,
        public_url=args.public_url,
        business_url=args.business_url,
        time_fetcher=_okx_time_ms,
    ) as stream:
        end = started + args.seconds
        while time.monotonic() < end:
            timeout = max(0.01, end - time.monotonic())
            try:
                msg = await asyncio.wait_for(anext(stream), timeout=timeout)
            except (StopAsyncIteration, TimeoutError, asyncio.TimeoutError):
                break
            counts[msg.channel or msg.kind] += 1
            if msg.kind != "data" or args.verbose or msg.channel in {"trades", "books5", "bbo-tbt", "books"}:
                _print_message(msg)
            if stream.is_stale(msg.channel, msg.inst_id) and msg.kind == "data":
                print(f"warning: acting on stale flag for {msg.channel} {msg.inst_id}")
    elapsed = time.monotonic() - started
    recorder.close()
    print("--- throughput ---")
    for line in _throughput_lines(recorder, counts, elapsed):
        print(line)
    if recorder.files_written:
        print("recorded_files:")
        for path in recorder.files_written:
            print(f"  {path} ({path.stat().st_size if path.exists() else 0} bytes)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inst", default="SPCX-USDT-SWAP", help="instrument id")
    parser.add_argument("--channels", default="trades,books5")
    parser.add_argument("--seconds", type=float, default=60.0)
    parser.add_argument("--record", action="store_true", help="write gzip JSONL under OKX_DATA_DIR (off by default)")
    parser.add_argument(
        "--record-channels",
        default=None,
        help="comma-separated channels to record (implies --record; default is all subscribed)",
    )
    parser.add_argument("--rotation", choices=("hourly", "daily"), default="daily")
    parser.add_argument("--books-snapshot-interval", type=float, default=60.0)
    parser.add_argument("--retention-age", type=float, default=None)
    parser.add_argument("--retention-bytes", type=int, default=None)
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--stale-timeout", type=float, default=60.0)
    parser.add_argument(
        "--public-url",
        default=None,
        help="wss public URL (else OKX_WS_PUBLIC_URL, else wss://ws.okx.com:8443/ws/v5/public)",
    )
    parser.add_argument(
        "--business-url",
        default=None,
        help="wss business URL (else OKX_WS_BUSINESS_URL, else wss://ws.okx.com:8443/ws/v5/business)",
    )
    parser.add_argument("--verbose", action="store_true")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
