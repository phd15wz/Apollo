from __future__ import annotations

import asyncio
import functools
import inspect
import json
from pathlib import Path

import pytest

from tools.okx.errors import PrivateChannelError, UnsafeEndpointError
from tools.okx.record import StreamRecorder
from tools.okx.stream import (
    GapInfo,
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


@run_async
async def test_overflow_gap_written_to_recording(tmp_path: Path) -> None:
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
        await _collect(stream, 2)
    rec.close()
    import gzip

    blob = gzip.open(rec.files_written[0], "rt", encoding="utf-8").read()
    assert "overflow" in blob
    assert '"event":"gap"' in blob


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
