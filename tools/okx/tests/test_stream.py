from __future__ import annotations

import asyncio
import functools
import gzip
import inspect
import json
import random
import time
from pathlib import Path
from urllib.parse import urlparse

import pytest

from tools.okx import endpoints
from tools.okx.errors import PrivateChannelError, UnsafeEndpointError
from tools.okx.record import StreamRecorder
from tools.okx.stream import (
    DEFAULT_PING_INTERVAL,
    DEFAULT_RECONNECT_MAX,
    GapInfo,
    LagTracker,
    OkxPublicStream,
    _assert_public_op,
    book_crc32,
    validate_subscriptions,
)
from tools.okx.tests.conftest import FakeTransport, make_client
from tools.okx.types import Trade


def run_async(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        return asyncio.run(func(*args, **kwargs))

    wrapper.__signature__ = inspect.signature(func)
    return wrapper


class MemorySocket:
    def __init__(self) -> None:
        self.incoming: asyncio.Queue[str] = asyncio.Queue()
        self.outgoing: asyncio.Queue[str | None] = asyncio.Queue()
        self.sent: list[str] = []
        self.closed = False

    async def send(self, data: str) -> None:
        self.sent.append(data)
        await self.incoming.put(data)

    async def recv(self) -> str:
        item = await self.outgoing.get()
        if item is None:
            raise ConnectionError("closed")
        return item

    async def close(self) -> None:
        self.closed = True
        await self.outgoing.put(None)

    async def push(self, payload: dict | str) -> None:
        text = payload if isinstance(payload, str) else json.dumps(payload)
        await self.outgoing.put(text)


class ScriptedConnector:
    def __init__(self) -> None:
        self.ready: list[MemorySocket] = []
        self.urls: list[str] = []
        self._i = 0

    def add(self) -> MemorySocket:
        sock = MemorySocket()
        self.ready.append(sock)
        return sock

    async def __call__(self, url: str) -> MemorySocket:
        self.urls.append(url)
        if self._i >= len(self.ready):
            self.add()
        sock = self.ready[self._i]
        self._i += 1
        return sock


def _trade_push(inst_id: str, trade_id: int, ts_ms: int | None = None, count: int = 1) -> dict:
    stamp = ts_ms if ts_ms is not None else 1_700_000_000_000 + trade_id
    return {
        "arg": {"channel": "trades", "instId": inst_id},
        "data": [
            {
                "instId": inst_id,
                "tradeId": str(trade_id),
                "px": "1.0",
                "sz": "1",
                "side": "buy",
                "ts": str(stamp),
                "count": str(count),
            }
        ],
    }


def _book_push(
    inst_id: str,
    *,
    action: str,
    bids: list,
    asks: list,
    seq: int,
    prev: int,
    checksum: int,
    ts: str = "1",
) -> dict:
    return {
        "arg": {"channel": "books", "instId": inst_id},
        "action": action,
        "data": [
            {
                "bids": bids,
                "asks": asks,
                "seqId": seq,
                "prevSeqId": prev,
                "checksum": checksum,
                "ts": ts,
            }
        ],
    }


async def _wait_sent(sock: MemorySocket, pred, timeout: float = 1.0) -> str:
    end = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < end:
        for item in sock.sent:
            if pred(item):
                return item
        await asyncio.sleep(0.01)
    raise AssertionError(f"nothing sent matching predicate; got {sock.sent!r}")


async def _collect(stream: OkxPublicStream, n: int, timeout: float = 2.0) -> list:
    out = []
    for _ in range(n):
        out.append(await asyncio.wait_for(anext(stream), timeout=timeout))
    return out


INST = "BTC-USDT-SWAP"


def _tape_coverage(rec: StreamRecorder) -> tuple[int, int]:
    rows: list[dict] = []
    for path in rec.files_written:
        if path.is_file():
            rows.extend(json.loads(line) for line in gzip.open(path, "rt", encoding="utf-8") if line.strip())
    recorded = sum(1 for row in rows if row.get("event") == "data")
    covered = sum(int(row.get("dropped") or 0) for row in rows if row.get("event") == "gap")
    return recorded, covered


def _assert_gzip_readable(path: Path) -> None:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                json.loads(line)


@run_async
async def test_subscribe_and_trade_push() -> None:
    broker = ScriptedConnector()
    sock = broker.add()
    async with OkxPublicStream(
        [("trades", INST)],
        connector=broker,
        ping_interval=30,
        stale_timeout=30,
    ) as stream:
        raw = await _wait_sent(sock, lambda s: s.startswith("{") and "subscribe" in s)
        payload = json.loads(raw)
        assert payload["op"] == "subscribe"
        assert payload["args"] == [{"channel": "trades", "instId": INST}]
        await sock.push(_trade_push(INST, 7))
        msg = await asyncio.wait_for(anext(stream), timeout=1)
    assert msg.kind == "data"
    assert isinstance(msg.data, Trade)
    assert msg.data.trade_id == 7


@run_async
async def test_ping_pong() -> None:
    broker = ScriptedConnector()
    sock = broker.add()
    async with OkxPublicStream(
        [("tickers", INST)],
        connector=broker,
        ping_interval=0.05,
        stale_timeout=30,
    ) as stream:
        await _wait_sent(sock, lambda s: s.startswith("{"))
        ping = await _wait_sent(sock, lambda s: s == "ping", timeout=1.0)
        assert ping == "ping"
        await sock.push("pong")
        await asyncio.sleep(0.02)
        assert stream.overflow_count == 0


@run_async
async def test_reconnect_resubscribes() -> None:
    broker = ScriptedConnector()
    first = broker.add()
    second = broker.add()
    async with OkxPublicStream(
        [("trades", INST)],
        connector=broker,
        ping_interval=30,
        stale_timeout=30,
        reconnect_base=0.01,
        reconnect_max=0.02,
    ) as stream:
        await _wait_sent(first, lambda s: "subscribe" in s)
        await first.push(_trade_push(INST, 1))
        first_msg = await asyncio.wait_for(anext(stream), timeout=1)
        assert first_msg.data.trade_id == 1
        await first.close()
        await _wait_sent(second, lambda s: "subscribe" in s, timeout=1.0)
        await second.push(_trade_push(INST, 2))
        second_msg = await asyncio.wait_for(anext(stream), timeout=1)
    assert second_msg.data.trade_id == 2
    assert len(broker.urls) >= 2


def _history_rows(ids: list[int]) -> dict:
    return {
        "code": "0",
        "data": [
            {
                "instId": INST,
                "tradeId": str(i),
                "px": "1",
                "sz": "1",
                "side": "buy",
                "ts": str(1_700_000_000_000 + i),
            }
            for i in reversed(ids)
        ],
    }


@run_async
async def test_trade_gap_backfill_and_dedupe() -> None:
    broker = ScriptedConnector()
    sock = broker.add()
    transport = FakeTransport()
    transport.add(
        "/api/v5/market/history-trades",
        _history_rows([11, 12, 13, 14]),
    )
    rest = make_client(transport)
    async with OkxPublicStream(
        [("trades", INST)],
        connector=broker,
        rest=rest,
        ping_interval=30,
        stale_timeout=30,
    ) as stream:
        await _wait_sent(sock, lambda s: "subscribe" in s)
        await sock.push(_trade_push(INST, 10))
        await sock.push(_trade_push(INST, 15))
        await sock.push(_trade_push(INST, 15))
        msgs = await _collect(stream, 6)
    ids = [m.data.trade_id for m in msgs if m.kind == "data"]
    assert ids == [10, 11, 12, 13, 14, 15]
    assert all(m.kind != "gap" for m in msgs)


@run_async
async def test_aggregated_trade_count_is_not_a_gap() -> None:
    broker = ScriptedConnector()
    sock = broker.add()
    async with OkxPublicStream(
        [("trades", INST)],
        connector=broker,
        rest=None,
        ping_interval=30,
        stale_timeout=30,
    ) as stream:
        await _wait_sent(sock, lambda s: "subscribe" in s)
        await sock.push(_trade_push(INST, 10, count=1))
        await sock.push(_trade_push(INST, 15, count=5))
        msgs = await _collect(stream, 2)
    assert [m.kind for m in msgs] == ["data", "data"]
    assert [m.data.trade_id for m in msgs] == [10, 15]


@run_async
async def test_backfill_failure_emits_and_records_gap(tmp_path: Path) -> None:
    broker = ScriptedConnector()
    sock = broker.add()
    rec = StreamRecorder(cache_dir=tmp_path, enabled=True)
    async with OkxPublicStream(
        [("trades", INST)],
        connector=broker,
        rest=None,
        recorder=rec,
        ping_interval=30,
        stale_timeout=30,
    ) as stream:
        await _wait_sent(sock, lambda s: "subscribe" in s)
        await sock.push(_trade_push(INST, 10))
        await sock.push(_trade_push(INST, 15))
        msgs = await _collect(stream, 3)
    kinds = [m.kind for m in msgs]
    assert "gap" in kinds
    gap = next(m.gap for m in msgs if m.kind == "gap")
    assert isinstance(gap, GapInfo)
    assert gap.reason == "backfill_failed"
    assert gap.first_missing_trade_id == 11
    assert gap.last_missing_trade_id == 14
    rec.close()
    import gzip

    text = gzip.open(rec.files_written[0], "rt", encoding="utf-8").read()
    assert '"event":"gap"' in text
    assert "backfill_failed" in text


@run_async
async def test_incomplete_backfill_is_a_gap() -> None:
    """Fails if the completeness check (`expected <= have`) is removed."""
    broker = ScriptedConnector()
    sock = broker.add()
    transport = FakeTransport()
    transport.add("/api/v5/market/history-trades", _history_rows([11, 12, 13]))
    rest = make_client(transport)
    async with OkxPublicStream(
        [("trades", INST)],
        connector=broker,
        rest=rest,
        ping_interval=30,
        stale_timeout=30,
    ) as stream:
        await _wait_sent(sock, lambda s: "subscribe" in s)
        await sock.push(_trade_push(INST, 10))
        await sock.push(_trade_push(INST, 15))
        msgs = await _collect(stream, 3)
    gap = next(m.gap for m in msgs if m.kind == "gap")
    assert gap.reason == "backfill_failed"
    assert gap.first_missing_trade_id == 11
    assert gap.last_missing_trade_id == 14
    assert [m.data.trade_id for m in msgs if m.kind == "data"] == [10, 15]


@run_async
async def test_stale_flag_and_recovery() -> None:
    broker = ScriptedConnector()
    sock = broker.add()
    async with OkxPublicStream(
        [("tickers", INST)],
        connector=broker,
        ping_interval=30,
        stale_timeout=0.05,
    ) as stream:
        await _wait_sent(sock, lambda s: "subscribe" in s)
        stale = await asyncio.wait_for(anext(stream), timeout=1)
        assert stale.kind == "stale"
        assert stream.is_stale("tickers", INST)
        await sock.push(
            {
                "arg": {"channel": "tickers", "instId": INST},
                "data": [{"instId": INST, "last": "1", "ts": "1"}],
            }
        )
        recovered = await asyncio.wait_for(anext(stream), timeout=1)
        data = await asyncio.wait_for(anext(stream), timeout=1)
    assert recovered.kind == "recovered"
    assert data.kind == "data"
    assert not stream.is_stale("tickers", INST)


@run_async
async def test_book_checksum_mismatch_resyncs() -> None:
    broker = ScriptedConnector()
    sock = broker.add()
    bids = [["100", "1", "0", "1"]]
    asks = [["101", "2", "0", "1"]]
    crc = book_crc32(bids, asks)
    async with OkxPublicStream(
        [("books", INST)],
        connector=broker,
        ping_interval=30,
        stale_timeout=30,
    ) as stream:
        await _wait_sent(sock, lambda s: "subscribe" in s)
        await sock.push(_book_push(INST, action="snapshot", bids=bids, asks=asks, seq=1, prev=-1, checksum=crc))
        snap = await asyncio.wait_for(anext(stream), timeout=1)
        assert snap.kind == "data"
        await sock.push(
            _book_push(INST, action="update", bids=[["100", "2", "0", "1"]], asks=asks, seq=2, prev=1, checksum=999)
        )
        resync = await asyncio.wait_for(anext(stream), timeout=1)
        unsub = await _wait_sent(sock, lambda s: "unsubscribe" in s)
        assert json.loads(unsub)["op"] == "unsubscribe"
        assert any(s != unsub and "subscribe" in s for s in sock.sent)
    assert resync.kind == "book_resync"
    assert resync.data["reason"] == "checksum"


@run_async
async def test_book_seq_break_resyncs() -> None:
    broker = ScriptedConnector()
    sock = broker.add()
    bids = [["100", "1", "0", "1"]]
    asks = [["101", "1", "0", "1"]]
    async with OkxPublicStream(
        [("books", INST)],
        connector=broker,
        ping_interval=30,
        stale_timeout=30,
    ) as stream:
        await _wait_sent(sock, lambda s: "subscribe" in s)
        await sock.push(_book_push(INST, action="snapshot", bids=bids, asks=asks, seq=10, prev=-1, checksum=0))
        await asyncio.wait_for(anext(stream), timeout=1)
        await sock.push(_book_push(INST, action="update", bids=bids, asks=asks, seq=100, prev=99, checksum=0))
        resync = await asyncio.wait_for(anext(stream), timeout=1)
    assert resync.kind == "book_resync"
    assert resync.data["reason"] == "seq_break"


@run_async
async def test_buffer_overflow_drop_oldest_and_gap() -> None:
    broker = ScriptedConnector()
    sock = broker.add()
    rec = StreamRecorder(cache_dir=Path("/tmp/okx-overflow-test"), enabled=False)
    async with OkxPublicStream(
        [("trades", INST)],
        connector=broker,
        recorder=rec,
        queue_maxsize=2,
        ping_interval=30,
        stale_timeout=30,
    ) as stream:
        await _wait_sent(sock, lambda s: "subscribe" in s)
        for trade_id in range(1, 6):
            await sock.push(_trade_push(INST, trade_id))
        await asyncio.sleep(0.05)
        assert stream.overflow_count >= 1
        msgs = []
        while stream._queue.items:
            msgs.append(await asyncio.wait_for(anext(stream), timeout=0.2))
    kinds = [m.kind for m in msgs]
    assert "gap" in kinds
    gap = next(m.gap for m in msgs if m.kind == "gap")
    assert gap.reason == "overflow"
    data_ids = [m.data.trade_id for m in msgs if m.kind == "data"]
    assert data_ids[-1] == 5
    assert 1 not in data_ids
    overflow_gaps = [m.gap for m in msgs if m.kind == "gap" and m.gap and m.gap.reason == "overflow"]
    assert overflow_gaps
    assert stream.overflow_count == sum(g.dropped for g in overflow_gaps)


def test_overflow_coalesces_and_is_not_recorded(tmp_path: Path) -> None:
    rec = StreamRecorder(cache_dir=tmp_path, enabled=True)
    stream = OkxPublicStream(
        [("trades", INST)],
        recorder=rec,
        queue_maxsize=100,
    )
    for trade_id in range(1, 2001):
        stream._emit_trade(
            Trade(
                inst_id=INST,
                trade_id=trade_id,
                price=1.0,
                size=1.0,
                side="buy",
                ts_ms=1_700_000_000_000 + trade_id,
            )
        )
    assert len(stream._queue.data) <= 100
    assert len(stream._queue.items) <= 100 + 8
    overflow_gaps = [
        m.gap
        for m in stream._queue.items
        if m is not None and m.kind == "gap" and m.gap is not None and m.gap.reason == "overflow"
    ]
    assert len(overflow_gaps) == 1
    assert stream.overflow_count == overflow_gaps[0].dropped
    assert stream.overflow_count == stream._queue.overflow_gap_dropped()
    rec.offer_batch_sync(
        [
            {
                "ts": 1_700_000_000_000 + trade_id,
                "event": "data",
                "channel": "trades",
                "instId": INST,
                "data": {"tradeId": str(trade_id)},
            }
            for trade_id in range(1, 2001)
        ]
    )
    rec.close()
    import gzip

    rows = [json.loads(line) for line in gzip.open(rec.files_written[0], "rt", encoding="utf-8") if line.strip()]
    assert [row.get("reason") for row in rows if row.get("event") == "gap"] == []
    assert sum(1 for row in rows if row.get("event") == "data") == 2000


@run_async
async def test_overflow_gap_not_written_to_recording(tmp_path: Path) -> None:
    broker = ScriptedConnector()
    sock = broker.add()
    rec = StreamRecorder(cache_dir=tmp_path, enabled=True)
    async with OkxPublicStream(
        [("trades", INST)],
        connector=broker,
        recorder=rec,
        queue_maxsize=1,
        ping_interval=30,
        stale_timeout=30,
    ) as stream:
        await _wait_sent(sock, lambda s: "subscribe" in s)
        await sock.push(_trade_push(INST, 1))
        await sock.push(_trade_push(INST, 2))
        await asyncio.sleep(0.05)
        assert stream.overflow_count >= 1
        await _collect(stream, 1)
    rec.close()
    import gzip

    blob = gzip.open(rec.files_written[0], "rt", encoding="utf-8").read()
    assert "overflow" not in blob
    assert '"tradeId":"1"' in blob
    assert '"tradeId":"2"' in blob


def test_refuses_login_and_private_channels() -> None:
    with pytest.raises(PrivateChannelError, match="login"):
        _assert_public_op("login")
    with pytest.raises(PrivateChannelError, match="orders"):
        OkxPublicStream([("orders", INST)])
    with pytest.raises(PrivateChannelError, match="account"):
        validate_subscriptions([("account", INST)])
    with pytest.raises(PrivateChannelError, match="allowlist"):
        validate_subscriptions([("not-a-channel", INST)])


def test_host_allowlist_and_private_path() -> None:
    with pytest.raises(ValueError, match="host"):
        OkxPublicStream([("trades", INST)], public_url="wss://evil.example/ws/v5/public")
    with pytest.raises(ValueError, match="non-wss"):
        OkxPublicStream([("trades", INST)], public_url="ws://ws.okx.com/ws/v5/public")
    with pytest.raises(UnsafeEndpointError, match="private"):
        OkxPublicStream([("trades", INST)], public_url="wss://ws.okx.com:8443/ws/v5/private")
    stream = OkxPublicStream(
        [("trades", INST)],
        public_url="wss://wsaws.okx.com:8443/ws/v5/public",
        business_url="wss://wspap.okx.com:8443/ws/v5/business",
    )
    assert "wsaws.okx.com" in stream.public_url


def test_invalid_inst_id_rejected() -> None:
    with pytest.raises(ValueError, match="inst_id"):
        OkxPublicStream([("trades", "../ETC-USDT-SWAP")])


def test_env_ws_url_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OKX_WS_PUBLIC_URL", "wss://ws.okx.com:443/ws/v5/public")
    monkeypatch.setenv("OKX_WS_BUSINESS_URL", "wss://wsaws.okx.com:443/ws/v5/business")
    stream = OkxPublicStream([("trades", INST)])
    assert stream.public_url == "wss://ws.okx.com:443/ws/v5/public"
    assert stream.business_url == "wss://wsaws.okx.com:443/ws/v5/business"


def test_env_ws_url_refuses_non_wss(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OKX_WS_PUBLIC_URL", "ws://ws.okx.com/ws/v5/public")
    with pytest.raises(ValueError, match="non-wss"):
        OkxPublicStream([("trades", INST)])
    monkeypatch.setenv("OKX_WS_PUBLIC_URL", "http://ws.okx.com/ws/v5/public")
    with pytest.raises(ValueError, match="non-wss"):
        OkxPublicStream([("trades", INST)])


def test_env_ws_url_refuses_non_okx_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OKX_WS_PUBLIC_URL", "wss://evil.example/ws/v5/public")
    monkeypatch.setenv("OKX_ALLOW_UNOFFICIAL_HOSTS", "1")
    monkeypatch.setenv("ALLOW_UNOFFICIAL_HOSTS", "1")
    with pytest.raises(ValueError, match="host"):
        OkxPublicStream([("trades", INST)])
    with pytest.raises(ValueError, match="host"):
        OkxPublicStream([("trades", INST)], allow_unofficial_hosts=False)


def test_fallback_that_alters_host_is_refused() -> None:
    with pytest.raises(ValueError, match="must not change host"):
        endpoints.assert_same_ws_host_path(
            "wss://ws.okx.com:8443/ws/v5/public",
            "wss://evil.example:443/ws/v5/public",
        )
    with pytest.raises(ValueError, match="must not change path"):
        endpoints.assert_same_ws_host_path(
            "wss://ws.okx.com:8443/ws/v5/public",
            "wss://ws.okx.com:443/ws/v5/private",
        )
    good = endpoints.fallback_ws_port_443("wss://ws.okx.com:8443/ws/v5/public")
    assert good == "wss://ws.okx.com:443/ws/v5/public"
    assert urlparse(good).hostname == "ws.okx.com"
    assert urlparse(good).path == "/ws/v5/public"
    assert urlparse(good).port == 443
    assert endpoints.fallback_ws_port_443("wss://ws.okx.com:443/ws/v5/public") is None


def test_stream_demo_url_flags() -> None:
    from tools.okx.stream_demo import build_parser

    args = build_parser().parse_args(
        [
            "--public-url",
            "wss://ws.okx.com:443/ws/v5/public",
            "--business-url",
            "wss://ws.okx.com:443/ws/v5/business",
        ]
    )
    assert args.public_url == "wss://ws.okx.com:443/ws/v5/public"
    assert args.business_url == "wss://ws.okx.com:443/ws/v5/business"
    stream = OkxPublicStream(
        [("trades", INST)],
        public_url=args.public_url,
        business_url=args.business_url,
    )
    assert stream.public_url.endswith(":443/ws/v5/public")


@run_async
async def test_8443_timeout_falls_back_to_443() -> None:
    broker = ScriptedConnector()
    sock = broker.add()

    class Timeout8443:
        def __init__(self) -> None:
            self.urls: list[str] = []

        async def __call__(self, url: str) -> MemorySocket:
            self.urls.append(url)
            parsed = urlparse(url)
            if parsed.port == 8443:
                raise TimeoutError("opening handshake timed out")
            if parsed.port == 443 and parsed.hostname == "ws.okx.com":
                return sock
            raise AssertionError(f"unexpected url {url}")

    connector = Timeout8443()
    async with OkxPublicStream(
        [("trades", INST)],
        public_url="wss://ws.okx.com:8443/ws/v5/public",
        connector=connector,
        ping_interval=30,
        stale_timeout=30,
        handshake_timeout=0.05,
        reconnect_base=0.01,
        reconnect_max=0.02,
    ) as stream:
        await _wait_sent(sock, lambda s: "subscribe" in s)
        await sock.push(_trade_push(INST, 1))
        msg = await asyncio.wait_for(anext(stream), timeout=1)
    assert msg.data.trade_id == 1
    assert connector.urls[0] == "wss://ws.okx.com:8443/ws/v5/public"
    assert connector.urls[1] == "wss://ws.okx.com:443/ws/v5/public"
    assert stream.fallbacks == [
        ("wss://ws.okx.com:8443/ws/v5/public", "wss://ws.okx.com:443/ws/v5/public")
    ]
    assert all(urlparse(u).hostname == "ws.okx.com" for u in connector.urls)
    assert all(urlparse(u).path == "/ws/v5/public" for u in connector.urls)


@run_async
async def test_local_fake_websocket_server_optional() -> None:
    websockets = pytest.importorskip("websockets")

    subscribed = asyncio.Event()

    async def handler(ws):
        raw = await ws.recv()
        payload = json.loads(raw)
        assert payload["op"] == "subscribe"
        await ws.send(json.dumps({"event": "subscribe", "arg": payload["args"][0]}))
        subscribed.set()
        await ws.send(json.dumps(_trade_push("SPCX-USDT-SWAP", 42)))
        await asyncio.sleep(0.2)
        await ws.close()

    server = await websockets.serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    url = f"ws://127.0.0.1:{port}/ws/v5/public"
    try:
        async with OkxPublicStream(
            [("trades", "SPCX-USDT-SWAP")],
            public_url=url,
            business_url=url,
            allow_unofficial_hosts=True,
            allow_insecure=True,
            ping_interval=30,
            stale_timeout=30,
            reconnect_max=0.05,
            reconnect_base=0.01,
        ) as stream:
            msg = await asyncio.wait_for(anext(stream), timeout=2)
            assert msg.data.trade_id == 42
            assert subscribed.is_set()
    finally:
        server.close()
        await server.wait_closed()


def test_default_ping_interval_is_25_seconds() -> None:
    assert DEFAULT_PING_INTERVAL == 25.0
    stream = OkxPublicStream([("trades", INST)])
    assert stream.ping_interval == 25.0


def test_reconnect_max_is_finite() -> None:
    assert 0 < DEFAULT_RECONNECT_MAX < float("inf")
    stream = OkxPublicStream([("trades", INST)])
    assert stream.reconnect_max == DEFAULT_RECONNECT_MAX


@run_async
async def test_short_lived_reconnects_backoff_and_cap() -> None:
    sleeps: list[float] = []

    class Clock:
        def __init__(self) -> None:
            self.t = 0.0

        def __call__(self) -> float:
            return self.t

    clock = Clock()

    async def sleeper(seconds: float) -> None:
        sleeps.append(seconds)
        clock.t += seconds
        await asyncio.sleep(0)

    class InstantClose:
        async def send(self, data: str) -> None:
            return

        async def recv(self) -> str:
            raise ConnectionError("closed immediately")

        async def close(self) -> None:
            return

    async def connector(_url: str) -> InstantClose:
        return InstantClose()

    async with OkxPublicStream(
        [("trades", INST)],
        connector=connector,
        clock=clock,
        sleep=sleeper,
        stale_timeout=1e9,
        reconnect_base=0.1,
        reconnect_max=0.8,
        healthy_after=30.0,
        handshake_timeout=0.05,
        ping_interval=30,
    ):
        for _ in range(30):
            await asyncio.sleep(0)
    assert any(0.7 <= s <= 0.96 for s in sleeps)
    assert all(s <= 1.0 + 1e-9 for s in sleeps)


@run_async
async def test_stop_cancels_backfill_promptly() -> None:
    class SlowTape:
        def __init__(self) -> None:
            self.n = 0
            self.next_id = 500

        def request(self, method, url, *, params, headers, timeout):
            from tools.okx.client import HttpResponse

            self.n += 1
            time.sleep(0.2)
            tid = self.next_id
            self.next_id -= 1
            body = json.dumps(
                {
                    "code": "0",
                    "data": [
                        {
                            "instId": INST,
                            "tradeId": str(tid),
                            "px": "1",
                            "sz": "1",
                            "side": "buy",
                            "ts": str(1_700_000_000_000 + tid),
                        }
                    ],
                }
            ).encode()
            return HttpResponse(200, {}, body, url)

    tape = SlowTape()
    rest = make_client(tape)  # type: ignore[arg-type]
    stream = OkxPublicStream([("trades", INST)], rest=rest)
    task = asyncio.create_task(stream._backfill(INST, 1, 500))
    await asyncio.sleep(0.05)
    t0 = time.monotonic()
    await stream.stop()
    result = await asyncio.wait_for(task, timeout=1.0)
    assert result is None
    assert time.monotonic() - t0 < 0.8
    assert tape.n <= 5


def test_ws_url_rejects_userinfo_query_and_odd_ports() -> None:
    with pytest.raises(ValueError, match="user-info"):
        OkxPublicStream(
            [("trades", INST)],
            public_url="wss://user:pass@ws.okx.com:443/ws/v5/public",
        )
    with pytest.raises(ValueError, match="query"):
        OkxPublicStream(
            [("trades", INST)],
            public_url="wss://ws.okx.com:443/ws/v5/public?x=1",
        )
    with pytest.raises(ValueError, match="port"):
        OkxPublicStream(
            [("trades", INST)],
            public_url="wss://ws.okx.com:80/ws/v5/public",
        )


def test_unofficial_host_still_requires_wss_unless_insecure() -> None:
    with pytest.raises(ValueError, match="non-wss"):
        OkxPublicStream(
            [("trades", INST)],
            public_url="ws://127.0.0.1:9/ws/v5/public",
            allow_unofficial_hosts=True,
        )
    stream = OkxPublicStream(
        [("trades", INST)],
        public_url="ws://127.0.0.1:9/ws/v5/public",
        business_url="ws://127.0.0.1:9/ws/v5/business",
        allow_unofficial_hosts=True,
        allow_insecure=True,
    )
    assert stream.public_url.startswith("ws://127.0.0.1")


@run_async
async def test_subscribe_calls_assert_public_op(monkeypatch: pytest.MonkeyPatch) -> None:
    import tools.okx.stream as stream_mod

    seen: list[str] = []
    orig = stream_mod._assert_public_op

    def spy(op: str) -> None:
        seen.append(op)
        orig(op)

    monkeypatch.setattr(stream_mod, "_assert_public_op", spy)
    broker = ScriptedConnector()
    sock = broker.add()
    async with OkxPublicStream(
        [("trades", INST)],
        connector=broker,
        ping_interval=30,
        stale_timeout=30,
    ):
        await _wait_sent(sock, lambda s: "subscribe" in s)
    assert "subscribe" in seen


def test_record_channels_flag() -> None:
    from tools.okx.stream_demo import build_parser

    args = build_parser().parse_args(["--record-channels", "trades,books5"])
    assert args.record_channels == "trades,books5"


def test_ws_url_path_must_be_public_or_business() -> None:
    with pytest.raises(UnsafeEndpointError, match="path"):
        OkxPublicStream(
            [("trades", INST)],
            public_url="wss://ws.okx.com:443/ws/v5/not-public",
        )
    with pytest.raises(UnsafeEndpointError, match="path"):
        OkxPublicStream(
            [("trades", INST)],
            public_url="wss://ws.okx.com:443/ws/v5/private",
        )


@run_async
async def test_crafted_inst_id_from_server_is_not_recorded(tmp_path: Path) -> None:
    broker = ScriptedConnector()
    sock = broker.add()
    rec = StreamRecorder(cache_dir=tmp_path, enabled=True)
    async with OkxPublicStream(
        [("trades", INST)],
        connector=broker,
        recorder=rec,
        ping_interval=30,
        stale_timeout=30,
    ) as stream:
        await _wait_sent(sock, lambda s: "subscribe" in s)
        await sock.push(_trade_push("../ETC-USDT-SWAP", 1))
        await sock.push(_trade_push(INST, 2))
        msg = await asyncio.wait_for(anext(stream), timeout=1)
        await asyncio.sleep(0.05)
    rec.close()
    assert stream.rejected_messages >= 1
    assert msg.data.trade_id == 2
    rows = [
        json.loads(line)
        for line in __import__("gzip").open(rec.files_written[0], "rt", encoding="utf-8")
        if line.strip()
    ]
    assert all(row.get("instId") == INST for row in rows)
    assert not (tmp_path.parent / "ETC-USDT-SWAP").exists()


@run_async
async def test_crafted_channel_from_server_is_not_recorded(tmp_path: Path) -> None:
    broker = ScriptedConnector()
    sock = broker.add()
    rec = StreamRecorder(cache_dir=tmp_path, enabled=True)
    async with OkxPublicStream(
        [("trades", INST)],
        connector=broker,
        recorder=rec,
        ping_interval=30,
        stale_timeout=30,
    ) as stream:
        await _wait_sent(sock, lambda s: "subscribe" in s)
        await sock.push(
            {
                "arg": {"channel": "orders", "instId": INST},
                "data": [{"instId": INST, "ordId": "1"}],
            }
        )
        await sock.push(_trade_push(INST, 3))
        await asyncio.wait_for(anext(stream), timeout=1)
        await asyncio.sleep(0.05)
    rec.close()
    assert stream.rejected_messages >= 1
    blob = __import__("gzip").open(rec.files_written[0], "rt", encoding="utf-8").read()
    assert "orders" not in blob


@run_async
async def test_500_trade_burst_is_fully_recorded(tmp_path: Path) -> None:
    broker = ScriptedConnector()
    sock = broker.add()
    rec = StreamRecorder(cache_dir=tmp_path, enabled=True)
    payload = {
        "arg": {"channel": "trades", "instId": INST},
        "data": [
            {
                "instId": INST,
                "tradeId": str(i),
                "px": "1",
                "sz": "1",
                "side": "buy",
                "ts": str(1_700_000_000_000 + i),
                "count": "1",
            }
            for i in range(1, 501)
        ],
    }
    async with OkxPublicStream(
        [("trades", INST)],
        connector=broker,
        recorder=rec,
        ping_interval=30,
        stale_timeout=30,
    ):
        await _wait_sent(sock, lambda s: "subscribe" in s)
        await sock.push(payload)
        await asyncio.sleep(0.2)
    rec.close()
    import gzip

    rows = [json.loads(line) for line in gzip.open(rec.files_written[0], "rt", encoding="utf-8") if line.strip()]
    assert sum(1 for row in rows if row.get("event") == "data") == 500
    assert rec.writer_dropped == 0


@run_async
async def test_2000_trade_backfill_is_fully_recorded(tmp_path: Path) -> None:
    from tools.okx.client import HttpResponse

    class Tape:
        def __init__(self) -> None:
            self.next_id = 2001

        def request(self, method, url, *, params, headers, timeout):
            page = []
            for _ in range(100):
                if self.next_id < 2:
                    break
                tid = self.next_id
                self.next_id -= 1
                page.append(
                    {
                        "instId": INST,
                        "tradeId": str(tid),
                        "px": "1",
                        "sz": "1",
                        "side": "buy",
                        "ts": str(1_700_000_000_000 + tid),
                    }
                )
            return HttpResponse(200, {}, json.dumps({"code": "0", "data": page}).encode(), url)

    broker = ScriptedConnector()
    sock = broker.add()
    rec = StreamRecorder(cache_dir=tmp_path, enabled=True)
    rest = make_client(Tape())  # type: ignore[arg-type]
    async with OkxPublicStream(
        [("trades", INST)],
        connector=broker,
        recorder=rec,
        rest=rest,
        ping_interval=30,
        stale_timeout=30,
    ):
        await _wait_sent(sock, lambda s: "subscribe" in s)
        await sock.push(_trade_push(INST, 1))
        await sock.push(_trade_push(INST, 2002))
        await asyncio.sleep(0.5)
    rec.close()
    import gzip

    rows = [json.loads(line) for line in gzip.open(rec.files_written[0], "rt", encoding="utf-8") if line.strip()]
    ids = [int(row["data"]["tradeId"]) for row in rows if row.get("event") == "data"]
    assert ids == list(range(1, 2003))
    assert rec.writer_dropped == 0


@run_async
async def test_slow_disk_backpressure_keeps_pinging(tmp_path: Path) -> None:
    broker = ScriptedConnector()
    sock = broker.add()
    rec = StreamRecorder(
        cache_dir=tmp_path,
        enabled=True,
        write_queue_maxsize=2,
        write_sleep=0.4,
        # Longer than the 250 ms probe budget so a blocking wait_put fails.
        offer_timeout=0.5,
        sync=False,
    )
    max_delay = 0.0
    probing = True

    async def probe() -> None:
        nonlocal max_delay
        loop = asyncio.get_running_loop()
        last = loop.time()
        while probing:
            await asyncio.sleep(0.05)
            now = loop.time()
            max_delay = max(max_delay, now - last - 0.05)
            last = now

    async with OkxPublicStream(
        [("trades", INST)],
        connector=broker,
        recorder=rec,
        ping_interval=0.1,
        stale_timeout=30,
        handshake_timeout=1,
        reconnect_base=5,
        reconnect_max=5,
    ) as stream:
        probe_task = asyncio.create_task(probe())
        try:
            await _wait_sent(sock, lambda s: "subscribe" in s)
            for trade_id in range(1, 13):
                await sock.push(_trade_push(INST, trade_id))
            await _collect(stream, 12, timeout=8)
            await asyncio.sleep(0.3)
            pings = [item for item in sock.sent if item == "ping"]
            assert pings
            assert broker._i == 1
            assert stream._reconnect_generation == 1
        finally:
            probing = False
            probe_task.cancel()
            try:
                await probe_task
            except asyncio.CancelledError:
                pass
        assert max_delay < 0.25
    rec.close()
    rows = []
    for path in rec.files_written:
        rows.extend(json.loads(line) for line in gzip.open(path, "rt", encoding="utf-8") if line.strip())
    gaps = [row for row in rows if row.get("reason") == "writer_overflow"]
    data = [row for row in rows if row.get("event") == "data"]
    assert rec.writer_dropped == sum(int(row["dropped"]) for row in gaps)
    assert len(data) + rec.writer_dropped == 12


@run_async
async def test_stop_never_loses_batch_without_gap(tmp_path: Path) -> None:
    from tools.okx.client import HttpResponse

    rng = random.Random(1)
    for i in range(300):
        rec = StreamRecorder(
            cache_dir=tmp_path / f"run{i}",
            enabled=True,
            write_queue_maxsize=1,
            write_sleep=rng.choice([0.0, 0.0, 0.02]),
            offer_timeout=0.5,
            sync=False,
        )
        broker = ScriptedConnector()
        sock = broker.add()
        rest = None
        if i % 3 == 0:

            class Tape:
                def __init__(self) -> None:
                    self.next_id = 40

                def request(self, method, url, *, params, headers, timeout):
                    page = []
                    for _ in range(20):
                        if self.next_id < 2:
                            break
                        tid = self.next_id
                        self.next_id -= 1
                        page.append(
                            {
                                "instId": INST,
                                "tradeId": str(tid),
                                "px": "1",
                                "sz": "1",
                                "side": "buy",
                                "ts": str(1_700_000_000_000 + tid),
                            }
                        )
                    return HttpResponse(200, {}, json.dumps({"code": "0", "data": page}).encode(), url)

            rest = make_client(Tape())  # type: ignore[arg-type]
        stream = OkxPublicStream(
            [("trades", INST)],
            connector=broker,
            recorder=rec,
            rest=rest,
            ping_interval=30,
            stale_timeout=30,
            shutdown_timeout=rng.choice([0.05, 0.2, 1.0, 2.0]),
        )
        await stream.start()
        try:
            await _wait_sent(sock, lambda s: "subscribe" in s)
            if rest is not None:
                await sock.push(_trade_push(INST, 1))
                await sock.push(_trade_push(INST, 41))
            else:
                await sock.push(_trade_push(INST, 1))
                await asyncio.wait_for(anext(stream), timeout=2)
                await sock.push(
                    {
                        "arg": {"channel": "trades", "instId": INST},
                        "data": [
                            {
                                "instId": INST,
                                "tradeId": str(tid),
                                "px": "1",
                                "sz": "1",
                                "side": "buy",
                                "ts": str(1_700_000_000_000 + tid),
                                "count": "1",
                            }
                            for tid in range(2, 502)
                        ],
                    }
                )
            await asyncio.wait_for(anext(stream), timeout=2)
            await asyncio.sleep(rng.uniform(0.0, 0.03))
        finally:
            await stream.stop()
        recorded, covered = _tape_coverage(rec)
        assert recorded + covered == rec.accepted, (i, recorded, covered, rec.accepted, rec.writer_dropped)
        for path in rec.files_written:
            _assert_gzip_readable(path)


@run_async
async def test_slow_disk_sets_writer_lag(tmp_path: Path) -> None:
    broker = ScriptedConnector()
    sock = broker.add()
    rec = StreamRecorder(
        cache_dir=tmp_path,
        enabled=True,
        write_queue_maxsize=1,
        write_sleep=0.4,
        offer_timeout=0.3,
        sync=False,
    )
    async with OkxPublicStream(
        [("trades", INST)],
        connector=broker,
        recorder=rec,
        ping_interval=30,
        stale_timeout=0.2,
        lag_threshold=0.15,
        handshake_timeout=1,
        reconnect_base=5,
        reconnect_max=5,
    ) as stream:
        await _wait_sent(sock, lambda s: "subscribe" in s)
        for trade_id in range(1, 8):
            await sock.push(_trade_push(INST, trade_id))
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not stream.writer_lagging:
            await asyncio.sleep(0.05)
        assert stream.writer_lagging
        assert stream.writer_lag >= 0.15
        kinds = [item.kind for item in stream._queue.items if item is not None]
        assert "lag" in kinds
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and stream.writer_lagging:
            await asyncio.sleep(0.05)
        assert stream.writer_lagging is False
        kinds = [item.kind for item in stream._queue.items if item is not None]
        assert "lag_cleared" in kinds


def test_default_lag_and_shutdown_bounds() -> None:
    stream = OkxPublicStream([("trades", INST)])
    assert stream.lag_threshold == 5.0
    assert stream.shutdown_timeout == 8.0
    assert stream.writer_lagging is False


def test_clock_offset_does_not_false_alert() -> None:
    tracker = LagTracker()
    wall = time.time()
    mono = time.monotonic()
    for offset in (-30.0, 30.0):
        tracker = LagTracker()
        for i in range(8):
            lag = tracker.observe(
                recv_mono=mono + i * 0.01,
                recv_wall=wall + i * 0.01,
                okx_ts_ms=int((wall + i * 0.01 + offset) * 1000),
                processed_mono=mono + i * 0.01,
            )
            assert lag < 1.0
            assert tracker.secondary < 1.0


@run_async
async def test_socket_backlog_sets_lag_without_clock_false_alarm(tmp_path: Path) -> None:
    rec = StreamRecorder(cache_dir=tmp_path / "offset", enabled=True, sync=False)
    broker = ScriptedConnector()
    sock = broker.add()
    async with OkxPublicStream(
        [("trades", INST)],
        connector=broker,
        recorder=rec,
        ping_interval=30,
        stale_timeout=0.2,
        lag_threshold=5.0,
    ) as stream:
        await _wait_sent(sock, lambda s: "subscribe" in s)
        now = int(time.time() * 1000)
        for trade_id in range(1, 6):
            await sock.push(_trade_push(INST, trade_id, ts_ms=now - 30_000 + trade_id))
        await _collect(stream, 5, timeout=2)
        await asyncio.sleep(0.3)
        assert stream.writer_lagging is False

    rec = StreamRecorder(cache_dir=tmp_path / "offset_plus", enabled=True, sync=False)
    broker = ScriptedConnector()
    sock = broker.add()
    async with OkxPublicStream(
        [("trades", INST)],
        connector=broker,
        recorder=rec,
        ping_interval=30,
        stale_timeout=0.2,
        lag_threshold=5.0,
    ) as stream:
        await _wait_sent(sock, lambda s: "subscribe" in s)
        now = int(time.time() * 1000)
        for trade_id in range(1, 6):
            await sock.push(_trade_push(INST, trade_id, ts_ms=now + 30_000 + trade_id))
        await _collect(stream, 5, timeout=2)
        await asyncio.sleep(0.3)
        assert stream.writer_lagging is False

    rec = StreamRecorder(
        cache_dir=tmp_path / "backlog",
        enabled=True,
        write_queue_maxsize=1,
        write_sleep=0.05,
        offer_timeout=0.05,
        sync=False,
    )
    broker = ScriptedConnector()
    sock = broker.add()
    async with OkxPublicStream(
        [("trades", INST)],
        connector=broker,
        recorder=rec,
        ping_interval=30,
        stale_timeout=0.1,
        lag_threshold=0.4,
    ) as stream:
        await _wait_sent(sock, lambda s: "subscribe" in s)
        baseline = int(time.time() * 1000)
        await sock.push(_trade_push(INST, 1, ts_ms=baseline))
        await asyncio.wait_for(anext(stream), timeout=2)
        stamped = int(time.time() * 1000)
        for trade_id in range(2, 36):
            await sock.push(_trade_push(INST, trade_id, ts_ms=stamped))
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and not stream.writer_lagging:
            await asyncio.sleep(0.05)
        assert stream.writer_lagging
        assert stream.writer_lag >= 0.4
        kinds = [item.kind for item in stream._queue.items if item is not None]
        assert "lag" in kinds
