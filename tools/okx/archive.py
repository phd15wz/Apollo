"""Official OKX public historical-trade downloader.

Source of truth (do not guess CDN layouts):

- Product page: https://www.okx.com/historical-data
- REST listing (keyless): ``GET /api/v5/public/market-data-history``
  https://www.okx.com/docs-v5/en/#public-data-rest-api-get-historical-market-data
- Each listing row includes a ``url`` on ``static.okx.com`` and a ``sizeMB``.
- Terms: https://www.okx.com/en-us/help/historicaldata-terms-and-conditions

Daily trade files for a busy SWAP such as BTC-USDT-SWAP can be several
GB per month. Downloads go to one cache directory (``OKX_DATA_DIR``,
default ``~/.cache/apollo/okx``), never into the repo. A total-cache
budget and a per-run byte limit both default to 4 GiB and stop cleanly
with :class:`CacheBudgetExceeded` when exceeded.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

import requests

from tools.okx import endpoints
from tools.okx.client import PublicClient
from tools.okx.errors import CacheBudgetExceeded, OkxHttpError, UnsafeArchiveError
from tools.okx.types import ArchiveFile, Trade, parse_trade_id

DATA_DIR_ENV = "OKX_DATA_DIR"
BUDGET_ENV = "OKX_DATA_BUDGET_BYTES"
RUN_BUDGET_ENV = "OKX_DATA_RUN_BUDGET_BYTES"
DEFAULT_BUDGET_BYTES = 4 * 1024**3
DEFAULT_MAX_UNCOMPRESSED_BYTES = 2 * 1024**3
DEFAULT_MAX_COMPRESSION_RATIO = 100.0
SIZE_TOLERANCE = 0.25
CHUNK = 1024 * 256

Streamer = Callable[[str, Mapping[str, str], float], "StreamResponse"]


def default_data_dir() -> Path:
    """Single cache root. Override with ``OKX_DATA_DIR``. Never the repo."""
    env = os.environ.get(DATA_DIR_ENV, "").strip()
    if env:
        return Path(env).expanduser().resolve()
    xdg = os.environ.get("XDG_CACHE_HOME", "").strip()
    if xdg:
        return Path(xdg).expanduser().resolve() / "apollo" / "okx"
    return Path.home().resolve() / ".cache" / "apollo" / "okx"


def default_budget_bytes() -> int:
    return _env_int(BUDGET_ENV, DEFAULT_BUDGET_BYTES)


def default_run_budget_bytes() -> int:
    return _env_int(RUN_BUDGET_ENV, DEFAULT_BUDGET_BYTES)


def _env_int(name: str, fallback: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return fallback
    return int(raw)


def cache_usage_bytes(root: Path) -> int:
    if not root.exists():
        return 0
    total = 0
    for path in root.rglob("*"):
        if path.is_file():
            total += path.stat().st_size
    return total


def safe_archive_filename(filename: str) -> str:
    """Reject traversal, absolute paths, and empty names; return a basename."""
    name = str(filename or "")
    if not name or name in {".", ".."}:
        raise ValueError("empty or invalid archive filename")
    if name.startswith("/") or name.startswith("\\"):
        raise ValueError(f"refusing absolute archive filename {filename!r}")
    if len(name) >= 2 and name[1] == ":":
        raise ValueError(f"refusing absolute archive filename {filename!r}")
    if "/" in name or "\\" in name or ".." in name:
        raise ValueError(f"refusing archive filename {filename!r}")
    base = Path(name).name
    if base != name:
        raise ValueError(f"refusing archive filename {filename!r}")
    return base


def cache_dest(root: Path, inst_id: str, filename: str) -> Path:
    """Join a listing filename under the cache, then prove it stayed inside."""
    endpoints.validate_inst_id(inst_id)
    safe = safe_archive_filename(filename)
    root_res = root.expanduser().resolve()
    dest_root = (root_res / "trades" / _inst_type(inst_id) / inst_id).resolve()
    dest = (dest_root / safe).resolve()
    if not dest.is_relative_to(root_res) or not dest.is_relative_to(dest_root):
        raise ValueError(f"refusing path outside cache dir: {dest}")
    return dest


def _inst_type(inst_id: str) -> str:
    if inst_id.endswith("-SWAP"):
        return "SWAP"
    parts = inst_id.split("-")
    if len(parts) >= 3 and parts[-1][:1].isdigit():
        return "FUTURES"
    return "SPOT"


def inst_family(inst_id: str, inst_type: str | None = None) -> str:
    kind = inst_type or _inst_type(inst_id)
    if kind == "SPOT":
        return inst_id
    if kind == "SWAP" and inst_id.endswith("-SWAP"):
        return inst_id[: -len("-SWAP")]
    parts = inst_id.split("-")
    if len(parts) >= 2:
        return "-".join(parts[:2])
    return inst_id


def _day_bounds_ms(day: date) -> tuple[int, int]:
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    end = start + timedelta(days=1) - timedelta(milliseconds=1)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def _parse_day(value: date | str) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return date.fromisoformat(str(value)[:10])


def _windows(start: date, end: date, width: int) -> list[tuple[date, date]]:
    if end < start:
        raise ValueError("end date is before start date")
    out: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        last = min(end, cursor + timedelta(days=width - 1))
        out.append((cursor, last))
        cursor = last + timedelta(days=1)
    return out


def _filename_date(filename: str, date_ts_ms: int | None) -> str:
    stem = filename[:-4] if filename.lower().endswith(".zip") else filename
    if "-trades-" in stem:
        tail = stem.rsplit("-trades-", 1)[-1]
        candidate = tail[:10]
        try:
            date.fromisoformat(candidate)
            return candidate
        except ValueError:
            pass
    if len(stem) >= 10:
        try:
            date.fromisoformat(stem[-10:])
            return stem[-10:]
        except ValueError:
            pass
    if date_ts_ms:
        return datetime.fromtimestamp(date_ts_ms / 1000.0, tz=timezone.utc).date().isoformat()
    return "unknown"


def _size_mb(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    return float(text)


def list_daily_trade_files(
    client: PublicClient,
    inst_id: str,
    start: date | str,
    end: date | str,
) -> list[ArchiveFile]:
    """Ask OKX which daily trade zips exist. Does not download them."""
    endpoints.validate_inst_id(inst_id)
    start_d = _parse_day(start)
    end_d = _parse_day(end)
    kind = _inst_type(inst_id)
    family = inst_family(inst_id, kind)
    files: list[ArchiveFile] = []
    seen: set[str] = set()
    for win_start, win_end in _windows(start_d, end_d, endpoints.ARCHIVE_DAILY_WINDOW_DAYS):
        begin_ms, _ = _day_bounds_ms(win_start)
        _, end_ms = _day_bounds_ms(win_end)
        params: dict[str, Any] = {
            "module": endpoints.ARCHIVE_MODULE_TRADES,
            "instType": kind,
            "dateAggrType": "daily",
            "begin": str(begin_ms),
            "end": str(end_ms),
        }
        if kind == "SPOT":
            params["instIdList"] = inst_id
        else:
            params["instFamilyList"] = family
        payload = client.get(endpoints.MARKET_DATA_HISTORY, params)
        for block in payload:
            for detail in block.get("details") or []:
                wanted = str(detail.get("instId") or "")
                for group in detail.get("groupDetails") or []:
                    filename = str(group.get("filename") or "")
                    url = str(group.get("url") or "")
                    if not filename or not url:
                        continue
                    stem = filename.split("-trades-")[0] if "-trades-" in filename else ""
                    identity = wanted or stem
                    if identity and identity != inst_id:
                        continue
                    if not identity and inst_id not in filename:
                        continue
                    if filename in seen:
                        continue
                    seen.add(filename)
                    date_ts = int(group["dateTs"]) if group.get("dateTs") else None
                    files.append(
                        ArchiveFile(
                            inst_id=wanted or inst_id,
                            filename=filename,
                            url=url,
                            size_mb=_size_mb(group.get("sizeMB")),
                            date_ts_ms=date_ts,
                            date=_filename_date(filename, date_ts),
                        )
                    )
    files.sort(key=lambda item: (item.date, item.filename))
    return files


@dataclass
class StreamResponse:
    status_code: int
    headers: Mapping[str, str]
    iter_bytes: Callable[[int], Any]
    url: str = ""


class RequestsStreamer:
    def __call__(self, url: str, headers: Mapping[str, str], timeout: float) -> StreamResponse:
        response = requests.get(url, headers=dict(headers), timeout=timeout, stream=True)
        return StreamResponse(
            status_code=response.status_code,
            headers=dict(response.headers),
            iter_bytes=lambda size: response.iter_content(chunk_size=size),
            url=str(response.url),
        )


@dataclass
class DownloadResult:
    files: list[Path]
    skipped: list[Path]
    bytes_written: int
    cache_dir: Path


def _expected_bytes(size_mb: float | None, content_length: str | None) -> int | None:
    candidates: list[int] = []
    if content_length:
        try:
            candidates.append(int(content_length))
        except ValueError:
            pass
    if size_mb is not None:
        candidates.append(int(size_mb * 1_000_000))
        candidates.append(int(size_mb * 1024 * 1024))
    if not candidates:
        return None
    return max(candidates)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _zip_ok(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as zf:
            _select_csv_member(zf)
        return True
    except (zipfile.BadZipFile, UnsafeArchiveError, ValueError, OSError):
        return False


def _within_size(path: Path, size_mb: float | None, content_length: int | None) -> bool:
    actual = path.stat().st_size
    if actual <= 0:
        return False
    if content_length is not None and content_length > 0:
        if abs(actual - content_length) > max(64, int(content_length * SIZE_TOLERANCE)):
            return False
    if size_mb is None:
        return True
    expected_si = size_mb * 1_000_000
    expected_bin = size_mb * 1024 * 1024
    low = min(expected_si, expected_bin) * (1 - SIZE_TOLERANCE)
    high = max(expected_si, expected_bin) * (1 + SIZE_TOLERANCE)
    return low <= actual <= high


class _BoundedReader(io.BufferedIOBase):
    """Count inflated bytes and abort if size or ratio caps are exceeded."""

    def __init__(
        self,
        inner: Any,
        *,
        max_bytes: int,
        max_ratio: float,
        compressed_size: int,
    ) -> None:
        super().__init__()
        self._inner = inner
        self._max_bytes = max_bytes
        self._max_ratio = max_ratio
        self._compressed = max(int(compressed_size), 1)
        self._n = 0

    def readable(self) -> bool:
        return True

    def read(self, size: int | None = -1) -> bytes:
        if size is None or size < 0:
            chunks: list[bytes] = []
            while True:
                piece = self._take(65536)
                if not piece:
                    break
                chunks.append(piece)
            return b"".join(chunks)
        return self._take(size)

    def read1(self, size: int = -1) -> bytes:
        want = 65536 if size is None or size < 0 else size
        return self._take(want)

    def _take(self, size: int) -> bytes:
        buf = self._inner.read(size)
        if not buf:
            return b""
        self._n += len(buf)
        if self._n > self._max_bytes:
            raise UnsafeArchiveError(
                f"uncompressed size {self._n} exceeds cap {self._max_bytes}"
            )
        ratio = self._n / self._compressed
        if ratio > self._max_ratio:
            raise UnsafeArchiveError(
                f"compression ratio {ratio:.1f} exceeds cap {self._max_ratio}"
            )
        return buf


def _select_csv_member(
    zf: zipfile.ZipFile,
    *,
    max_uncompressed_bytes: int = DEFAULT_MAX_UNCOMPRESSED_BYTES,
    max_compression_ratio: float = DEFAULT_MAX_COMPRESSION_RATIO,
) -> zipfile.ZipInfo:
    infos = [info for info in zf.infolist() if not info.is_dir()]
    if len(infos) != 1:
        raise UnsafeArchiveError(f"expected a single file in archive, got {len(infos)}")
    info = infos[0]
    name = info.filename.replace("\\", "/")
    base = name.rsplit("/", 1)[-1]
    if not base or base in {".", ".."} or ".." in name.split("/"):
        raise UnsafeArchiveError(f"unsafe zip member name {info.filename!r}")
    if name.startswith("/") or (len(name) >= 2 and name[1] == ":"):
        raise UnsafeArchiveError(f"unsafe zip member name {info.filename!r}")
    if "/" in name:
        raise UnsafeArchiveError(f"refusing nested zip member {info.filename!r}")
    if not base.lower().endswith(".csv"):
        raise UnsafeArchiveError(f"expected a .csv member, got {info.filename!r}")
    uncompressed = int(info.file_size or 0)
    compressed = int(info.compress_size or 0)
    if uncompressed > max_uncompressed_bytes:
        raise UnsafeArchiveError(
            f"declared uncompressed size {uncompressed} exceeds cap {max_uncompressed_bytes}"
        )
    if compressed > 0 and uncompressed / compressed > max_compression_ratio:
        raise UnsafeArchiveError(
            f"declared compression ratio {uncompressed / compressed:.1f} exceeds cap {max_compression_ratio}"
        )
    return info


def _check_budget(*, used: int, extra: int, budget: int, label: str) -> None:
    if extra < 0:
        extra = 0
    if used + extra <= budget:
        return
    raise CacheBudgetExceeded(
        f"{label} budget exceeded: have {used} bytes, need {extra} more, cap is {budget} bytes "
        f"({budget / 1024**3:.2f} GiB). Raise {BUDGET_ENV}/{RUN_BUDGET_ENV} or pass "
        f"cache_budget_bytes/run_budget_bytes explicitly.",
        used=used,
        budget=budget,
        extra=extra,
    )


def download_daily_trades(
    client: PublicClient,
    inst_id: str,
    start: date | str,
    end: date | str,
    *,
    cache_dir: Path | str | None = None,
    cache_budget_bytes: int | None = None,
    run_budget_bytes: int | None = None,
    timeout: float = 120.0,
    streamer: Streamer | None = None,
    resume: bool = True,
    allow_unofficial_hosts: bool = False,
    max_uncompressed_bytes: int = DEFAULT_MAX_UNCOMPRESSED_BYTES,
    max_compression_ratio: float = DEFAULT_MAX_COMPRESSION_RATIO,
) -> DownloadResult:
    """Download daily trade zips into the cache. Resumable, size-capped.

    Existing valid zips are skipped. Partial ``.part`` files are discarded
    on any error, including ``KeyboardInterrupt``. Checksums: zip member
    limits plus ``sizeMB`` / ``Content-Length``. A sidecar ``.sha256`` is
    written after a good fetch. ``PublicClient.allow_unofficial_hosts`` does
    not apply to archive CDN URLs; pass ``allow_unofficial_hosts=True`` here.
    """
    endpoints.validate_inst_id(inst_id)
    root = Path(cache_dir) if cache_dir is not None else default_data_dir()
    root = root.expanduser().resolve()
    budget = default_budget_bytes() if cache_budget_bytes is None else cache_budget_bytes
    run_budget = default_run_budget_bytes() if run_budget_bytes is None else run_budget_bytes
    if budget <= 0 or run_budget <= 0:
        raise ValueError("budgets must be positive; pass a larger explicit cap to override")
    # Archive CDN checks are independent of PublicClient.allow_unofficial_hosts.
    unofficial = allow_unofficial_hosts
    files = list_daily_trade_files(client, inst_id, start, end)
    stream = streamer or RequestsStreamer()
    used = cache_usage_bytes(root)
    written = 0
    kept: list[Path] = []
    skipped: list[Path] = []
    for item in files:
        endpoints.assert_okx_url(item.url, allow_unofficial_hosts=unofficial)
        dest = cache_dest(root, inst_id, item.filename)
        dest.parent.mkdir(parents=True, exist_ok=True)
        meta_path = dest.with_suffix(dest.suffix + ".meta.json")
        hash_path = dest.with_suffix(dest.suffix + ".sha256")
        if resume and dest.exists() and _zip_ok(dest) and _within_size(dest, item.size_mb, None):
            skipped.append(dest)
            continue
        expected = _expected_bytes(item.size_mb, None)
        _check_budget(used=used, extra=expected or 0, budget=budget, label="total cache")
        _check_budget(used=written, extra=expected or 0, budget=run_budget, label="per-run")
        part = dest.with_suffix(dest.suffix + ".part")
        if part.exists():
            part.unlink()
        headers = {"User-Agent": "apollo-okx-public/0.1", "Accept": "*/*"}
        got = 0
        digest = hashlib.sha256()
        content_length: int | None = None
        try:
            response = stream(item.url, headers, timeout)
            final_url = (response.url or item.url).strip()
            endpoints.assert_okx_url(final_url, allow_unofficial_hosts=unofficial)
            if response.status_code != 200:
                raise OkxHttpError(response.status_code, item.url, "archive download failed")
            length_header = None
            for key, value in response.headers.items():
                if key.lower() == "content-length":
                    length_header = value
                    break
            content_length = int(length_header) if length_header and length_header.isdigit() else None
            expected = _expected_bytes(item.size_mb, length_header)
            _check_budget(used=used, extra=expected or 0, budget=budget, label="total cache")
            _check_budget(used=written, extra=expected or 0, budget=run_budget, label="per-run")
            with part.open("wb") as handle:
                for chunk in response.iter_bytes(CHUNK):
                    if not chunk:
                        continue
                    got += len(chunk)
                    if used + got > budget:
                        raise CacheBudgetExceeded(
                            f"total cache budget exceeded while writing {item.filename}",
                            used=used,
                            budget=budget,
                            extra=got,
                        )
                    if written + got > run_budget:
                        raise CacheBudgetExceeded(
                            f"per-run budget exceeded while writing {item.filename}",
                            used=written,
                            budget=run_budget,
                            extra=got,
                        )
                    handle.write(chunk)
                    digest.update(chunk)
            if not _zip_ok(part) or not _within_size(part, item.size_mb, content_length):
                raise OkxHttpError(200, item.url, f"archive failed size/CRC checks: {item.filename}")
            with zipfile.ZipFile(part) as zf:
                _select_csv_member(
                    zf,
                    max_uncompressed_bytes=max_uncompressed_bytes,
                    max_compression_ratio=max_compression_ratio,
                )
            part.replace(dest)
            hash_path.write_text(digest.hexdigest() + "\n", encoding="utf-8")
            meta_path.write_text(
                json.dumps(
                    {
                        "url": item.url,
                        "filename": item.filename,
                        "size_mb": item.size_mb,
                        "bytes": got,
                        "sha256": digest.hexdigest(),
                        "inst_id": inst_id,
                        "date": item.date,
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            used += got
            written += got
            kept.append(dest)
        finally:
            if part.exists():
                part.unlink()
    return DownloadResult(files=kept, skipped=skipped, bytes_written=written, cache_dir=root)


_HEADER_ALIASES = {
    "trade_id": "trade_id",
    "tradeid": "trade_id",
    "id": "trade_id",
    "side": "side",
    "size": "size",
    "sz": "size",
    "qty": "size",
    "quantity": "size",
    "price": "price",
    "px": "price",
    "timestamp_ms": "ts_ms",
    "timestamp": "ts_ms",
    "ts": "ts_ms",
    "created_time": "ts_ms",
    "time": "ts_ms",
}


def _header_map(row: list[str]) -> dict[str, int] | None:
    lowered = [cell.strip().lower().replace(" ", "_") for cell in row]
    if not any(name in _HEADER_ALIASES for name in lowered):
        return None
    mapping: dict[str, int] = {}
    for index, name in enumerate(lowered):
        key = _HEADER_ALIASES.get(name)
        if key:
            mapping[key] = index
    if {"trade_id", "price", "size", "side", "ts_ms"} <= mapping.keys():
        return mapping
    return None


def _parse_ts(value: str) -> int:
    text = value.strip()
    if text.isdigit():
        stamp = int(text)
        if stamp < 10**12:
            return stamp * 1000
        return stamp
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def parse_trade_zip(
    path: Path | str,
    *,
    inst_id: str = "",
    max_uncompressed_bytes: int = DEFAULT_MAX_UNCOMPRESSED_BYTES,
    max_compression_ratio: float = DEFAULT_MAX_COMPRESSION_RATIO,
) -> list[Trade]:
    """Parse a daily OKX trade zip. Milliseconds and trade ids are kept.

    Caps declared and observed uncompressed size and compression ratio.
    CSV is streamed row by row so a lying header cannot inflate the process.
    """
    if inst_id:
        endpoints.validate_inst_id(inst_id)
    zip_path = Path(path)
    on_disk = max(zip_path.stat().st_size, 1)
    trades: list[Trade] = []
    with zipfile.ZipFile(zip_path) as zf:
        info = _select_csv_member(
            zf,
            max_uncompressed_bytes=max_uncompressed_bytes,
            max_compression_ratio=max_compression_ratio,
        )
        compressed = max(int(info.compress_size or 0), on_disk)
        # ZipExtFile stops at ZipInfo.file_size and CRC-checks that prefix.
        # A lying-small header would never reach the stream cap; raise the
        # in-memory size past the cap so _BoundedReader sees the inflation.
        info.file_size = max(int(info.file_size or 0), max_uncompressed_bytes + 65536)
        raw = zf.open(info, "r")
        bounded = _BoundedReader(
            raw,
            max_bytes=max_uncompressed_bytes,
            max_ratio=max_compression_ratio,
            compressed_size=compressed,
        )
        try:
            text = io.TextIOWrapper(
                bounded,
                encoding="utf-8",
                errors="replace",
                newline="",
            )
            mapping: dict[str, int] | None = None
            for row in csv.reader(text):
                if not row or all(not cell.strip() for cell in row):
                    continue
                if mapping is None:
                    sniffed = _header_map(row)
                    if sniffed is not None:
                        mapping = sniffed
                        continue
                    mapping = {
                        "trade_id": 0,
                        "side": 1,
                        "size": 2,
                        "price": 3,
                        "ts_ms": 4,
                    }
                try:
                    trade_id = parse_trade_id(row[mapping["trade_id"]])
                    side = row[mapping["side"]].strip().lower()
                    size = float(row[mapping["size"]].strip())
                    price = float(row[mapping["price"]].strip())
                    ts_ms = _parse_ts(row[mapping["ts_ms"]])
                except (IndexError, ValueError):
                    continue
                trades.append(
                    Trade(
                        inst_id=inst_id,
                        trade_id=trade_id,
                        price=price,
                        size=size,
                        side=side,
                        ts_ms=ts_ms,
                    )
                )
        finally:
            raw.close()
    trades.sort(key=lambda t: (t.ts_ms, t.trade_id))
    return trades


def load_cached_trades(
    inst_id: str,
    start: date | str,
    end: date | str,
    *,
    cache_dir: Path | str | None = None,
) -> list[Trade]:
    """Read already-downloaded daily zips in ``[start, end]`` (inclusive)."""
    endpoints.validate_inst_id(inst_id)
    root = Path(cache_dir) if cache_dir is not None else default_data_dir()
    folder = Path(root) / "trades" / _inst_type(inst_id) / inst_id
    start_d = _parse_day(start)
    end_d = _parse_day(end)
    if not folder.exists():
        return []
    collected: list[Trade] = []
    for path in sorted(folder.glob("*.zip")):
        day = _filename_date(path.name, None)
        try:
            parsed = date.fromisoformat(day)
        except ValueError:
            continue
        if parsed < start_d or parsed > end_d:
            continue
        collected.extend(parse_trade_zip(path, inst_id=inst_id))
    collected.sort(key=lambda t: (t.ts_ms, t.trade_id))
    return collected
