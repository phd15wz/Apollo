from __future__ import annotations

import gzip
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.okx.errors import CacheBudgetExceeded, LowDiskError
from tools.okx.record import StreamRecorder, recording_period_key
from tools.okx.stream import GapInfo


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
    assert rec.usage_scans == 1
    started = time_mod.perf_counter()
    for i in range(80):
        rec.write_data("trades", "BTC-USDT-SWAP", {"tradeId": str(i), "pad": "aaaaaaaaaa"}, ts_ms=i)
    rec.close()
    elapsed = time_mod.perf_counter() - started
    assert rec.usage_scans == 1
    assert elapsed < 3.0
    raw = rec.throughput["trades"].raw_bytes
    compressed = rec.files_written[0].stat().st_size
    assert compressed < raw * 0.6


def test_writer_queue_slow_disk_coalesces_gaps(tmp_path: Path) -> None:
    rec = StreamRecorder(
        cache_dir=tmp_path,
        enabled=True,
        write_queue_maxsize=8,
        write_sleep=0.03,
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
