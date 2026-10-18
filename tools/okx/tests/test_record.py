from __future__ import annotations

import gzip
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.okx.archive import cache_usage_bytes
from tools.okx.errors import CacheBudgetExceeded, LowDiskError
from tools.okx.record import (
    DEFAULT_OFFER_TIMEOUT,
    StreamRecorder,
    recording_dest,
    recording_period_key,
)
from tools.okx.stream import CONTROL_MAX, EventQueue, GapInfo, StreamMessage


class WallClock:
    def __init__(self, t: float = 1_700_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def _read_jsonl(path: Path) -> list[dict]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def test_recording_off_by_default(tmp_path: Path) -> None:
    rec = StreamRecorder(cache_dir=tmp_path)
    assert rec.enabled is False
    rec.write_data("trades", "BTC-USDT-SWAP", {"tradeId": "1"}, ts_ms=1)
    assert list(tmp_path.rglob("*.jsonl.gz")) == []


def test_rotation_hourly_and_daily(tmp_path: Path) -> None:
    clock = WallClock(1_700_000_000.0)
    rec = StreamRecorder(cache_dir=tmp_path, enabled=True, rotation="hourly", clock=clock)
    rec.write_data("trades", "BTC-USDT-SWAP", {"tradeId": "1"}, ts_ms=1)
    clock.t += 3600
    rec.write_data("trades", "BTC-USDT-SWAP", {"tradeId": "2"}, ts_ms=2)
    files = sorted(p.name for p in tmp_path.rglob("*.jsonl.gz"))
    assert len(files) == 2
    assert files[0] != files[1]
    assert recording_period_key(1_700_000_000.0, "hourly") in files[0]
    rec.close()

    clock.t = 1_700_000_000.0
    daily = StreamRecorder(cache_dir=tmp_path / "d", enabled=True, rotation="daily", clock=clock)
    daily.write_data("tickers", "BTC-USDT-SWAP", {"last": "1"}, ts_ms=1)
    clock.t += 25 * 3600
    daily.write_data("tickers", "BTC-USDT-SWAP", {"last": "2"}, ts_ms=2)
    names = sorted(p.name for p in (tmp_path / "d").rglob("*.jsonl.gz"))
    assert len(names) == 2
    daily.close()


def test_retention_deletes_oldest_first(tmp_path: Path) -> None:
    clock = WallClock(1_700_000_000.0)
    rec = StreamRecorder(
        cache_dir=tmp_path,
        enabled=True,
        rotation="hourly",
        clock=clock,
        retention_max_bytes=10**9,
    )
    rec.write_data("trades", "BTC-USDT-SWAP", {"n": 1}, ts_ms=1)
    first = rec.files_written[0]
    first_name = first.name
    rec.retention_max_bytes = first.stat().st_size
    clock.t += 3600
    rec.write_data("trades", "BTC-USDT-SWAP", {"n": 2}, ts_ms=2)
    remaining = list(tmp_path.rglob("*.jsonl.gz"))
    assert len(remaining) == 1
    assert remaining[0].name != first_name
    assert not first.exists()


def test_retention_max_age(tmp_path: Path) -> None:
    import os

    clock = WallClock(1_700_000_000.0)
    rec = StreamRecorder(
        cache_dir=tmp_path,
        enabled=True,
        rotation="hourly",
        clock=clock,
        retention_max_age_s=10,
    )
    rec.write_data("trades", "BTC-USDT-SWAP", {"n": 1}, ts_ms=1)
    old = rec.files_written[0]
    clock.t += 3600
    rec.write_data("trades", "BTC-USDT-SWAP", {"n": 2}, ts_ms=2)
    os.utime(old, (clock.t - 100, clock.t - 100))
    rec.apply_retention()
    assert not old.exists()
    assert list(tmp_path.rglob("*.jsonl.gz"))


def test_books_snapshot_interval(tmp_path: Path) -> None:
    clock = WallClock(100.0)
    rec = StreamRecorder(
        cache_dir=tmp_path,
        enabled=True,
        clock=clock,
        books_snapshot_interval=60.0,
        rotation="daily",
    )
    assert rec.maybe_write_books_snapshot("BTC-USDT-SWAP", {"seqId": 1}, ts_ms=1) > 0
    clock.t += 10
    assert rec.maybe_write_books_snapshot("BTC-USDT-SWAP", {"seqId": 2}, ts_ms=2) == 0
    clock.t += 60
    assert rec.maybe_write_books_snapshot("BTC-USDT-SWAP", {"seqId": 3}, ts_ms=3) > 0
    rec.close()
    rows = _read_jsonl(rec.files_written[0])
    assert [row["event"] for row in rows] == ["book_snapshot", "book_snapshot"]
    assert [row["data"]["seqId"] for row in rows] == [1, 3]


def test_recording_budget_exceeded(tmp_path: Path) -> None:
    rec = StreamRecorder(
        cache_dir=tmp_path,
        enabled=True,
        cache_budget_bytes=40,
        run_budget_bytes=1_000_000,
    )
    with pytest.raises(CacheBudgetExceeded, match="cache budget"):
        for i in range(20):
            rec.write_data("trades", "BTC-USDT-SWAP", {"tradeId": str(i), "pad": "x" * 20}, ts_ms=i)


def test_low_disk_mocked(tmp_path: Path) -> None:
    def usage(_path: str) -> SimpleNamespace:
        return SimpleNamespace(free=100)

    rec = StreamRecorder(
        cache_dir=tmp_path,
        enabled=True,
        min_free_disk_bytes=1_000,
        disk_usage=usage,
    )
    with pytest.raises(LowDiskError, match="free disk") as exc:
        rec.write_data("trades", "BTC-USDT-SWAP", {"tradeId": "1"}, ts_ms=1)
    assert exc.value.free == 100
    assert exc.value.floor == 1_000
    assert rec.stopped_reason() == "low_disk"
    assert list(tmp_path.rglob("*.jsonl.gz")) == []


def test_gap_record_written(tmp_path: Path) -> None:
    rec = StreamRecorder(cache_dir=tmp_path, enabled=True)
    gap = GapInfo(
        channel="trades",
        inst_id="BTC-USDT-SWAP",
        reason="backfill_failed",
        first_missing_trade_id=11,
        last_missing_trade_id=14,
        start_ts_ms=1,
        end_ts_ms=4,
        dropped=4,
    )
    rec.write_gap(gap)
    rec.close()
    rows = _read_jsonl(rec.files_written[0])
    assert rows[0]["event"] == "gap"
    assert rows[0]["firstMissingTradeId"] == 11
    assert rows[0]["dropped"] == 4


def test_per_channel_recording_opt_in(tmp_path: Path) -> None:
    rec = StreamRecorder(cache_dir=tmp_path, enabled=True, channels={"trades"})
    rec.write_data("trades", "BTC-USDT-SWAP", {"tradeId": "1"}, ts_ms=1)
    rec.write_data("tickers", "BTC-USDT-SWAP", {"last": "1"}, ts_ms=2)
    rec.close()
    files = list(tmp_path.rglob("*.jsonl.gz"))
    assert len(files) == 1
    assert files[0].parent.name == "trades"
    rows = _read_jsonl(files[0])
    assert [row["event"] for row in rows] == ["data"]
    assert rows[0]["channel"] == "trades"


def test_per_run_recording_budget(tmp_path: Path) -> None:
    rec = StreamRecorder(
        cache_dir=tmp_path,
        enabled=True,
        cache_budget_bytes=10**9,
        run_budget_bytes=80,
    )
    with pytest.raises(CacheBudgetExceeded, match="per-run"):
        for i in range(20):
            rec.write_data("trades", "BTC-USDT-SWAP", {"tradeId": str(i), "pad": "x" * 20}, ts_ms=i)


def test_recording_stays_fast_with_many_files(tmp_path: Path) -> None:
    import time as time_mod

    recordings = tmp_path / "recordings"
    recordings.mkdir()
    for i in range(400):
        (recordings / f"dummy-{i}.jsonl.gz").write_bytes(b"dummy")
    rec = StreamRecorder(cache_dir=tmp_path, enabled=True)
    scans_at_start = rec.usage_scans
    started = time_mod.perf_counter()
    for i in range(80):
        rec.write_data("trades", "BTC-USDT-SWAP", {"tradeId": str(i), "pad": "aaaaaaaaaa"}, ts_ms=i)
    rec.close()
    elapsed = time_mod.perf_counter() - started
    assert rec.usage_scans == scans_at_start
    assert elapsed < 3.0
    raw = rec.throughput["trades"].raw_bytes
    compressed = rec.files_written[0].stat().st_size
    assert compressed < raw * 0.6


def test_writer_queue_slow_disk_coalesces_gaps(tmp_path: Path) -> None:
    rec = StreamRecorder(
        cache_dir=tmp_path,
        enabled=True,
        write_queue_maxsize=8,
        write_sleep=0.05,
        offer_timeout=0.01,
        sync=False,
    )
    rec.start()
    for i in range(80):
        rec.offer(
            {
                "ts": i,
                "event": "data",
                "channel": "trades",
                "instId": "BTC-USDT-SWAP",
                "data": {"tradeId": str(i), "px": "1", "sz": "1", "side": "buy", "ts": str(i)},
            }
        )
    assert rec._queue.max_depth <= rec.write_queue_maxsize
    assert rec._queue.dropped > 0
    rec.close()
    rows = []
    for path in rec.files_written:
        rows.extend(_read_jsonl(path))
    gaps = [row for row in rows if row.get("reason") == "writer_overflow"]
    data = [row for row in rows if row.get("event") == "data"]
    assert gaps
    assert rec.writer_dropped == sum(int(row["dropped"]) for row in gaps)
    assert len(data) + rec.writer_dropped == 80


def test_daily_rotation_is_the_default(tmp_path: Path) -> None:
    rec = StreamRecorder(cache_dir=tmp_path)
    assert rec.rotation == "daily"
    dest = recording_dest(tmp_path, "BTC-USDT-SWAP", "trades", 1_700_000_000.0, rec.rotation)
    assert recording_period_key(1_700_000_000.0, "daily") in dest.name
    assert "T" not in dest.stem


def test_refuses_crafted_inst_id_and_channel(tmp_path: Path) -> None:
    rec = StreamRecorder(cache_dir=tmp_path, enabled=True)
    rec.write_data("trades", "../ETC-USDT-SWAP", {"tradeId": "1"}, ts_ms=1)
    rec.write_data("orders", "BTC-USDT-SWAP", {"ordId": "1"}, ts_ms=2)
    rec.write_data("trades/../../x", "BTC-USDT-SWAP", {"tradeId": "2"}, ts_ms=3)
    rec.close()
    assert rec.rejected >= 3
    assert list(tmp_path.rglob("*.jsonl.gz")) == []
    assert list(tmp_path.rglob("*")) == [] or all(
        "recordings" not in str(p) or p.is_dir() for p in tmp_path.rglob("*")
    )
    outside = tmp_path.parent / "ETC-USDT-SWAP"
    assert not outside.exists() or not any(outside.rglob("*.jsonl.gz"))


def test_symlink_escape_is_refused(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    recordings = tmp_path / "recordings"
    recordings.mkdir()
    (recordings / "BTC-USDT-SWAP").symlink_to(outside)
    rec = StreamRecorder(cache_dir=tmp_path, enabled=True)
    rec.write_data("trades", "BTC-USDT-SWAP", {"tradeId": "1"}, ts_ms=1)
    rec.close()
    assert rec.rejected >= 1
    assert list(outside.rglob("*.jsonl.gz")) == []
    assert list(tmp_path.rglob("*.jsonl.gz")) == []


def test_unsubscribed_pair_is_not_recorded(tmp_path: Path) -> None:
    rec = StreamRecorder(
        cache_dir=tmp_path,
        enabled=True,
        allowed_pairs=frozenset({("trades", "BTC-USDT-SWAP")}),
    )
    rec.write_data("trades", "ETH-USDT-SWAP", {"tradeId": "1"}, ts_ms=1)
    rec.write_data("trades", "BTC-USDT-SWAP", {"tradeId": "2"}, ts_ms=2)
    rec.close()
    assert rec.rejected >= 1
    rows = _read_jsonl(rec.files_written[0])
    assert [row["data"]["tradeId"] for row in rows] == ["2"]


def test_storage_counts_compressed_bytes(tmp_path: Path) -> None:
    rec = StreamRecorder(cache_dir=tmp_path, enabled=True, cache_budget_bytes=2_000_000)
    for i in range(40):
        rec.write_data("trades", "BTC-USDT-SWAP", {"tradeId": str(i), "pad": "a" * 400}, ts_ms=i)
    rec.close()
    raw = rec.throughput["trades"].raw_bytes
    on_disk = rec.files_written[0].stat().st_size
    assert on_disk < raw / 4
    assert rec._used <= on_disk + 64
    assert rec._used < raw / 2


def test_budget_includes_archive_cache(tmp_path: Path) -> None:
    trades = tmp_path / "trades"
    trades.mkdir()
    (trades / "old.zip").write_bytes(b"x" * 800)
    rec = StreamRecorder(cache_dir=tmp_path, enabled=True, cache_budget_bytes=850)
    assert rec._used >= 800
    with pytest.raises(CacheBudgetExceeded):
        rec.write_data("trades", "BTC-USDT-SWAP", {"tradeId": "1", "pad": "y" * 200}, ts_ms=1)


def test_rescan_sees_files_from_other_processes(tmp_path: Path) -> None:
    clock = WallClock(100.0)
    rec = StreamRecorder(
        cache_dir=tmp_path,
        enabled=True,
        clock=clock,
        usage_rescan_interval_s=5,
        cache_budget_bytes=500,
    )
    rec.write_data("trades", "BTC-USDT-SWAP", {"tradeId": "1"}, ts_ms=1)
    clock.t += 6
    (tmp_path / "other-process.bin").write_bytes(b"z" * 400)
    with pytest.raises(CacheBudgetExceeded):
        rec.write_data("trades", "BTC-USDT-SWAP", {"tradeId": "2", "pad": "w" * 80}, ts_ms=2)
    assert rec.usage_scans >= 2


def test_retention_skips_open_files(tmp_path: Path) -> None:
    rec = StreamRecorder(cache_dir=tmp_path, enabled=True, retention_max_bytes=1, rotation="daily")
    rec.write_data("trades", "BTC-USDT-SWAP", {"n": 1}, ts_ms=1)
    rec.write_data("tickers", "BTC-USDT-SWAP", {"n": 2}, ts_ms=2)
    open_before = {p.resolve() for p in rec.open_paths()}
    assert open_before
    rec.apply_retention()
    for path in open_before:
        assert path.exists()
    rec.close()


def test_control_events_are_not_evicted() -> None:
    queue = EventQueue(maxsize=2)
    for trade_id in range(1, 6):
        queue.put(
            StreamMessage(kind="data", channel="trades", inst_id="BTC-USDT-SWAP", ts_ms=trade_id, data=trade_id)
        )
    gap = StreamMessage(
        kind="gap",
        channel="trades",
        inst_id="BTC-USDT-SWAP",
        gap=GapInfo(channel="trades", inst_id="BTC-USDT-SWAP", reason="backfill_failed", dropped=3),
    )
    stale = StreamMessage(kind="stale", channel="trades", inst_id="BTC-USDT-SWAP", stale=True)
    queue.put(gap, control=True)
    queue.put(stale, control=True)
    for trade_id in range(6, 10):
        queue.put(
            StreamMessage(kind="data", channel="trades", inst_id="BTC-USDT-SWAP", ts_ms=trade_id, data=trade_id)
        )
    kinds = [item.kind for item in queue.items if item is not None]
    assert "gap" in kinds
    assert "stale" in kinds
    assert any(item.kind == "gap" and item.gap and item.gap.reason == "backfill_failed" for item in queue.items)


def test_batch_write_throughput(tmp_path: Path) -> None:
    import time as time_mod

    rec = StreamRecorder(cache_dir=tmp_path, enabled=True, sync=False)
    rec.start()
    batch = [
        {
            "ts": i,
            "event": "data",
            "channel": "trades",
            "instId": "BTC-USDT-SWAP",
            "data": {"tradeId": str(i), "pad": "a" * 8},
        }
        for i in range(2000)
    ]
    started = time_mod.perf_counter()
    rec.offer_batch_sync(batch)
    rec.close()
    elapsed = time_mod.perf_counter() - started
    rate = 2000 / max(elapsed, 1e-6)
    assert rate > 300
    assert sum(1 for row in _read_jsonl(rec.files_written[0]) if row.get("event") == "data") == 2000


def test_close_is_idempotent_and_joins_writer(tmp_path: Path) -> None:
    rec = StreamRecorder(cache_dir=tmp_path, enabled=True, sync=False)
    rec.start()
    rec.offer({"ts": 1, "event": "data", "channel": "trades", "instId": "BTC-USDT-SWAP", "data": {"tradeId": "1"}})
    rec.close()
    rec.close()
    assert rec._writer is None
    assert rec.files_written
    _read_jsonl(rec.files_written[0])


def test_writer_queue_put_returns_when_full_of_gaps(tmp_path: Path) -> None:
    rec = StreamRecorder(
        cache_dir=tmp_path,
        enabled=True,
        write_queue_maxsize=2,
        write_sleep=0.2,
        offer_timeout=0.05,
        sync=False,
    )
    rec.start()
    started = __import__("time").perf_counter()
    for i in range(40):
        rec.offer_batch_sync(
            [
                {
                    "ts": i,
                    "event": "data",
                    "channel": "trades",
                    "instId": f"BTC{i}-USDT-SWAP",
                    "data": {"tradeId": str(i)},
                }
            ]
        )
    elapsed = __import__("time").perf_counter() - started
    assert elapsed < 4.0
    assert rec.writer_dropped > 0
    rec.close()
    rows = []
    for path in rec.files_written:
        rows.extend(_read_jsonl(path))
    gaps = [row for row in rows if row.get("reason") == "writer_overflow"]
    data = [row for row in rows if row.get("event") == "data"]
    assert rec.writer_dropped == sum(int(row["dropped"]) for row in gaps)
    assert len(data) + rec.writer_dropped == 40


def test_offer_timeout_is_under_ping_interval() -> None:
    assert DEFAULT_OFFER_TIMEOUT == 2.0
    rec = StreamRecorder(enabled=False)
    assert rec.offer_timeout == 2.0
    with pytest.raises(ValueError, match="2s"):
        StreamRecorder(enabled=False, offer_timeout=25.0)


def test_alert_cap_merges_repeated_flaps() -> None:
    queue = EventQueue(maxsize=10, control_max=CONTROL_MAX)
    kinds = ("stale", "recovered", "book_resync", "record_error")
    for i in range(10_000):
        kind = kinds[i % 4]
        queue.put(
            StreamMessage(
                kind=kind,
                channel="trades",
                inst_id="BTC-USDT-SWAP",
                ts_ms=i,
                stale=kind == "stale",
                data={"i": i},
            ),
            control=True,
        )
    assert CONTROL_MAX == 64
    assert queue.control_max == CONTROL_MAX
    assert len(queue.alerts) <= CONTROL_MAX
    assert len(queue.alerts) == 4
    latest = {item.kind: item for item in queue.alerts}
    assert latest["stale"].ts_ms == 9996
    assert latest["recovered"].ts_ms == 9997
    assert latest["book_resync"].ts_ms == 9998
    assert latest["record_error"].ts_ms == 9999
    assert latest["record_error"].data == {"i": 9999}
    assert all(item.repeats == 2500 for item in latest.values())
    assert sum(item.repeats for item in queue.alerts) == 10_000
    assert queue.merged_alerts == 10_000 - 4
    assert len(queue.alerts) + queue.merged_alerts == 10_000


def test_non_adjacent_gaps_stay_separate() -> None:
    queue = EventQueue(maxsize=10)
    for i in range(1000):
        start = i * 20 + 1
        end = start + 9
        queue.put(
            StreamMessage(
                kind="gap",
                channel="trades",
                inst_id="BTC-USDT-SWAP",
                ts_ms=end,
                gap=GapInfo(
                    channel="trades",
                    inst_id="BTC-USDT-SWAP",
                    reason="backfill_failed",
                    first_missing_trade_id=start,
                    last_missing_trade_id=end,
                    dropped=10,
                ),
            ),
            control=True,
        )
    pending = [item for item in queue.items if item is not None and item.kind == "gap"]
    assert len(pending) == 1000
    widths = [
        int(item.gap.last_missing_trade_id) - int(item.gap.first_missing_trade_id) + 1
        for item in pending
        if item.gap is not None
        and item.gap.first_missing_trade_id is not None
        and item.gap.last_missing_trade_id is not None
    ]
    assert widths == [10] * 1000
    starts = sorted(
        item.gap.first_missing_trade_id
        for item in pending
        if item.gap is not None and item.gap.first_missing_trade_id is not None
    )
    assert starts == [i * 20 + 1 for i in range(1000)]
    assert max(item.gap.last_missing_trade_id or 0 for item in pending) == 19990
    assert min(item.gap.first_missing_trade_id or 0 for item in pending) == 1


def test_writer_non_adjacent_gaps_stay_separate(tmp_path: Path) -> None:
    rec = StreamRecorder(
        cache_dir=tmp_path,
        enabled=True,
        write_queue_maxsize=1,
        offer_timeout=0.0,
        sync=False,
    )
    rec.start()
    rec.offer_batch_sync([{"ts": 0, "event": "data", "channel": "trades", "instId": "BTC-USDT-SWAP", "data": {"tradeId": "0"}}])
    for i in range(1000):
        start = i * 20 + 1
        rec.offer_batch_sync(
            [
                {
                    "ts": start + offset,
                    "event": "data",
                    "channel": "trades",
                    "instId": "BTC-USDT-SWAP",
                    "data": {"tradeId": str(start + offset)},
                }
                for offset in range(10)
            ]
        )
    rec.close()
    rows = []
    for path in rec.files_written:
        rows.extend(_read_jsonl(path))
    gaps = [row for row in rows if row.get("reason") == "writer_overflow"]
    assert len(gaps) >= 1000
    widths = [
        int(row["lastMissingTradeId"]) - int(row["firstMissingTradeId"]) + 1
        for row in gaps
        if row.get("firstMissingTradeId") is not None
    ]
    assert max(widths) <= 10


def test_retention_still_counts_archive_bytes(tmp_path: Path) -> None:
    trades = tmp_path / "trades"
    trades.mkdir()
    (trades / "old.zip").write_bytes(b"x" * 2000)
    rec = StreamRecorder(cache_dir=tmp_path, enabled=True, retention_max_bytes=1, rotation="hourly")
    rec.write_data("trades", "BTC-USDT-SWAP", {"n": 1}, ts_ms=1)
    rec.close()
    rec2 = StreamRecorder(
        cache_dir=tmp_path,
        enabled=True,
        retention_max_bytes=1,
        rotation="hourly",
        clock=WallClock(1_700_003_600.0),
    )
    rec2.apply_retention()
    assert rec2._used >= 2000
    assert rec2._used == cache_usage_bytes(tmp_path)
    assert (tmp_path / "trades" / "old.zip").exists()
