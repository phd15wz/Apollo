"""Optional gzip-JSONL recording of public stream messages.

Recording is off by default. When enabled, a bounded background writer
keeps one gzip stream open per active file so the socket loop never
walks the data folder or blocks on disk. Writer-queue overflow is
coalesced into gap records (``reason=writer_overflow``) so Hawkes fits
never mistake a drop for a quiet period.
"""

from __future__ import annotations

import gzip
import json
import os
import shutil
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.okx.archive import cache_usage_bytes, default_budget_bytes, default_data_dir
from tools.okx.errors import CacheBudgetExceeded, LowDiskError

MIN_FREE_DISK_ENV = "OKX_MIN_FREE_DISK_BYTES"
DEFAULT_MIN_FREE_DISK_BYTES = 10 * 1024 * 1024 * 1024
DEFAULT_WRITE_QUEUE_MAX = 256
_WRITER_SENTINEL = object()


def default_min_free_disk_bytes() -> int:
    raw = os.environ.get(MIN_FREE_DISK_ENV, "").strip()
    if raw:
        return max(0, int(raw))
    return DEFAULT_MIN_FREE_DISK_BYTES


def recording_period_key(ts: float, rotation: str) -> str:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    if rotation == "hourly":
        return dt.strftime("%Y%m%dT%H")
    if rotation == "daily":
        return dt.strftime("%Y%m%d")
    raise ValueError("rotation must be 'hourly' or 'daily'")


def recording_dest(cache_dir: Path, inst_id: str, channel: str, ts: float, rotation: str) -> Path:
    period = recording_period_key(ts, rotation)
    return cache_dir / "recordings" / inst_id / channel / f"{period}.jsonl.gz"


@dataclass
class ChannelThroughput:
    messages: int = 0
    raw_bytes: int = 0
    compressed_bytes: int = 0
    elapsed_s: float = 0.0

    def per_minute(self) -> dict[str, float]:
        minutes = self.elapsed_s / 60.0 if self.elapsed_s > 0 else 1.0
        return {
            "messages_per_min": self.messages / minutes,
            "raw_bytes_per_min": self.raw_bytes / minutes,
            "compressed_bytes_per_min": self.compressed_bytes / minutes,
        }


def _min_opt(a: int | None, b: int | None) -> int | None:
    if a is None:
        return b
    if b is None:
        return a
    return min(int(a), int(b))


def _max_opt(a: int | None, b: int | None) -> int | None:
    if a is None:
        return b
    if b is None:
        return a
    return max(int(a), int(b))


def _trade_ids(record: dict[str, Any]) -> tuple[int | None, int | None]:
    data = record.get("data")
    if isinstance(data, dict) and data.get("tradeId") not in (None, ""):
        tid = int(data["tradeId"])
        return tid, tid
    first = record.get("firstMissingTradeId")
    last = record.get("lastMissingTradeId")
    return (
        None if first in (None, "") else int(first),
        None if last in (None, "") else int(last),
    )


