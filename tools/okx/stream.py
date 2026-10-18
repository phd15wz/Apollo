"""Public, keyless OKX WebSocket client.

Stage 1: public data only. No API keys, no ``login`` op, no private
channels, no order path. Optional ``websockets`` dependency (asyncio).

Safeguards
----------
1. **Gap detection.** Last trade id is tracked per instrument. After a
   reconnect or a jump in trade ids the missing range is backfilled from
   REST ``/market/history-trades`` (trade-id paging), de-duplicated, and
   emitted in order. If backfill cannot complete, a ``gap`` event is
   emitted and written to the recording — never silently dropped.
2. **Stale-feed timeout.** Per-subscription silence past ``stale_timeout``
   sets a stale flag and emits ``stale``; the next data message emits
   ``recovered``. ``is_stale`` / ``stale_subscriptions`` expose the flag
   so callers can stop acting on a dead feed.
3. **Size caps.** Optional gzip-JSONL recording (off by default) uses the
   same ``OKX_DATA_DIR`` budgets as zip downloads and raises
   ``CacheBudgetExceeded``. The in-memory outbound queue is strictly
   bounded. Consecutive overflow drops coalesce into one ``gap`` event
   (invariant: ``overflow_count == sum(gap.dropped)``). Consumer-side
   overflow is **not** written to the recording; recorder-side writer
   overflow is.
4. **Public channels only.** ``login`` and private channel names raise
   :class:`PrivateChannelError`. Hosts are allowlisted; only ``wss``
   ``/ws/v5/public`` and ``/ws/v5/business`` are accepted.

Overflow policy
    Default queue size is 10_000 events (data, control, and gaps). When
    full, the oldest data event is discarded and folded into one overflow
    ``gap`` for that instrument. Downstream Hawkes fits must treat
    ``kind='gap'`` as a hole in the tape, never as a quiet period.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import threading
import time
import zlib
from collections import deque
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable, Iterable, Mapping, Protocol, Sequence

from tools.okx import endpoints
from tools.okx.client import PublicClient
from tools.okx.errors import CacheBudgetExceeded, LowDiskError, PrivateChannelError
from tools.okx.market import history_trades_range
from tools.okx.record import StreamRecorder
from tools.okx.types import Trade, parse_trade_id

Connector = Callable[[str], Awaitable["WsConnection"]]
Clock = Callable[[], float]
AsyncSleeper = Callable[[float], Awaitable[None]]

DEFAULT_QUEUE_MAX = 10_000
DEFAULT_PING_INTERVAL = 25.0
DEFAULT_STALE_TIMEOUT = 60.0
DEFAULT_RECONNECT_BASE = 0.5
DEFAULT_RECONNECT_MAX = 30.0
DEFAULT_HANDSHAKE_TIMEOUT = 8.0
DEFAULT_HEALTHY_AFTER = 5.0

_LOG = logging.getLogger(__name__)


class WsConnection(Protocol):
    async def send(self, data: str) -> None: ...
    async def recv(self) -> str: ...
    async def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class GapInfo:
    channel: str
    inst_id: str
    reason: str
    first_missing_trade_id: int | None = None
    last_missing_trade_id: int | None = None
    start_ts_ms: int | None = None
    end_ts_ms: int | None = None
    dropped: int = 0


@dataclass(frozen=True, slots=True)
class StreamMessage:
    """One item from :class:`OkxPublicStream`.

    ``kind`` is ``data``, ``gap``, ``stale``, ``recovered``,
    ``book_resync``, or ``low_disk``. Treat ``gap`` as a missing interval,
    never as a quiet period.
    """

    kind: str
    channel: str
    inst_id: str
    ts_ms: int = 0
    data: Any = None
    stale: bool = False
    gap: GapInfo | None = None
    raw: dict[str, Any] | None = None


def _import_websockets() -> Any:
    try:
        import websockets
    except ImportError as exc:
        raise ImportError(
            "the public OKX stream requires the optional 'websockets' package. "
            "Install it with: python3 -m pip install 'websockets>=12'"
        ) from exc
    return websockets


class WebsocketsConnector:
    """Connect via the optional ``websockets`` package."""

    def __init__(
        self,
        *,
        allow_unofficial_hosts: bool = False,
        allow_insecure: bool = False,
        **kwargs: Any,
    ) -> None:
        self.allow_unofficial_hosts = allow_unofficial_hosts
        self.allow_insecure = allow_insecure
        self.kwargs = kwargs

    async def __call__(self, url: str) -> WsConnection:
        endpoints.assert_okx_ws_url(
            url,
            allow_unofficial_hosts=self.allow_unofficial_hosts,
            allow_insecure=self.allow_insecure,
        )
        ws = _import_websockets()
        return await ws.connect(url, **self.kwargs)


def book_crc32(bids: Sequence[Sequence[Any]], asks: Sequence[Sequence[Any]]) -> int:
    """OKX books CRC32 over the top 25 bid/ask price:size pairs (signed int32).

    Checksum is deprecated on the live feed (always ``0`` after 2026-06-23)
    and is skipped when the field is 0. Non-zero values are still verified.
    """
    parts: list[str] = []
    n = max(len(bids), len(asks))
    for i in range(min(25, n)):
        if i < len(bids):
            parts.append(str(bids[i][0]))
            parts.append(str(bids[i][1]))
        if i < len(asks):
            parts.append(str(asks[i][0]))
            parts.append(str(asks[i][1]))
    digest = zlib.crc32(":".join(parts).encode("utf-8"))
    if digest >= 2**31:
        digest -= 2**32
    return int(digest)


@dataclass
class LocalBook:
    bids: dict[str, list[Any]] = field(default_factory=dict)
    asks: dict[str, list[Any]] = field(default_factory=dict)
    seq_id: int | None = None
    ts_ms: int = 0

    def snapshot(self) -> dict[str, Any]:
        return {
            "bids": self._levels(self.bids, reverse=True),
            "asks": self._levels(self.asks, reverse=False),
            "seqId": self.seq_id,
            "ts": str(self.ts_ms),
        }

    def apply(self, row: Mapping[str, Any], *, action: str) -> str | None:
        if action == "snapshot":
            self.bids.clear()
            self.asks.clear()
            self.seq_id = None
        prev = row.get("prevSeqId")
        seq = row.get("seqId")
        prev_i = int(prev) if prev is not None and str(prev) != "" else None
        seq_i = int(seq) if seq is not None and str(seq) != "" else None
        empty = not row.get("asks") and not row.get("bids")
        if action == "update" and self.seq_id is not None and prev_i is not None and seq_i is not None:
            if prev_i == seq_i == self.seq_id and empty:
                self.ts_ms = int(row.get("ts") or self.ts_ms)
                return None
            if seq_i < prev_i:
                # Documented maintenance reset; accept and continue.
                pass
            elif prev_i not in {self.seq_id, seq_i}:
                return "seq_break"
        self._merge(self.asks, row.get("asks") or [])
        self._merge(self.bids, row.get("bids") or [])
        self.seq_id = seq_i if seq_i is not None else self.seq_id
        self.ts_ms = int(row.get("ts") or self.ts_ms)
        checksum = row.get("checksum")
        if checksum not in (None, 0, "0", ""):
            levels_b = self._levels(self.bids, reverse=True)
            levels_a = self._levels(self.asks, reverse=False)
            if book_crc32(levels_b, levels_a) != int(checksum):
                return "checksum"
        return None

    def _merge(self, side: dict[str, list[Any]], rows: Iterable[Any]) -> None:
        for item in rows:
            if not item:
                continue
            px = str(item[0])
            sz = str(item[1]) if len(item) > 1 else "0"
            if float(sz) == 0.0:
                side.pop(px, None)
            else:
                side[px] = list(item)

    def _levels(self, side: Mapping[str, list[Any]], *, reverse: bool) -> list[list[Any]]:
        keys = sorted(side, key=lambda px: float(px), reverse=reverse)
        return [side[k] for k in keys]


class EventQueue:
    """Strictly bounded queue. Consecutive overflow drops become one gap."""

    def __init__(self, maxsize: int) -> None:
        if maxsize < 1:
            raise ValueError("queue_maxsize must be at least 1")
        self.maxsize = maxsize
        self.items: deque[StreamMessage | None] = deque()
        self._ready = asyncio.Event()
        self.overflow_count = 0

    def overflow_gap_dropped(self) -> int:
        total = 0
        for item in self.items:
            if item is not None and item.kind == "gap" and item.gap is not None and item.gap.reason == "overflow":
                total += item.gap.dropped
        return total

    def put(self, msg: StreamMessage | None, *, control: bool = False) -> StreamMessage | None:
        del control
        if msg is None:
            self.items.append(None)
            self._ready.set()
            return None
        if msg.kind == "gap" and msg.gap is not None and msg.gap.reason == "overflow":
            if self._merge_overflow(msg):
                self._ready.set()
                return None
        while len(self.items) >= self.maxsize:
            if not self._evict():
                break
        if len(self.items) >= self.maxsize:
            if msg.kind == "data":
                self.overflow_count += 1
                if not self._absorb_data(msg, allow_insert=False):
                    self._bump_any_overflow()
                self._ready.set()
                return None
            return None
        self.items.append(msg)
        self._ready.set()
        return None

    def _evict(self) -> bool:
        for index, item in enumerate(self.items):
            if item is None:
                continue
            if item.kind == "gap" and item.gap is not None and item.gap.reason == "overflow":
                continue
            del self.items[index]
            if item.kind == "data":
                self.overflow_count += 1
                self._absorb_data(item, allow_insert=True)
            return True
        return False

    def _absorb_data(self, dropped: StreamMessage, *, allow_insert: bool) -> bool:
        for index, item in enumerate(self.items):
            if _is_overflow_gap(item, dropped.channel, dropped.inst_id):
                self.items[index] = _extend_overflow_gap(item, dropped)
                return True
        if not allow_insert:
            return False
        gap = _gap_from_message(dropped, reason="overflow", dropped=1)
        self.items.appendleft(
            StreamMessage(
                kind="gap",
                channel=dropped.channel,
                inst_id=dropped.inst_id,
                ts_ms=dropped.ts_ms,
                gap=gap,
                data=gap,
            )
        )
        return True

    def _merge_overflow(self, msg: StreamMessage) -> bool:
        if msg.gap is None:
            return False
        for index, item in enumerate(self.items):
            if _is_overflow_gap(item, msg.channel, msg.inst_id) and item is not None and item.gap is not None:
                merged = GapInfo(
                    channel=item.gap.channel,
                    inst_id=item.gap.inst_id,
                    reason="overflow",
                    first_missing_trade_id=_min_opt(item.gap.first_missing_trade_id, msg.gap.first_missing_trade_id),
                    last_missing_trade_id=_max_opt(item.gap.last_missing_trade_id, msg.gap.last_missing_trade_id),
                    start_ts_ms=_min_opt(item.gap.start_ts_ms, msg.gap.start_ts_ms),
                    end_ts_ms=_max_opt(item.gap.end_ts_ms, msg.gap.end_ts_ms),
                    dropped=item.gap.dropped + msg.gap.dropped,
                )
                self.items[index] = StreamMessage(
                    kind="gap",
                    channel=merged.channel,
                    inst_id=merged.inst_id,
                    ts_ms=msg.ts_ms,
                    gap=merged,
                    data=merged,
                )
                return True
        return False

    def _bump_any_overflow(self) -> None:
        for index, item in enumerate(self.items):
            if item is not None and item.kind == "gap" and item.gap is not None and item.gap.reason == "overflow":
                gap = item.gap
                bumped = GapInfo(
                    channel=gap.channel,
                    inst_id=gap.inst_id,
                    reason="overflow",
                    first_missing_trade_id=gap.first_missing_trade_id,
                    last_missing_trade_id=gap.last_missing_trade_id,
                    start_ts_ms=gap.start_ts_ms,
                    end_ts_ms=gap.end_ts_ms,
                    dropped=gap.dropped + 1,
                )
                self.items[index] = StreamMessage(
                    kind="gap",
                    channel=bumped.channel,
                    inst_id=bumped.inst_id,
                    ts_ms=bumped.end_ts_ms or 0,
                    gap=bumped,
                    data=bumped,
                )
                return

    async def get(self) -> StreamMessage | None:
        while not self.items:
            self._ready.clear()
            await self._ready.wait()
        item = self.items.popleft()
        if not self.items:
            self._ready.clear()
        return item


class AsyncRateLimiter:
    def __init__(
        self,
        max_requests: int,
        window: float,
        *,
        clock: Clock | None = None,
        sleep: AsyncSleeper | None = None,
    ) -> None:
        self.max_requests = max_requests
        self.window = window
        self._clock = clock or time.monotonic
        self._sleep = sleep or asyncio.sleep
        self._hits: list[float] = []

    async def acquire(self) -> None:
        now = self._clock()
        cutoff = now - self.window
        self._hits = [ts for ts in self._hits if ts > cutoff]
        if len(self._hits) >= self.max_requests:
            waited = max(0.0, self._hits[0] + self.window - now)
            if waited > 0:
                await self._sleep(waited)
            now = self._clock()
            cutoff = now - self.window
            self._hits = [ts for ts in self._hits if ts > cutoff]
        self._hits.append(now)


def validate_subscriptions(
    subscriptions: Sequence[tuple[str, str] | Mapping[str, str]],
) -> list[dict[str, str]]:
    args: list[dict[str, str]] = []
    for item in subscriptions:
        if isinstance(item, Mapping):
            channel = str(item.get("channel") or "")
            inst_id = str(item.get("instId") or item.get("inst_id") or "")
        else:
            channel, inst_id = item
        if not channel:
            raise PrivateChannelError("refusing empty WebSocket channel")
        if channel.lower() == "login" or channel in endpoints.PRIVATE_WS_CHANNELS:
            raise PrivateChannelError(
                f"refusing private channel {channel!r}; stage-1 streams are public/keyless only"
            )
        if not endpoints.is_public_ws_channel(channel):
            raise PrivateChannelError(
                f"refusing channel {channel!r}; not on the stage-1 public allowlist"
            )
        args.append({"channel": channel, "instId": endpoints.validate_inst_id(inst_id)})
    if not args:
        raise ValueError("at least one public subscription is required")
    return args


def _assert_public_op(op: str) -> None:
    if op == "login" or op.startswith("login"):
        raise PrivateChannelError(
            "refusing WebSocket op 'login'; stage-1 streams are public/keyless only"
        )
    if op not in {"subscribe", "unsubscribe"}:
        raise PrivateChannelError(f"refusing WebSocket op {op!r}")


class OkxPublicStream:
    """Async iterator over public OKX channels.

    Example::

        async with OkxPublicStream([("trades", "SPCX-USDT-SWAP")]) as stream:
            async for msg in stream:
                if stream.is_stale(msg.channel, msg.inst_id):
                    continue
    """

    def __init__(
        self,
        subscriptions: Sequence[tuple[str, str] | Mapping[str, str]],
        *,
        public_url: str | None = None,
        business_url: str | None = None,
        rest: PublicClient | None = None,
        recorder: StreamRecorder | None = None,
        record_channels: Sequence[str] | None = None,
        connector: Connector | None = None,
        allow_unofficial_hosts: bool = False,
        allow_insecure: bool = False,
        queue_maxsize: int = DEFAULT_QUEUE_MAX,
        ping_interval: float = DEFAULT_PING_INTERVAL,
        stale_timeout: float = DEFAULT_STALE_TIMEOUT,
        reconnect_base: float = DEFAULT_RECONNECT_BASE,
        reconnect_max: float = DEFAULT_RECONNECT_MAX,
        handshake_timeout: float = DEFAULT_HANDSHAKE_TIMEOUT,
        healthy_after: float = DEFAULT_HEALTHY_AFTER,
        clock: Clock | None = None,
        sleep: AsyncSleeper | None = None,
    ) -> None:
        self.args = validate_subscriptions(subscriptions)
        self.allow_unofficial_hosts = allow_unofficial_hosts
        self.allow_insecure = allow_insecure
        self.public_url = endpoints.resolve_ws_url(
            public_url,
            env_name=endpoints.PUBLIC_WS_URL_ENV,
            default=endpoints.DEFAULT_PUBLIC_WS_URL,
            allow_unofficial_hosts=allow_unofficial_hosts,
            allow_insecure=allow_insecure,
        )
        self.business_url = endpoints.resolve_ws_url(
            business_url,
            env_name=endpoints.BUSINESS_WS_URL_ENV,
            default=endpoints.DEFAULT_BUSINESS_WS_URL,
            allow_unofficial_hosts=allow_unofficial_hosts,
            allow_insecure=allow_insecure,
        )
        self.rest = rest
        self.recorder = recorder or StreamRecorder.disabled()
        if record_channels is not None:
            self.recorder.channels = frozenset(record_channels)
            self.recorder.enabled = True
        self._connector = connector
        self.queue_maxsize = queue_maxsize
        self.ping_interval = ping_interval
        self.stale_timeout = stale_timeout
        self.reconnect_base = reconnect_base
        self.reconnect_max = reconnect_max
        self.handshake_timeout = handshake_timeout
        self.healthy_after = healthy_after
        self.fallbacks: list[tuple[str, str]] = []
        self._url_override: dict[str, str] = {}
        self._clock = clock or time.monotonic
        self._sleep = sleep or asyncio.sleep
        self._queue = EventQueue(queue_maxsize)
        self.record_error: BaseException | None = None
        self._stale: set[tuple[str, str]] = set()
        self._last_data_at: dict[tuple[str, str], float] = {}
        self._last_trade_id: dict[str, int] = {}
        self._books: dict[str, LocalBook] = {}
        self._running = False
        self._cancel = threading.Event()
        self._tasks: list[asyncio.Task[Any]] = []
        self._reconnect_generation = 0
        self._connect_limiter = AsyncRateLimiter(*endpoints.WS_CONNECT_RATE, clock=self._clock, sleep=self._sleep)
        self._sub_limiter = AsyncRateLimiter(*endpoints.WS_SUBSCRIBE_RATE, clock=self._clock, sleep=self._sleep)
        self._seen_trade_ids: dict[str, set[int]] = {}
        self._seen_cap = 50_000

    @property
    def overflow_count(self) -> int:
        return self._queue.overflow_count

    def is_stale(self, channel: str, inst_id: str) -> bool:
        return (channel, inst_id) in self._stale

    @property
    def stale_subscriptions(self) -> frozenset[tuple[str, str]]:
        return frozenset(self._stale)

    async def __aenter__(self) -> "OkxPublicStream":
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.stop()

    def __aiter__(self) -> AsyncIterator[StreamMessage]:
        return self

    async def __anext__(self) -> StreamMessage:
        item = await self._queue.get()
        if item is None:
            raise StopAsyncIteration
        return item

    async def start(self) -> None:
        if self._running:
            return
        if self._connector is None:
            self._connector = WebsocketsConnector(
                allow_unofficial_hosts=self.allow_unofficial_hosts,
                allow_insecure=self.allow_insecure,
            )
        self._cancel.clear()
        self.recorder.start()
        self._running = True
        public_args = [a for a in self.args if not endpoints.is_candle_channel(a["channel"])]
        business_args = [a for a in self.args if endpoints.is_candle_channel(a["channel"])]
        now = self._clock()
        for arg in self.args:
            self._last_data_at[(arg["channel"], arg["instId"])] = now
        if public_args:
            self._tasks.append(asyncio.create_task(self._run_socket(self.public_url, public_args), name="okx-ws-public"))
        if business_args:
            self._tasks.append(asyncio.create_task(self._run_socket(self.business_url, business_args), name="okx-ws-business"))
        self._tasks.append(asyncio.create_task(self._watchdog(), name="okx-ws-watchdog"))

    async def stop(self) -> None:
        self._running = False
        self._cancel.set()
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks.clear()
        self.recorder.close()
        self._queue.put(None, control=True)

    async def _connect(self, url: str) -> WsConnection:
        assert self._connector is not None
        endpoints.assert_okx_ws_url(
            url,
            allow_unofficial_hosts=self.allow_unofficial_hosts,
            allow_insecure=self.allow_insecure,
        )
        return await asyncio.wait_for(self._connector(url), timeout=self.handshake_timeout)

    async def _open_socket(self, url: str) -> WsConnection:
        target = self._url_override.get(url, url)
        endpoints.assert_okx_ws_url(
            target,
            allow_unofficial_hosts=self.allow_unofficial_hosts,
            allow_insecure=self.allow_insecure,
        )
        try:
            return await self._connect(target)
        except (TimeoutError, asyncio.TimeoutError):
            fallback = endpoints.fallback_ws_port_443(
                target,
                allow_unofficial_hosts=self.allow_unofficial_hosts,
                allow_insecure=self.allow_insecure,
            )
            if fallback is None:
                raise
            endpoints.assert_same_ws_host_path(target, fallback)
            endpoints.assert_okx_ws_url(
                fallback,
                allow_unofficial_hosts=self.allow_unofficial_hosts,
                allow_insecure=self.allow_insecure,
            )
            _LOG.warning(
                "OKX WebSocket handshake to %s timed out; falling back to %s (port only)",
                target,
                fallback,
            )
            self.fallbacks.append((target, fallback))
            await self._connect_limiter.acquire()
            conn = await self._connect(fallback)
            self._url_override[url] = fallback
            return conn

    async def _run_socket(self, url: str, args: list[dict[str, str]]) -> None:
        delay = self.reconnect_base
        while self._running:
            conn: WsConnection | None = None
            connected_at: float | None = None
            try:
                await self._connect_limiter.acquire()
                conn = await self._open_socket(url)
                await self._subscribe(conn, args)
                self._reconnect_generation += 1
                connected_at = self._clock()
                await self._reader(conn)
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            finally:
                if conn is not None:
                    try:
                        await conn.close()
                    except Exception:
                        pass
            if not self._running:
                return
            lived = 0.0 if connected_at is None else self._clock() - connected_at
            if lived >= self.healthy_after:
                delay = self.reconnect_base
            jitter = random.uniform(0, max(0.05, delay * 0.2))
            await self._sleep(min(self.reconnect_max, delay) + jitter)
            delay = min(self.reconnect_max, delay * 2)

    async def _send_op(self, conn: WsConnection, op: str, args: list[dict[str, str]], req_id: str) -> None:
        _assert_public_op(op)
        await conn.send(json.dumps({"id": req_id, "op": op, "args": args}))

    async def _subscribe(self, conn: WsConnection, args: list[dict[str, str]]) -> None:
        await self._sub_limiter.acquire()
        await self._send_op(conn, "subscribe", args, "1")

    async def _unsubscribe(self, conn: WsConnection, args: list[dict[str, str]]) -> None:
        await self._sub_limiter.acquire()
        await self._send_op(conn, "unsubscribe", args, "2")

    async def _reader(self, conn: WsConnection) -> None:
        last_rx = self._clock()
        while self._running:
            timeout = self.ping_interval - (self._clock() - last_rx)
            try:
                if timeout <= 0:
                    await conn.send("ping")
                    raw = await asyncio.wait_for(conn.recv(), timeout=self.ping_interval)
                else:
                    raw = await asyncio.wait_for(conn.recv(), timeout=timeout)
            except (TimeoutError, asyncio.TimeoutError):
                if timeout <= 0:
                    return
                continue
            except Exception:
                return
            last_rx = self._clock()
            if raw is None:
                return
            if raw == "pong":
                continue
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", "replace")
            await self._dispatch(raw, conn)

    async def _dispatch(self, raw: str, conn: WsConnection) -> None:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return
        if not isinstance(payload, dict):
            return
        event = payload.get("event")
        if event in {"subscribe", "unsubscribe", "error", "login", "notice"}:
            return
        arg = payload.get("arg") or {}
        channel = str(arg.get("channel") or "")
        inst_id = str(arg.get("instId") or "")
        rows = payload.get("data") or []
        if channel == "trades":
            await self._on_trades(inst_id, rows, payload)
            return
        if channel == "books":
            await self._on_books(inst_id, payload, conn)
            return
        if channel:
            self._touch(channel, inst_id)
            ts_ms = 0
            if rows and isinstance(rows[0], dict):
                ts_ms = int(rows[0].get("ts") or 0)
            msg = StreamMessage(
                kind="data",
                channel=channel,
                inst_id=inst_id,
                ts_ms=ts_ms,
                data=rows,
                stale=False,
                raw=payload,
            )
            self._emit(msg)
            self._record_data(msg)

    async def _on_trades(self, inst_id: str, rows: list[Any], raw: dict[str, Any]) -> None:
        self._touch("trades", inst_id)
        parsed: list[tuple[Trade, int]] = []
        for row in rows:
            trade = Trade.from_dict(row)
            count = 1
            raw_count = row.get("count") if isinstance(row, Mapping) else None
            if raw_count not in (None, ""):
                count = max(int(raw_count), 1)
            parsed.append((trade, count))
        parsed.sort(key=lambda item: (item[0].ts_ms, item[0].trade_id))
        seen = self._seen_trade_ids.setdefault(inst_id, set())
        last = self._last_trade_id.get(inst_id)
        for trade, count in parsed:
            if trade.trade_id in seen or (last is not None and trade.trade_id <= last):
                continue
            # ``trades`` aggregates: tradeId is the last id, ``count`` is how
            # many consecutive ids the message already covers.
            covered_start = trade.trade_id - count + 1
            if last is not None and covered_start > last + 1:
                filled = await self._backfill(inst_id, last + 1, covered_start - 1)
                if filled is None:
                    gap = GapInfo(
                        channel="trades",
                        inst_id=inst_id,
                        reason="backfill_failed",
                        first_missing_trade_id=last + 1,
                        last_missing_trade_id=covered_start - 1,
                        start_ts_ms=None,
                        end_ts_ms=trade.ts_ms,
                        dropped=covered_start - last - 1,
                    )
                    self._emit_gap(gap)
                else:
                    for item in filled:
                        if item.trade_id in seen or (last is not None and item.trade_id <= last):
                            continue
                        self._emit_trade(item, raw)
                        seen.add(item.trade_id)
                        last = item.trade_id
            self._emit_trade(trade, raw)
            seen.add(trade.trade_id)
            last = trade.trade_id
            if len(seen) > self._seen_cap:
                seen.clear()
                if last is not None:
                    seen.add(last)
        if last is not None:
            self._last_trade_id[inst_id] = last

    def _emit_trade(self, trade: Trade, raw: dict[str, Any] | None = None) -> None:
        msg = StreamMessage(
            kind="data",
            channel="trades",
            inst_id=trade.inst_id,
            ts_ms=trade.ts_ms,
            data=trade,
            stale=False,
            raw=raw,
        )
        self._emit(msg)
        self._record_data(msg)

    async def _backfill(self, inst_id: str, start_id: int, end_id: int) -> list[Trade] | None:
        if self.rest is None or end_id < start_id:
            return None
        try:
            trades = await asyncio.to_thread(
                history_trades_range,
                self.rest,
                inst_id,
                start_trade_id=start_id,
                end_trade_id=end_id,
                pagination="tradeId",
                should_cancel=self._cancel.is_set,
            )
        except asyncio.CancelledError:
            self._cancel.set()
            raise
        except InterruptedError:
            return None
        except Exception:
            return None
        have = {trade.trade_id for trade in trades}
        expected = set(range(start_id, end_id + 1))
        if not expected <= have:
            return None
        return sorted(trades, key=lambda t: (t.ts_ms, t.trade_id))

    async def _on_books(self, inst_id: str, payload: dict[str, Any], conn: WsConnection) -> None:
        self._touch("books", inst_id)
        action = str(payload.get("action") or "snapshot")
        book = self._books.setdefault(inst_id, LocalBook())
        for row in payload.get("data") or []:
            reason = book.apply(row, action=action)
            if reason:
                self._books[inst_id] = LocalBook()
                self._emit(
                    StreamMessage(
                        kind="book_resync",
                        channel="books",
                        inst_id=inst_id,
                        ts_ms=int(row.get("ts") or 0),
                        data={"reason": reason},
                        raw=payload,
                    )
                )
                try:
                    args = [{"channel": "books", "instId": inst_id}]
                    await self._unsubscribe(conn, args)
                    await self._subscribe(conn, args)
                except Exception:
                    return
                return
            snapshot = book.snapshot()
            msg = StreamMessage(
                kind="data",
                channel="books",
                inst_id=inst_id,
                ts_ms=book.ts_ms,
                data=snapshot,
                raw=payload,
            )
            self._emit(msg)
            if self.recorder.records_channel("books"):
                try:
                    self.recorder.maybe_write_books_snapshot(inst_id, snapshot, ts_ms=book.ts_ms)
                except (LowDiskError, CacheBudgetExceeded) as exc:
                    self.record_error = exc
                    self._emit_record_error(exc)

    def _touch(self, channel: str, inst_id: str) -> None:
        key = (channel, inst_id)
        was_stale = key in self._stale
        self._last_data_at[key] = self._clock()
        if was_stale:
            self._stale.discard(key)
            self._emit(
                StreamMessage(
                    kind="recovered",
                    channel=channel,
                    inst_id=inst_id,
                    ts_ms=int(self._clock() * 1000),
                    stale=False,
                )
            )

    async def _watchdog(self) -> None:
        tick = min(1.0, max(0.01, self.stale_timeout / 4.0))
        while self._running:
            await self._sleep(tick)
            now = self._clock()
            for arg in self.args:
                key = (arg["channel"], arg["instId"])
                last = self._last_data_at.get(key, now)
                if now - last >= self.stale_timeout and key not in self._stale:
                    self._stale.add(key)
                    self._emit(
                        StreamMessage(
                            kind="stale",
                            channel=key[0],
                            inst_id=key[1],
                            ts_ms=int(now * 1000),
                            stale=True,
                        )
                    )

    def _emit(self, msg: StreamMessage) -> None:
        self._queue.put(msg, control=msg.kind != "data")

    def _emit_gap(self, gap: GapInfo) -> None:
        msg = StreamMessage(
            kind="gap",
            channel=gap.channel,
            inst_id=gap.inst_id,
            ts_ms=gap.end_ts_ms or 0,
            gap=gap,
            data=gap,
        )
        self._queue.put(msg, control=True)
        if gap.reason != "overflow" and self.recorder.records_channel(gap.channel):
            try:
                self.recorder.write_gap(gap)
            except (LowDiskError, CacheBudgetExceeded) as exc:
                self.record_error = exc
                self._emit_record_error(exc)

    def _record_data(self, msg: StreamMessage) -> None:
        if not self.recorder.records_channel(msg.channel) or msg.channel == "books":
            return
        payload = msg.data
        if isinstance(payload, Trade):
            payload = {
                "instId": payload.inst_id,
                "tradeId": str(payload.trade_id),
                "px": str(payload.price),
                "sz": str(payload.size),
                "side": payload.side,
                "ts": str(payload.ts_ms),
            }
        try:
            self.recorder.write_data(msg.channel, msg.inst_id, payload, ts_ms=msg.ts_ms)
        except (LowDiskError, CacheBudgetExceeded) as exc:
            self.record_error = exc
            self._emit_record_error(exc)

    def _emit_record_error(self, exc: BaseException) -> None:
        kind = "low_disk" if isinstance(exc, LowDiskError) else "record_error"
        self._queue.put(
            StreamMessage(
                kind=kind,
                channel="",
                inst_id="",
                data={"reason": self.recorder.stopped_reason() or kind, "error": str(exc)},
            ),
            control=True,
        )


def _min_opt(left: int | None, right: int | None) -> int | None:
    if left is None:
        return right
    if right is None:
        return left
    return min(int(left), int(right))


def _max_opt(left: int | None, right: int | None) -> int | None:
    if left is None:
        return right
    if right is None:
        return left
    return max(int(left), int(right))


def _is_overflow_gap(item: StreamMessage | None, channel: str, inst_id: str) -> bool:
    return (
        item is not None
        and item.kind == "gap"
        and item.gap is not None
        and item.gap.reason == "overflow"
        and item.channel == channel
        and item.inst_id == inst_id
    )


def _extend_overflow_gap(item: StreamMessage, dropped: StreamMessage) -> StreamMessage:
    gap = item.gap
    extra = _gap_from_message(dropped, reason="overflow", dropped=1)
    if gap is None:
        return StreamMessage(
            kind="gap",
            channel=dropped.channel,
            inst_id=dropped.inst_id,
            ts_ms=dropped.ts_ms,
            gap=extra,
            data=extra,
        )
    merged = GapInfo(
        channel=gap.channel,
        inst_id=gap.inst_id,
        reason="overflow",
        first_missing_trade_id=_min_opt(gap.first_missing_trade_id, extra.first_missing_trade_id),
        last_missing_trade_id=_max_opt(gap.last_missing_trade_id, extra.last_missing_trade_id),
        start_ts_ms=_min_opt(gap.start_ts_ms, extra.start_ts_ms),
        end_ts_ms=_max_opt(gap.end_ts_ms, extra.end_ts_ms),
        dropped=gap.dropped + extra.dropped,
    )
    return StreamMessage(
        kind="gap",
        channel=merged.channel,
        inst_id=merged.inst_id,
        ts_ms=merged.end_ts_ms or dropped.ts_ms,
        gap=merged,
        data=merged,
    )


def _gap_from_message(msg: StreamMessage, *, reason: str, dropped: int) -> GapInfo:
    first = last = None
    start = end = msg.ts_ms or None
    if isinstance(msg.data, Trade):
        first = last = msg.data.trade_id
        start = end = msg.data.ts_ms
    return GapInfo(
        channel=msg.channel,
        inst_id=msg.inst_id,
        reason=reason,
        first_missing_trade_id=first,
        last_missing_trade_id=last,
        start_ts_ms=start,
        end_ts_ms=end,
        dropped=dropped,
    )


def run_public_stream(
    subscriptions: Sequence[tuple[str, str] | Mapping[str, str]],
    *,
    seconds: float = 60.0,
    on_message: Callable[[StreamMessage], None] | None = None,
    **kwargs: Any,
) -> list[StreamMessage]:
    """Blocking helper around :class:`OkxPublicStream`."""

    async def _run() -> list[StreamMessage]:
        out: list[StreamMessage] = []
        async with OkxPublicStream(subscriptions, **kwargs) as stream:
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                timeout = max(0.01, end - time.monotonic())
                try:
                    msg = await asyncio.wait_for(anext(stream), timeout=timeout)
                except (StopAsyncIteration, TimeoutError, asyncio.TimeoutError):
                    break
                out.append(msg)
                if on_message is not None:
                    on_message(msg)
        return out

    return asyncio.run(_run())
