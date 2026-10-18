"""Optional gzip-JSONL recording for the public OKX stream.

Recording is **off by default**. When enabled, files land under
``OKX_DATA_DIR/recordings/`` (same cache root as zip downloads), count
toward ``OKX_DATA_BUDGET_BYTES`` / ``OKX_DATA_RUN_BUDGET_BYTES``, and
stop with :class:`CacheBudgetExceeded` or :class:`LowDiskError`.

Rotation
    ``hourly`` or ``daily`` (default **daily**) per ``(inst_id, channel)``.

Retention
    Optional max age and/or max total recording bytes. Oldest files
    (by period stamp, then name) are deleted first.

Overflow policy for the in-memory queue lives on :class:`OkxPublicStream`;
this module only writes the gap records the stream asks it to write.

Books
    The full ``books`` channel records periodic snapshots (configurable
    interval), not every incremental update.
"""

from __future__ import annotations

import gzip
import json
import os
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal, Protocol

from tools.okx import endpoints
from tools.okx.archive import (
    BUDGET_ENV,
    DATA_DIR_ENV,
    RUN_BUDGET_ENV,
    cache_usage_bytes,
    default_budget_bytes,
    default_data_dir,
    default_run_budget_bytes,
)
from tools.okx.errors import CacheBudgetExceeded, LowDiskError

Rotation = Literal["hourly", "daily"]

MIN_FREE_DISK_ENV = "OKX_MIN_FREE_DISK_BYTES"
DEFAULT_MIN_FREE_DISK_BYTES = 10 * 1024**3
RECORDINGS_SUBDIR = "recordings"
class DiskStat(Protocol):
    free: int


DiskUsage = Callable[[str], DiskStat]
Clock = Callable[[], float]


def default_min_free_disk_bytes() -> int:
    raw = os.environ.get(MIN_FREE_DISK_ENV, "").strip()
    if not raw:
        return DEFAULT_MIN_FREE_DISK_BYTES
    return int(raw)


def _safe_channel(channel: str) -> str:
    if not channel or any(part in channel for part in ("/", "\\", "..")):
        raise ValueError(f"refusing recording channel name {channel!r}")
    if not endpoints.is_public_ws_channel(channel):
        raise ValueError(f"refusing recording channel name {channel!r}")
    return channel


def recording_period_key(ts: float, rotation: Rotation) -> str:
    moment = datetime.fromtimestamp(ts, tz=timezone.utc)
    if rotation == "hourly":
        return moment.strftime("%Y-%m-%dT%H")
    if rotation == "daily":
        return moment.strftime("%Y-%m-%d")
    raise ValueError(f"rotation must be 'hourly' or 'daily', got {rotation!r}")


def recording_dest(root: Path, inst_id: str, channel: str, period: str) -> Path:
    endpoints.validate_inst_id(inst_id)
    safe_ch = _safe_channel(channel)
    if any(part in period for part in ("/", "\\", "..")):
        raise ValueError(f"refusing recording period {period!r}")
    root_res = root.expanduser().resolve()
    dest_root = (root_res / RECORDINGS_SUBDIR / inst_id / safe_ch).resolve()
    dest = (dest_root / f"{period}.jsonl.gz").resolve()
    if not dest.is_relative_to(root_res) or not dest.is_relative_to(dest_root):
        raise ValueError(f"refusing path outside cache dir: {dest}")
    return dest


@dataclass
class ChannelThroughput:
    messages: int = 0
    raw_bytes: int = 0
    compressed_bytes: int = 0

    def per_minute(self, elapsed_s: float) -> dict[str, float]:
        minutes = max(elapsed_s, 1e-9) / 60.0
        return {
            "messages_per_minute": self.messages / minutes,
            "raw_bytes_per_minute": self.raw_bytes / minutes,
            "compressed_bytes_per_minute": self.compressed_bytes / minutes,
        }