class RecordWriteQueue:
    """Bounded producer/consumer queue. Drops coalesce into one overflow gap."""

    def __init__(self, maxsize: int) -> None:
        if maxsize < 1:
            raise ValueError("write_queue_maxsize must be >= 1")
        self.maxsize = maxsize
        self.dropped = 0
        self.max_depth = 0
        self._items: deque[Any] = deque()
        self._cv = threading.Condition()
        self._closed = False

    def put(self, record: dict[str, Any]) -> None:
        with self._cv:
            if self._closed:
                return
            while len(self._items) >= self.maxsize:
                self._evict()
            self._items.append(record)
            self.max_depth = max(self.max_depth, len(self._items))
            self._cv.notify()

    def get(self, timeout: float | None = None) -> Any:
        with self._cv:
            deadline = None if timeout is None else time.monotonic() + timeout
            while not self._items:
                if self._closed:
                    return _WRITER_SENTINEL
                remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
                if remaining == 0:
                    return None
                self._cv.wait(timeout=remaining if deadline is not None else None)
            return self._items.popleft()

    def close(self) -> None:
        with self._cv:
            self._closed = True
            self._items.append(_WRITER_SENTINEL)
            self._cv.notify_all()

    def __len__(self) -> int:
        with self._cv:
            return len(self._items)

    def _evict(self) -> None:
        for index, rec in enumerate(self._items):
            if rec is _WRITER_SENTINEL:
                continue
            if rec.get("event") == "gap" and rec.get("reason") == "writer_overflow":
                continue
            del self._items[index]
            if rec.get("event") == "gap":
                extra = int(rec.get("dropped") or 1)
            else:
                extra = 1
            self.dropped += extra
            self._absorb(rec, extra)
            return

    def _absorb(self, rec: dict[str, Any], extra: int) -> None:
        channel = str(rec.get("channel") or "")
        inst_id = str(rec.get("instId") or "")
        first, last = _trade_ids(rec)
        start = rec.get("startTs", rec.get("ts"))
        end = rec.get("endTs", rec.get("ts"))
        start_i = None if start in (None, "") else int(start)
        end_i = None if end in (None, "") else int(end)
        for item in self._items:
            if (
                item is not _WRITER_SENTINEL
                and item.get("event") == "gap"
                and item.get("reason") == "writer_overflow"
                and item.get("channel") == channel
                and item.get("instId") == inst_id
            ):
                item["dropped"] = int(item.get("dropped") or 0) + extra
                item["firstMissingTradeId"] = _min_opt(item.get("firstMissingTradeId"), first)
                item["lastMissingTradeId"] = _max_opt(item.get("lastMissingTradeId"), last)
                item["startTs"] = _min_opt(item.get("startTs"), start_i)
                item["endTs"] = _max_opt(item.get("endTs"), end_i)
                item["ts"] = item["endTs"]
                return
        self._items.appendleft(
            {
                "ts": end_i,
                "event": "gap",
                "channel": channel,
                "instId": inst_id,
                "reason": "writer_overflow",
                "firstMissingTradeId": first,
                "lastMissingTradeId": last,
                "startTs": start_i,
                "endTs": end_i,
                "dropped": extra,
            }
        )


