"""Optional gzip-JSONL recording of public stream messages.

Recording is off by default. A background thread writes one gzip stream
per active file. The socket loop enqueues whole batches and waits for
space with an asyncio-aware timeout (never a blocking put on the loop
thread). Writer-queue overflow becomes ``writer_overflow`` gap records.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
import os
import shutil
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.okx import endpoints
from tools.okx.archive import cache_usage_bytes, default_budget_bytes, default_data_dir
from tools.okx.errors import CacheBudgetExceeded, LowDiskError

_LOG = logging.getLogger(__name__)

MIN_FREE_DISK_ENV = "OKX_MIN_FREE_DISK_BYTES"
DEFAULT_MIN_FREE_DISK_BYTES = 10 * 1024 * 1024 * 1024
DEFAULT_WRITE_QUEUE_MAX = 256
DEFAULT_OFFER_TIMEOUT = 2.0
DEFAULT_USAGE_RESCAN_S = 5.0
MAX_PENDING_GAPS = 256
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
    """Build a recording path and prove it stayed under ``recordings/``."""
    inst_id = endpoints.validate_inst_id(inst_id)
    if not endpoints.is_public_ws_channel(channel):
        raise ValueError(f"refusing recording channel {channel!r}")
    if "/" in channel or "\\" in channel or ".." in channel:
        raise ValueError(f"refusing recording channel {channel!r}")
    period = recording_period_key(ts, rotation)
    root = (Path(cache_dir).expanduser().resolve() / "recordings").resolve()
    dest = (root / inst_id / channel / f"{period}.jsonl.gz").resolve()
    if not dest.is_relative_to(root):
        raise ValueError(f"refusing path outside recordings root: {dest}")
    return dest


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


def _drop_count(record: dict[str, Any]) -> int:
    if record.get("event") == "gap":
        return max(1, int(record.get("dropped") or 1))
    data = record.get("data")
    if isinstance(data, list):
        return max(1, len(data))
    return 1


def _merge_gap(gap: dict[str, Any], rec: dict[str, Any], extra: int) -> None:
    first, last = _trade_ids(rec)
    start = rec.get("startTs", rec.get("ts"))
    end = rec.get("endTs", rec.get("ts"))
    start_i = None if start in (None, "") else int(start)
    end_i = None if end in (None, "") else int(end)
    gap["dropped"] = int(gap.get("dropped") or 0) + extra
    gap["firstMissingTradeId"] = _min_opt(gap.get("firstMissingTradeId"), first)
    gap["lastMissingTradeId"] = _max_opt(gap.get("lastMissingTradeId"), last)
    gap["startTs"] = _min_opt(gap.get("startTs"), start_i)
    gap["endTs"] = _max_opt(gap.get("endTs"), end_i)
    gap["ts"] = gap["endTs"]


def _gap_dicts_adjacent(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if left.get("channel") != right.get("channel") or left.get("instId") != right.get("instId"):
        return False
    if left.get("reason") != right.get("reason"):
        return False
    lf, ll = left.get("firstMissingTradeId"), left.get("lastMissingTradeId")
    rf, rl = right.get("firstMissingTradeId"), right.get("lastMissingTradeId")
    if None not in (lf, ll, rf, rl):
        return not (int(ll) + 1 < int(rf) or int(rl) + 1 < int(lf))
    ls, le = left.get("startTs"), left.get("endTs")
    rs, re = right.get("startTs"), right.get("endTs")
    if None not in (ls, le, rs, re):
        return not (int(le) + 1 < int(rs) or int(re) + 1 < int(ls))
    return False


def _gap_from_batch(batch: Sequence[dict[str, Any]]) -> dict[str, Any]:
    extra = 0
    first = last = start = end = None
    channel = str(batch[0].get("channel") or "") if batch else ""
    inst_id = str(batch[0].get("instId") or "") if batch else ""
    holes = 0
    for rec in batch:
        extra += _drop_count(rec)
        holes += int(rec.get("holes") or 1) if rec.get("event") == "gap" else 1
        f, l = _trade_ids(rec)
        first = _min_opt(first, f)
        last = _max_opt(last, l)
        s = rec.get("startTs", rec.get("ts"))
        e = rec.get("endTs", rec.get("ts"))
        start = _min_opt(start, None if s in (None, "") else int(s))
        end = _max_opt(end, None if e in (None, "") else int(e))
        channel = str(rec.get("channel") or channel)
        inst_id = str(rec.get("instId") or inst_id)
    return {
        "ts": end,
        "event": "gap",
        "channel": channel,
        "instId": inst_id,
        "reason": "writer_overflow",
        "firstMissingTradeId": first,
        "lastMissingTradeId": last,
        "startTs": start,
        "endTs": end,
        "dropped": extra,
        "precise": True,
        "holes": max(1, holes),
    }


def _shutdown_incomplete(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    gap = _gap_from_batch(records)
    gap["reason"] = "shutdown_incomplete"
    gap["precise"] = False
    return gap


def _fold_summary_gap(existing: dict[str, Any], incoming: dict[str, Any]) -> None:
    extra = int(incoming.get("dropped") or 0)
    _merge_gap(existing, incoming, extra)
    existing["precise"] = False
    existing["holes"] = int(existing.get("holes") or 1) + int(incoming.get("holes") or 1)
    existing["reason"] = incoming.get("reason") or existing.get("reason") or "writer_overflow"


class RecordWriteQueue:
    """Bounded batch queue. Pending gaps stay outside data slots."""

    def __init__(self, maxsize: int, *, mono: Callable[[], float] | None = None) -> None:
        if maxsize < 1:
            raise ValueError("write_queue_maxsize must be >= 1")
        self.maxsize = maxsize
        self.dropped = 0
        self.max_depth = 0
        self._mono = mono or time.monotonic
        self._data: deque[tuple[float, list[dict[str, Any]]]] = deque()
        self._gaps: list[dict[str, Any]] = []
        self._waiting: list[float] = []
        self._cv = threading.Condition()
        self._closed = False
        self._sentinel = False
        self.last_pop_age = 0.0

    def wait_put(self, batch: list[dict[str, Any]], timeout: float) -> bool:
        """Wait for a data slot. On timeout or close, fold the batch into a gap."""
        started = time.monotonic()
        deadline = started + max(0.0, timeout)
        token = self._mono()
        with self._cv:
            self._waiting.append(token)
            try:
                if self._closed:
                    self._drop_batch(batch)
                    return False
                while len(self._data) >= self.maxsize and not self._closed:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        self._drop_batch(batch)
                        return False
                    self._cv.wait(timeout=remaining)
                if self._closed or len(self._data) >= self.maxsize:
                    self._drop_batch(batch)
                    return False
                recv_at = min(
                    float(rec["_recv_mono"]) if rec.get("_recv_mono") is not None else self._mono()
                    for rec in batch
                )
                self._data.append((recv_at, batch))
                self.max_depth = max(self.max_depth, len(self._data))
                self._cv.notify_all()
                return True
            finally:
                try:
                    self._waiting.remove(token)
                except ValueError:
                    pass
                self._cv.notify_all()

    def get(self, timeout: float | None = None) -> Any:
        with self._cv:
            deadline = None if timeout is None else time.monotonic() + timeout
            while not self._data and not self._gaps:
                if self._sentinel:
                    return _WRITER_SENTINEL
                remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
                if remaining == 0:
                    return None
                self._cv.wait(timeout=remaining if deadline is not None else None)
            if self._gaps:
                self.last_pop_age = 0.0
                return [self._gaps.pop(0)]
            recv_at, batch = self._data.popleft()
            self.last_pop_age = max(0.0, self._mono() - recv_at)
            self._cv.notify_all()
            return batch

    def put_sentinel(self) -> None:
        with self._cv:
            self._closed = True
            self._sentinel = True
            self._cv.notify_all()

    def take_remaining(self) -> tuple[list[list[dict[str, Any]]], list[dict[str, Any]]]:
        with self._cv:
            batches = [batch for _recv, batch in self._data]
            gaps = list(self._gaps)
            self._data.clear()
            self._gaps.clear()
            return batches, gaps

    def backlog_age(self, now: float | None = None) -> float:
        now = self._mono() if now is None else now
        with self._cv:
            times = [recv for recv, _batch in self._data] + list(self._waiting)
        if not times:
            return 0.0
        return max(0.0, now - min(times))

    def close(self) -> None:
        with self._cv:
            self._closed = True
            self._cv.notify_all()

    def __len__(self) -> int:
        with self._cv:
            return len(self._data) + len(self._gaps)

    def _drop_batch(self, batch: Sequence[dict[str, Any]]) -> None:
        if not batch:
            return
        gap = _gap_from_batch(batch)
        self.dropped += int(gap["dropped"])
        for existing in self._gaps:
            if existing.get("precise", True) and _gap_dicts_adjacent(existing, gap):
                _merge_gap(existing, gap, int(gap["dropped"]))
                self._cv.notify_all()
                return
        exact = sum(1 for item in self._gaps if item.get("precise", True))
        if exact < MAX_PENDING_GAPS:
            self._gaps.append(gap)
            self._cv.notify_all()
            return
        for existing in self._gaps:
            if existing.get("precise") is False and existing.get("channel") == gap.get("channel") and existing.get("instId") == gap.get("instId"):
                _fold_summary_gap(existing, gap)
                self._cv.notify_all()
                return
        summary = dict(gap)
        summary["precise"] = False
        self._gaps.append(summary)
        self._cv.notify_all()


@dataclass
class StreamRecorder:
    cache_dir: Path = field(default_factory=default_data_dir)
    enabled: bool = False
    rotation: str = "daily"
    retention_max_age_s: float | None = None
    retention_max_bytes: int | None = None
    books_snapshot_interval: float = 60.0
    cache_budget_bytes: int = field(default_factory=default_budget_bytes)
    run_budget_bytes: int = field(
        default_factory=lambda: int(os.environ.get("OKX_DATA_RUN_BUDGET_BYTES", 0) or 0) or 512 * 1024 * 1024
    )
    min_free_disk_bytes: int = field(default_factory=default_min_free_disk_bytes)
    clock: Callable[[], float] = time.time
    disk_usage: Callable[[str], Any] = shutil.disk_usage
    channels: frozenset[str] | None = None
    allowed_pairs: frozenset[tuple[str, str]] | None = None
    write_queue_maxsize: int = DEFAULT_WRITE_QUEUE_MAX
    offer_timeout: float = DEFAULT_OFFER_TIMEOUT
    disk_check_interval_s: float = 2.0
    usage_rescan_interval_s: float = DEFAULT_USAGE_RESCAN_S
    flush_every: int = 64
    write_sleep: float = 0.0
    write_sleep_on_close: bool = False
    write_hang: bool = False
    offer_delay: float = 0.0
    mono: Callable[[], float] = time.monotonic
    sync: bool = True
    files_written: list[Path] = field(default_factory=list)
    throughput: dict[str, ChannelThroughput] = field(default_factory=dict)
    writer_dropped: int = 0
    rejected: int = 0
    usage_scans: int = 0

    def __post_init__(self) -> None:
        if self.rotation not in {"hourly", "daily"}:
            raise ValueError("rotation must be 'hourly' or 'daily'")
        if self.offer_timeout > 2.0:
            raise ValueError("offer_timeout must be <= 2s (well under the 25s ping interval)")
        if isinstance(self.channels, Iterable) and not isinstance(self.channels, (str, frozenset)):
            self.channels = frozenset(str(ch) for ch in self.channels)
        self.cache_dir = Path(self.cache_dir)
        self._used = 0
        self._run_used = 0
        self._book_last: dict[str, float] = {}
        self._handles: dict[tuple[str, str], tuple[str, Path, Any]] = {}
        self._lines_since_flush = 0
        self._last_disk_check = 0.0
        self._last_scan = 0.0
        self._stopped: str | None = None
        self._error: BaseException | None = None
        self._closed = False
        self._started_at = self.clock()
        self._queue = RecordWriteQueue(self.write_queue_maxsize, mono=self.mono)
        self._writer: threading.Thread | None = None
        self._writer_ident: int | None = None
        self._lock = threading.Lock()
        self._offer_cv = threading.Condition()
        self._offers = 0
        self.accepted = 0
        self._write_lag = 0.0
        self._write_recv = 0.0
        self._writing = 0
        self._drop_leftover = False
        self._write_hanging = False
        self.shutdown_incomplete = False
        self._writer_starts = 0
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

    def accept(self, channel: str, inst_id: str) -> bool:
        if not self.records_channel(channel):
            return False
        try:
            endpoints.validate_inst_id(inst_id)
        except ValueError:
            self.rejected += 1
            _LOG.warning("refusing to record invalid instId %r on %s", inst_id, channel)
            return False
        if not endpoints.is_public_ws_channel(channel):
            self.rejected += 1
            _LOG.warning("refusing to record channel %r", channel)
            return False
        if self.allowed_pairs is not None and (channel, inst_id) not in self.allowed_pairs:
            self.rejected += 1
            _LOG.warning("refusing to record unsubscribed %s %s", channel, inst_id)
            return False
        return True

    def start(self) -> None:
        if not self.enabled:
            return
        self.sync = False
        self._closed = False
        self._drop_leftover = False
        if self.usage_scans == 0:
            self._scan_usage()
            self.apply_retention()
        self._ensure_writer()

    def _scan_usage(self) -> None:
        self._used = cache_usage_bytes(self.cache_dir)
        self._last_scan = self.clock()
        self.usage_scans += 1

    def _maybe_rescan(self) -> None:
        if self.clock() - self._last_scan >= self.usage_rescan_interval_s:
            self._scan_usage()

    def _ensure_writer(self) -> None:
        with self._lock:
            if self._closed:
                return
            if self._writer is not None and self._writer.is_alive():
                return
            self._writer = threading.Thread(target=self._writer_loop, name="okx-record-writer", daemon=True)
            self._writer_starts += 1
            self._writer.start()

    def _writer_loop(self) -> None:
        self._writer_ident = threading.get_ident()
        try:
            while True:
                item = self._queue.get(timeout=0.2)
                if item is _WRITER_SENTINEL:
                    self._finish_shutdown()
                    break
                if item is None:
                    continue
                self._write_lag = self._queue.last_pop_age
                self._write_recv = self.mono() - self._write_lag
                self._writing += 1
                try:
                    self._maybe_write_sleep()
                    self._write_batch(item)
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
                    self._writing -= 1
        finally:
            self._close_handles()

    def backlog_age(self) -> float:
        if not self.enabled:
            return 0.0
        now = self.mono()
        age = self._queue.backlog_age(now)
        if self._writing:
            age = max(age, now - self._write_recv, self._write_lag)
        return age

    def service_lag(self) -> float:
        return self.backlog_age()

    def _maybe_write_sleep(self) -> None:
        if self.write_sleep <= 0:
            return
        if self._closed and not self.write_sleep_on_close:
            return
        until = time.monotonic() + self.write_sleep
        while time.monotonic() < until and not self._drop_leftover:
            time.sleep(0.01)

    def _report_shutdown_incomplete(self) -> None:
        self.shutdown_incomplete = True
        batches, gaps = self._queue.take_remaining()
        leftover: list[dict[str, Any]] = []
        for batch in batches:
            leftover.extend(batch)
        leftover.extend(gaps)
        if leftover:
            gap = _shutdown_incomplete(leftover)
            self._queue.dropped += int(gap["dropped"])
        self.writer_dropped = self._queue.dropped

    def _finish_shutdown(self) -> None:
        batches, gaps = self._queue.take_remaining()
        pending = list(batches)
        if gaps:
            pending.append(gaps)
        while pending:
            if self._drop_leftover:
                leftover: list[dict[str, Any]] = []
                for batch in pending:
                    leftover.extend(batch)
                gap = _shutdown_incomplete(leftover)
                self._queue.dropped += int(gap["dropped"])
                self._write_one([gap])
                return
            self._maybe_write_sleep()
            if self._drop_leftover:
                continue
            self._write_one(pending.pop(0))

    def _write_one(self, records: list[dict[str, Any]]) -> None:
        self._writing += 1
        try:
            self._write_batch(records)
        except Exception:
            pass
        finally:
            self._writing -= 1

    def _begin_offer(self) -> None:
        with self._offer_cv:
            self._offers += 1

    def _end_offer(self) -> None:
        with self._offer_cv:
            self._offers -= 1
            self._offer_cv.notify_all()

    def offer_batch_sync(self, records: list[dict[str, Any]], timeout: float | None = None) -> bool:
        self._begin_offer()
        try:
            if self.offer_delay > 0:
                time.sleep(self.offer_delay)
            kept = [rec for rec in records if self.accept(str(rec.get("channel") or ""), str(rec.get("instId") or ""))]
            if not kept:
                return False
            self.accepted += sum(_drop_count(rec) for rec in kept)
            if self._closed:
                with self._queue._cv:
                    self._queue._drop_batch(kept)
                self.writer_dropped = self._queue.dropped
                return False
            self._maybe_rescan()
            self._assert_disk()
            self._assert_budgets(0)
            self._ensure_writer()
            accepted = self._queue.wait_put(kept, timeout if timeout is not None else self.offer_timeout)
            self.writer_dropped = self._queue.dropped
            return accepted
        finally:
            self._end_offer()

    async def offer_batch(self, records: list[dict[str, Any]], timeout: float | None = None) -> bool:
        """Enqueue a batch without blocking the event-loop thread."""
        return await asyncio.to_thread(self.offer_batch_sync, records, timeout)

    def offer(self, record: dict[str, Any]) -> None:
        self.offer_batch_sync([record])

    def write_data(self, channel: str, inst_id: str, data: Any, *, ts_ms: int | None = None) -> int:
        if not self.accept(channel, inst_id):
            return 0
        record = {
            "ts": ts_ms,
            "event": "data",
            "channel": channel,
            "instId": inst_id,
            "data": data,
        }
        before = self._run_used
        self.offer_batch_sync([record])
        if self.sync:
            self.flush()
            self._raise_pending()
        return max(0, self._run_used - before)

    def write_gap(self, gap: Any) -> int:
        if not self.accept(getattr(gap, "channel", ""), getattr(gap, "inst_id", "")):
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
        self.offer_batch_sync([record])
        if self.sync:
            self.flush()
            self._raise_pending()
        return max(0, self._run_used - before)

    def maybe_write_books_snapshot(self, inst_id: str, snapshot: Any, *, ts_ms: int | None = None) -> int:
        if not self.accept("books", inst_id):
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
        self.offer_batch_sync([record])
        if self.sync:
            self.flush()
            self._raise_pending()
        return max(0, self._run_used - before)

    def flush(self, timeout: float = 10.0) -> None:
        if self._writer is None:
            return
        deadline = time.monotonic() + timeout
        while (len(self._queue) > 0 or self._writing > 0) and time.monotonic() < deadline:
            time.sleep(0.005)

    def _raise_pending(self) -> None:
        if self._error is not None:
            raise self._error

    def stopped_reason(self) -> str | None:
        return self._stopped

    def open_paths(self) -> set[Path]:
        return {path for _period, path, _handle in self._handles.values()}

    def apply_retention(self) -> None:
        recordings = self.cache_dir / "recordings"
        if not recordings.is_dir():
            return
        skip = {path.resolve() for path in self.open_paths()}
        files = [p for p in recordings.rglob("*.jsonl.gz") if p.is_file() and p.resolve() not in skip]
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
        self._scan_usage()

    def close(self, timeout: float = 8.0) -> None:
        if self._writer is None and self._closed:
            return
        deadline = time.monotonic() + max(0.0, timeout)
        self._closed = True
        self._queue.close()
        with self._offer_cv:
            while self._offers > 0 and time.monotonic() < deadline:
                self._offer_cv.wait(timeout=max(0.001, deadline - time.monotonic()))
        self._queue.put_sentinel()
        if self._writer is not None:
            remaining = max(0.0, deadline - time.monotonic())
            self._writer.join(timeout=remaining)
            if self._writer.is_alive():
                self._drop_leftover = True
                leftover_join = max(0.0, deadline - time.monotonic())
                self._writer.join(timeout=leftover_join)
            if self._writer.is_alive():
                self._report_shutdown_incomplete()
            self._writer = None
        self.writer_dropped = self._queue.dropped
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

    def _write_batch(self, records: list[dict[str, Any]]) -> None:
        if self.write_hang:
            self._write_hanging = True
            while self.write_hang:
                time.sleep(0.05)
        if self._writer_ident is not None and threading.get_ident() != self._writer_ident:
            raise RuntimeError("only the writer thread may open or write recording files")
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for record in records:
            channel = str(record.get("channel") or "")
            inst_id = str(record.get("instId") or "")
            if not self.accept(channel, inst_id):
                continue
            grouped.setdefault((inst_id, channel), []).append(record)
        for (inst_id, channel), group in grouped.items():
            try:
                dest, handle = self._handle(inst_id, channel)
            except ValueError:
                self.rejected += len(group)
                continue
            raw = 0
            before = dest.stat().st_size if dest.exists() else 0
            for record in group:
                payload = {key: value for key, value in record.items() if not str(key).startswith("_")}
                line = json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n"
                raw += len(line)
                handle.write(line)
            handle.flush()
            after = dest.stat().st_size if dest.exists() else before
            delta = max(0, after - before)
            self._maybe_rescan()
            self._assert_disk()
            self._assert_budgets(delta)
            with self._lock:
                self._used += delta
                self._run_used += delta
            stats = self.throughput.setdefault(channel, ChannelThroughput())
            stats.messages += len(group)
            stats.raw_bytes += raw
            self._note_file(dest)

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
        dest = dest.resolve()
        root = (self.cache_dir.resolve() / "recordings").resolve()
        if not dest.is_relative_to(root):
            raise ValueError(f"refusing path outside recordings root: {dest}")
        handle = gzip.open(dest, "ab")
        self._handles[key] = (period, dest, handle)
        self._note_file(dest)
        return dest, handle

    def _close_handles(self) -> None:
        for _period, _path, handle in list(self._handles.values()):
            try:
                handle.flush()
                handle.close()
            except Exception:
                pass
        self._handles.clear()

    def _note_file(self, path: Path) -> None:
        if path not in self.files_written:
            self.files_written.append(path)

    def _forget_file(self, path: Path) -> None:
        if path in self.files_written:
            self.files_written.remove(path)

    def _assert_budgets(self, extra_on_disk: int) -> None:
        if extra_on_disk < 0:
            raise ValueError("negative write size")
        if self._used + extra_on_disk > self.cache_budget_bytes:
            self._stopped = "budget"
            raise CacheBudgetExceeded(
                f"recording would exceed cache budget {self.cache_budget_bytes}",
                used=self._used,
                budget=self.cache_budget_bytes,
                extra=extra_on_disk,
            )
        if self._run_used + extra_on_disk > self.run_budget_bytes:
            self._stopped = "budget"
            raise CacheBudgetExceeded(
                f"recording would exceed per-run budget {self.run_budget_bytes}",
                used=self._run_used,
                budget=self.run_budget_bytes,
                extra=extra_on_disk,
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