@dataclass
class StreamRecorder:
    """Append-only gzip JSONL writer with rotation, retention, and caps."""

    cache_dir: Path
    enabled: bool = False
    rotation: Rotation = "daily"
    retention_max_age_s: float | None = None
    retention_max_bytes: int | None = None
    cache_budget_bytes: int | None = None
    run_budget_bytes: int | None = None
    min_free_disk_bytes: int | None = None
    books_snapshot_interval: float = 60.0
    disk_usage: DiskUsage = shutil.disk_usage
    clock: Clock = time.time
    _handles: dict[tuple[str, str], str] = field(default_factory=dict)
    _run_written: int = 0
    _stopped: str | None = None
    stats: dict[tuple[str, str], ChannelThroughput] = field(default_factory=dict)
    last_books_snapshot_at: dict[str, float] = field(default_factory=dict)
    files_written: list[Path] = field(default_factory=list)
    deleted: list[Path] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.cache_dir = Path(self.cache_dir).expanduser().resolve()
        if self.cache_budget_bytes is None:
            self.cache_budget_bytes = default_budget_bytes()
        if self.run_budget_bytes is None:
            self.run_budget_bytes = default_run_budget_bytes()
        if self.min_free_disk_bytes is None:
            self.min_free_disk_bytes = default_min_free_disk_bytes()
        if self.rotation not in {"hourly", "daily"}:
            raise ValueError("rotation must be 'hourly' or 'daily'")
        if self.books_snapshot_interval <= 0:
            raise ValueError("books_snapshot_interval must be positive")

    @classmethod
    def disabled(cls, cache_dir: Path | None = None) -> "StreamRecorder":
        return cls(cache_dir=cache_dir or default_data_dir(), enabled=False)

    def close(self) -> None:
        self._handles.clear()

    def __enter__(self) -> "StreamRecorder":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def stopped_reason(self) -> str | None:
        return self._stopped

    def write_data(self, channel: str, inst_id: str, payload: Any, *, ts_ms: int) -> int:
        record = {
            "ts": ts_ms,
            "channel": channel,
            "instId": inst_id,
            "data": payload,
        }
        return self._write(channel, inst_id, record)

    def write_gap(self, gap: MappingLike) -> int:
        record = {
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
        return self._write(gap.channel, gap.inst_id, record)

    def maybe_write_books_snapshot(
        self,
        inst_id: str,
        snapshot: Any,
        *,
        ts_ms: int,
        force: bool = False,
    ) -> int:
        now = self.clock()
        last = self.last_books_snapshot_at.get(inst_id)
        if not force and last is not None and (now - last) < self.books_snapshot_interval:
            return 0
        self.last_books_snapshot_at[inst_id] = now
        record = {
            "event": "book_snapshot",
            "ts": ts_ms,
            "channel": "books",
            "instId": inst_id,
            "data": snapshot,
        }
        return self._write("books", inst_id, record)

    def _write(self, channel: str, inst_id: str, record: dict[str, Any]) -> int:
        if not self.enabled or self._stopped:
            return 0
        line = (json.dumps(record, separators=(",", ":")) + "\n").encode("utf-8")
        self._assert_disk()
        self._assert_budgets(len(line))
        path = self._path_for(inst_id, channel)
        before = path.stat().st_size if path.exists() else 0
        with gzip.open(path, "ab") as handle:
            handle.write(line)
        after = path.stat().st_size
        delta = max(0, after - before)
        self._run_written += delta
        key = (inst_id, channel)
        stat = self.stats.setdefault(key, ChannelThroughput())
        stat.messages += 1
        stat.raw_bytes += len(line)
        stat.compressed_bytes += delta
        self.apply_retention()
        return delta

    def _path_for(self, inst_id: str, channel: str) -> Path:
        period = recording_period_key(self.clock(), self.rotation)
        dest = recording_dest(self.cache_dir, inst_id, channel, period)
        dest.parent.mkdir(parents=True, exist_ok=True)
        self._handles[(inst_id, channel)] = period
        if dest not in self.files_written:
            self.files_written.append(dest)
        return dest

    def _assert_budgets(self, extra: int) -> None:
        used = cache_usage_bytes(self.cache_dir)
        budget = int(self.cache_budget_bytes or 0)
        run_budget = int(self.run_budget_bytes or 0)
        if used + extra > budget:
            self._stopped = "cache_budget"
            raise CacheBudgetExceeded(
                f"recording would exceed total cache budget: have {used} bytes, "
                f"need {extra} more, cap is {budget} bytes. Raise {BUDGET_ENV} "
                f"or pass cache_budget_bytes. Cache root is ${DATA_DIR_ENV}.",
                used=used,
                budget=budget,
                extra=extra,
            )
        if self._run_written + extra > run_budget:
            self._stopped = "run_budget"
            raise CacheBudgetExceeded(
                f"recording would exceed per-run budget: have {self._run_written} bytes, "
                f"need {extra} more, cap is {run_budget} bytes. Raise {RUN_BUDGET_ENV} "
                "or pass run_budget_bytes.",
                used=self._run_written,
                budget=run_budget,
                extra=extra,
            )

    def _assert_disk(self) -> None:
        floor = int(self.min_free_disk_bytes or 0)
        if floor <= 0:
            return
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        usage = self.disk_usage(str(self.cache_dir))
        free = int(usage.free)
        if free < floor:
            self._stopped = "low_disk"
            raise LowDiskError(
                f"free disk space {free} bytes is below floor {floor} bytes "
                f"({MIN_FREE_DISK_ENV}, default {DEFAULT_MIN_FREE_DISK_BYTES}). "
                f"Recording stopped under {self.cache_dir}.",
                free=free,
                floor=floor,
                path=str(self.cache_dir),
            )

    def apply_retention(self) -> list[Path]:
        files = self._recording_files()
        now = self.clock()
        removed: list[Path] = []
        if self.retention_max_age_s is not None:
            for path in files:
                age = now - path.stat().st_mtime
                if age > self.retention_max_age_s:
                    path.unlink(missing_ok=True)
                    removed.append(path)
            files = [path for path in files if path not in removed]
        if self.retention_max_bytes is not None:
            files = sorted(files, key=_retention_sort_key)
            total = sum(path.stat().st_size for path in files if path.exists())
            while files and total > self.retention_max_bytes:
                oldest = files.pop(0)
                size = oldest.stat().st_size if oldest.exists() else 0
                oldest.unlink(missing_ok=True)
                total -= size
                removed.append(oldest)
        self.deleted.extend(removed)
        return removed

    def _recording_files(self) -> list[Path]:
        root = self.cache_dir / RECORDINGS_SUBDIR
        if not root.exists():
            return []
        return sorted((path for path in root.rglob("*.jsonl.gz") if path.is_file()), key=_retention_sort_key)

    def throughput_report(self, elapsed_s: float) -> dict[str, dict[str, float]]:
        return {
            f"{inst}:{channel}": stat.per_minute(elapsed_s)
            for (inst, channel), stat in sorted(self.stats.items())
        }


def _retention_sort_key(path: Path) -> tuple[str, float, str]:
    """Oldest period stamp first, then mtime, then name."""
    return (path.stem, path.stat().st_mtime if path.exists() else 0.0, str(path))


class MappingLike(Protocol):
    channel: str
    inst_id: str
    reason: str
    first_missing_trade_id: int | None
    last_missing_trade_id: int | None
    start_ts_ms: int | None
    end_ts_ms: int | None
    dropped: int