@dataclass
class StreamRecorder:
    cache_dir: Path = field(default_factory=default_data_dir)
    enabled: bool = False
    rotation: str = "daily"
    retention_max_age_s: float | None = None
    retention_max_bytes: int | None = None
    books_snapshot_interval: float = 60.0
    cache_budget_bytes: int = field(default_factory=default_budget_bytes)
    run_budget_bytes: int = field(default_factory=lambda: int(os.environ.get("OKX_DATA_RUN_BUDGET_BYTES", 0) or 0) or 512 * 1024 * 1024)
    min_free_disk_bytes: int = field(default_factory=default_min_free_disk_bytes)
    clock: Callable[[], float] = time.time
    disk_usage: Callable[[str], Any] = shutil.disk_usage
    channels: frozenset[str] | None = None
    write_queue_maxsize: int = DEFAULT_WRITE_QUEUE_MAX
    disk_check_interval_s: float = 2.0
    flush_every: int = 64
    write_sleep: float = 0.0
    sync: bool = True
    files_written: list[Path] = field(default_factory=list)
    throughput: dict[str, ChannelThroughput] = field(default_factory=dict)
    writer_dropped: int = 0
    usage_scans: int = 0

    def __post_init__(self) -> None:
        if self.rotation not in {"hourly", "daily"}:
            raise ValueError("rotation must be 'hourly' or 'daily'")
        if isinstance(self.channels, Iterable) and not isinstance(self.channels, (str, frozenset)):
            self.channels = frozenset(str(ch) for ch in self.channels)
        self.cache_dir = Path(self.cache_dir)
        self._used = 0
        self._run_used = 0
        self._book_last: dict[str, float] = {}
        self._handles: dict[tuple[str, str], tuple[str, Path, Any]] = {}
        self._lines_since_flush = 0
        self._last_disk_check = 0.0
        self._stopped: str | None = None
        self._error: BaseException | None = None
        self._started_at = self.clock()
        self._queue = RecordWriteQueue(self.write_queue_maxsize)
        self._writer: threading.Thread | None = None
        self._lock = threading.Lock()
        self._done = threading.Event()
        if self.enabled:
            self._scan_usage()
            self.apply_retention()

    @classmethod
    def disabled(cls) -> "StreamRecorder":
        return cls(enabled=False)

    def records_channel(self, channel: str) -> bool:
        if not self.enabled or self._stopped:
            return False
        if self.channels is None:
            return True
        return channel in self.channels

    def start(self) -> None:
        if not self.enabled:
            return
        self.sync = False
        if self.usage_scans == 0:
            self._scan_usage()
            self.apply_retention()
        self._ensure_writer()

    def _scan_usage(self) -> None:
        self._used = cache_usage_bytes(self.cache_dir)
        self.usage_scans += 1

    def _ensure_writer(self) -> None:
        if self._writer is not None and self._writer.is_alive():
            return
        self._done.clear()
        self._writer = threading.Thread(target=self._writer_loop, name="okx-record-writer", daemon=True)
        self._writer.start()

    def _writer_loop(self) -> None:
        try:
            while True:
                item = self._queue.get(timeout=0.2)
                if item is _WRITER_SENTINEL:
                    break
                if item is None:
                    continue
                if self.write_sleep > 0:
                    time.sleep(self.write_sleep)
                try:
                    self._write_record(item)
                except BaseException as exc:
                    self._error = exc
                    if isinstance(exc, LowDiskError):
                        self._stopped = "low_disk"
                    elif isinstance(exc, CacheBudgetExceeded):
                        self._stopped = "budget"
                    else:
                        self._stopped = "write_error"
                    break
        finally:
            self._close_handles()
            self._done.set()

    def offer(self, record: dict[str, Any]) -> None:
        if not self.records_channel(str(record.get("channel") or "")):
            return
        extra = len(json.dumps(record, separators=(",", ":"))) + 1
        self._assert_disk()
        self._assert_budgets(extra)
        self._ensure_writer()
        self._queue.put(record)
        self.writer_dropped = self._queue.dropped

    def write_data(self, channel: str, inst_id: str, data: Any, *, ts_ms: int | None = None) -> int:
        if not self.records_channel(channel):
            return 0
        record = {
            "ts": ts_ms,
            "event": "data",
            "channel": channel,
            "instId": inst_id,
            "data": data,
        }
        before = self._run_used
        self.offer(record)
        if self.sync:
            self.flush()
            self._raise_pending()
        return max(0, self._run_used - before)

    def write_gap(self, gap: Any) -> int:
        if not self.records_channel(getattr(gap, "channel", "")):
            return 0
        record = {
            "ts": gap.end_ts_ms,
            "event": "gap",
            "channel": gap.channel,
            "instId": gap.inst_id,
            "reason": gap.reason,
            "firstMissingTradeId": gap.first_missing_trade_id,
            "lastMissingTradeId": gap.last_missing_trade_id,
            "startTs": gap.start_ts_ms,
            "endTs": gap.end_ts_ms,
            "dropped": gap.dropped,
        }
        before = self._run_used
        self.offer(record)
        if self.sync:
            self.flush()
            self._raise_pending()
        return max(0, self._run_used - before)

    def maybe_write_books_snapshot(self, inst_id: str, snapshot: Any, *, ts_ms: int | None = None) -> int:
        if not self.records_channel("books"):
            return 0
        now = self.clock()
        last = self._book_last.get(inst_id)
        if last is not None and (now - last) < self.books_snapshot_interval:
            return 0
        self._book_last[inst_id] = now
        record = {
            "ts": ts_ms,
            "event": "book_snapshot",
            "channel": "books",
            "instId": inst_id,
            "data": snapshot,
        }
        before = self._run_used
        self.offer(record)
        if self.sync:
            self.flush()
            self._raise_pending()
        return max(0, self._run_used - before)

    def flush(self, timeout: float = 10.0) -> None:
        if self._writer is None:
            return
        deadline = time.monotonic() + timeout
        while len(self._queue) > 0 and time.monotonic() < deadline:
            time.sleep(0.005)
        with self._lock:
            for _period, _path, handle in self._handles.values():
                handle.flush()

    def _raise_pending(self) -> None:
        if self._error is not None:
            raise self._error

    def stopped_reason(self) -> str | None:
        return self._stopped

    def apply_retention(self) -> None:
        recordings = self.cache_dir / "recordings"
        if not recordings.is_dir():
            return
        files = [p for p in recordings.rglob("*.jsonl.gz") if p.is_file()]
        now = self.clock()
        changed = False
        if self.retention_max_age_s is not None:
            for path in list(files):
                if now - path.stat().st_mtime >= self.retention_max_age_s:
                    self._forget_file(path)
                    path.unlink(missing_ok=True)
                    files.remove(path)
                    changed = True
        if self.retention_max_bytes is not None:
            files.sort(key=lambda p: p.stat().st_mtime)
            total = sum(p.stat().st_size for p in files)
            while files and total > self.retention_max_bytes:
                oldest = files.pop(0)
                total -= oldest.stat().st_size
                self._forget_file(oldest)
                oldest.unlink(missing_ok=True)
                changed = True
        if changed:
            self._used = sum(
                p.stat().st_size for p in (self.cache_dir / "recordings").rglob("*.jsonl.gz") if p.is_file()
            )

    def close(self) -> None:
        try:
            self.flush()
        except Exception:
            pass
        self._queue.close()
        if self._writer is not None:
            self._writer.join(timeout=5.0)
        self._close_handles()
        elapsed = max(1e-6, self.clock() - self._started_at)
        for stats in self.throughput.values():
            stats.elapsed_s = elapsed
            stats.compressed_bytes = 0
        for path in self.files_written:
            if path.is_file():
                size = path.stat().st_size
                channel = path.parent.name
                self.throughput.setdefault(channel, ChannelThroughput()).compressed_bytes += size

    def throughput_report(self, elapsed_s: float | None = None) -> dict[str, dict[str, float]]:
        elapsed = self.clock() - self._started_at if elapsed_s is None else elapsed_s
        minutes = max(elapsed, 1e-9) / 60.0
        report: dict[str, dict[str, float]] = {}
        for channel, stats in self.throughput.items():
            report[channel] = {
                "messages_per_minute": stats.messages / minutes,
                "raw_bytes_per_minute": stats.raw_bytes / minutes,
                "compressed_bytes_per_minute": stats.compressed_bytes / minutes,
                "messages": float(stats.messages),
                "raw_bytes": float(stats.raw_bytes),
                "compressed_bytes": float(stats.compressed_bytes),
            }
        return report

    def _write_record(self, record: dict[str, Any]) -> None:
        channel = str(record.get("channel") or "unknown")
        inst_id = str(record.get("instId") or "unknown")
        line = json.dumps(record, separators=(",", ":")).encode("utf-8") + b"\n"
        extra = len(line)
        self._assert_disk()
        self._assert_budgets(extra)
        dest, handle = self._handle(inst_id, channel)
        before = dest.stat().st_size if dest.exists() else 0
        handle.write(line)
        self._lines_since_flush += 1
        if self._lines_since_flush >= self.flush_every:
            handle.flush()
            self._lines_since_flush = 0
        after = dest.stat().st_size if dest.exists() else before + extra
        delta = max(0, after - before)
        if delta == 0:
            delta = extra
        with self._lock:
            self._used += delta
            self._run_used += extra
        stats = self.throughput.setdefault(channel, ChannelThroughput())
        stats.messages += 1
        stats.raw_bytes += extra
        if dest not in self.files_written:
            self.files_written.append(dest)

    def _handle(self, inst_id: str, channel: str) -> tuple[Path, Any]:
        period = recording_period_key(self.clock(), self.rotation)
        key = (inst_id, channel)
        current = self._handles.get(key)
        if current is not None and current[0] == period:
            return current[1], current[2]
        if current is not None:
            current[2].close()
            del self._handles[key]
            self.apply_retention()
        dest = recording_dest(self.cache_dir, inst_id, channel, self.clock(), self.rotation)
        dest.parent.mkdir(parents=True, exist_ok=True)
        handle = gzip.open(dest, "ab")
        self._handles[key] = (period, dest, handle)
        if dest not in self.files_written:
            self.files_written.append(dest)
        return dest, handle

    def _close_handles(self) -> None:
        for _period, _path, handle in list(self._handles.values()):
            try:
                handle.flush()
                handle.close()
            except Exception:
                pass
        self._handles.clear()

    def _forget_file(self, path: Path) -> None:
        if path in self.files_written:
            self.files_written.remove(path)

    def _assert_budgets(self, extra: int) -> None:
        if extra < 0:
            raise ValueError("negative write size")
        if self._used + extra > self.cache_budget_bytes:
            self._stopped = "budget"
            raise CacheBudgetExceeded(
                f"recording would exceed cache budget {self.cache_budget_bytes}",
                used=self._used,
                budget=self.cache_budget_bytes,
                extra=extra,
            )
        if self._run_used + extra > self.run_budget_bytes:
            self._stopped = "budget"
            raise CacheBudgetExceeded(
                f"recording would exceed per-run budget {self.run_budget_bytes}",
                used=self._run_used,
                budget=self.run_budget_bytes,
                extra=extra,
            )

    def _assert_disk(self) -> None:
        now = self.clock()
        if self._last_disk_check and (now - self._last_disk_check) < self.disk_check_interval_s:
            return
        self._last_disk_check = now
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        usage = self.disk_usage(str(self.cache_dir))
        free = int(getattr(usage, "free"))
        if free < self.min_free_disk_bytes:
            self._stopped = "low_disk"
            raise LowDiskError(
                f"free disk {free} is below floor {self.min_free_disk_bytes} on {self.cache_dir}",
                free=free,
                floor=self.min_free_disk_bytes,
                path=str(self.cache_dir),
            )
