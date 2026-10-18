from __future__ import annotations

import io
import struct
import zipfile
from pathlib import Path

import pytest

from tools.okx.archive import (
    DEFAULT_BUDGET_BYTES,
    StreamResponse,
    _BoundedReader,
    _select_csv_member,
    cache_dest,
    cache_usage_bytes,
    default_data_dir,
    download_daily_trades,
    list_daily_trade_files,
    load_cached_trades,
    parse_trade_zip,
    safe_archive_filename,
)
from tools.okx.endpoints import validate_inst_id
from tools.okx.errors import CacheBudgetExceeded, UnsafeArchiveError
from tools.okx.tests.conftest import FakeTransport, load_fixture, make_client
from tools.okx.types import parse_trade_id


def _zip_bytes(csv_text: str, inner_name: str = "BTC-USDT-SWAP-trades-2024-01-01.csv") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(inner_name, csv_text)
    return buffer.getvalue()


CSV = (
    "trade_id,side,size,price,timestamp_ms\n"
    "10,buy,0.01,42000.5,1704067200500\n"
    "11,sell,0.02,42001.0,1704067200500\n"
    "12,buy,0.03,42000.7,1704067202000\n"
)


def test_parse_headerful_and_headerless_csv(tmp_path: Path) -> None:
    headerful = tmp_path / "with-header.zip"
    headerful.write_bytes(_zip_bytes(CSV))
    trades = parse_trade_zip(headerful, inst_id="BTC-USDT-SWAP")
    assert [t.trade_id for t in trades] == [10, 11, 12]
    assert trades[0].ts_ms == 1704067200500
    assert trades[0].side == "buy"

    headerless = tmp_path / "no-header.zip"
    headerless.write_bytes(
        _zip_bytes(
            "10,buy,0.01,42000.5,1704067200500\n11,sell,0.02,42001.0,1704067201500\n",
            "t.csv",
        )
    )
    rows = parse_trade_zip(headerless, inst_id="BTC-USDT-SWAP")
    assert rows[1].trade_id == 11
    assert rows[1].ts_ms == 1704067201500


def test_list_daily_trade_files_uses_official_listing() -> None:
    transport = FakeTransport()
    transport.add("/api/v5/public/market-data-history", load_fixture("market_data_history.json"))
    client = make_client(transport)
    files = list_daily_trade_files(client, "BTC-USDT-SWAP", "2024-01-01", "2024-01-01")
    assert len(files) == 1
    assert files[0].filename == "BTC-USDT-SWAP-trades-2024-01-01.zip"
    assert files[0].url.startswith("https://static.okx.com/cdn/okex/traderecords/")
    params = transport.calls[0]["params"]
    assert params["module"] == "1"
    assert params["instType"] == "SWAP"
    assert params["instFamilyList"] == "BTC-USDT"
    assert params["dateAggrType"] == "daily"


def test_list_chunks_ten_day_windows() -> None:
    transport = FakeTransport()
    transport.add("/api/v5/public/market-data-history", {"code": "0", "data": []})
    client = make_client(transport)
    list_daily_trade_files(client, "BTC-USDT-SWAP", "2024-01-01", "2024-01-25")
    assert len(transport.calls) == 3
    begins = [int(call["params"]["begin"]) for call in transport.calls]
    assert begins[0] < begins[1] < begins[2]


def _streamer(payload: bytes, size_mb: str | None = None) -> object:
    def _call(url: str, headers: dict[str, str], timeout: float) -> StreamResponse:
        assert "static.okx.com" in url
        header = {"Content-Length": str(len(payload))}
        return StreamResponse(
            status_code=200,
            headers=header,
            iter_bytes=lambda _n: [payload],
            url=url,
        )

    return _call


def test_download_resume_and_parse(tmp_path: Path) -> None:
    payload = _zip_bytes(CSV)
    listing = load_fixture("market_data_history.json")
    listing["data"][0]["details"][0]["groupDetails"][0]["sizeMB"] = f"{len(payload) / 1_000_000:.8f}"
    transport = FakeTransport()
    transport.add("/api/v5/public/market-data-history", listing)
    client = make_client(transport)
    first = download_daily_trades(
        client,
        "BTC-USDT-SWAP",
        "2024-01-01",
        "2024-01-01",
        cache_dir=tmp_path,
        cache_budget_bytes=10_000_000,
        run_budget_bytes=10_000_000,
        streamer=_streamer(payload),
    )
    assert first.bytes_written == len(payload)
    assert first.files
    second = download_daily_trades(
        client,
        "BTC-USDT-SWAP",
        "2024-01-01",
        "2024-01-01",
        cache_dir=tmp_path,
        cache_budget_bytes=10_000_000,
        run_budget_bytes=10_000_000,
        streamer=_streamer(payload),
    )
    assert second.skipped
    assert second.bytes_written == 0
    trades = load_cached_trades("BTC-USDT-SWAP", "2024-01-01", "2024-01-01", cache_dir=tmp_path)
    assert [t.trade_id for t in trades] == [10, 11, 12]


def test_total_cache_budget_refuses_before_write(tmp_path: Path) -> None:
    payload = _zip_bytes(CSV)
    listing = load_fixture("market_data_history.json")
    listing["data"][0]["details"][0]["groupDetails"][0]["sizeMB"] = "5"
    transport = FakeTransport()
    transport.add("/api/v5/public/market-data-history", listing)
    client = make_client(transport)
    with pytest.raises(CacheBudgetExceeded, match="total cache budget"):
        download_daily_trades(
            client,
            "BTC-USDT-SWAP",
            "2024-01-01",
            "2024-01-01",
            cache_dir=tmp_path,
            cache_budget_bytes=100,
            run_budget_bytes=10_000_000,
            streamer=_streamer(payload),
        )
    assert list(tmp_path.rglob("*.zip")) == []
    assert list(tmp_path.rglob("*.part")) == []


def test_per_run_budget_stops_cleanly(tmp_path: Path) -> None:
    payload = _zip_bytes(CSV)
    listing = load_fixture("market_data_history.json")
    listing["data"][0]["details"][0]["groupDetails"][0]["sizeMB"] = f"{len(payload) / 1_000_000:.8f}"
    transport = FakeTransport()
    transport.add("/api/v5/public/market-data-history", listing)
    client = make_client(transport)
    with pytest.raises(CacheBudgetExceeded, match="per-run"):
        download_daily_trades(
            client,
            "BTC-USDT-SWAP",
            "2024-01-01",
            "2024-01-01",
            cache_dir=tmp_path,
            cache_budget_bytes=10_000_000,
            run_budget_bytes=10,
            streamer=_streamer(payload),
        )
    assert list(tmp_path.rglob("*.zip")) == []


def test_corrupt_zip_is_rejected(tmp_path: Path) -> None:
    listing = load_fixture("market_data_history.json")
    listing["data"][0]["details"][0]["groupDetails"][0]["sizeMB"] = "0.00001"
    transport = FakeTransport()
    transport.add("/api/v5/public/market-data-history", listing)
    client = make_client(transport)
    with pytest.raises(Exception):
        download_daily_trades(
            client,
            "BTC-USDT-SWAP",
            "2024-01-01",
            "2024-01-01",
            cache_dir=tmp_path,
            cache_budget_bytes=10_000_000,
            run_budget_bytes=10_000_000,
            streamer=_streamer(b"not a zip"),
        )


def test_refuses_non_cdn_host() -> None:
    listing = load_fixture("market_data_history.json")
    listing["data"][0]["details"][0]["groupDetails"][0]["url"] = "https://evil.example/t.zip"
    transport = FakeTransport()
    transport.add("/api/v5/public/market-data-history", listing)
    client = make_client(transport)
    with pytest.raises(ValueError, match="refusing host"):
        download_daily_trades(
            client,
            "BTC-USDT-SWAP",
            "2024-01-01",
            "2024-01-01",
            cache_dir=Path("/tmp/okx-test"),
            cache_budget_bytes=10_000_000,
            run_budget_bytes=10_000_000,
            streamer=_streamer(b"x"),
        )


def test_default_data_dir_is_outside_repo(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("OKX_DATA_DIR", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    path = default_data_dir()
    assert path == (tmp_path / "xdg" / "apollo" / "okx").resolve()
    monkeypatch.setenv("OKX_DATA_DIR", str(tmp_path / "custom"))
    assert default_data_dir() == (tmp_path / "custom").resolve()
    assert DEFAULT_BUDGET_BYTES == 4 * 1024**3
    leftover = tmp_path / "empty"
    leftover.mkdir()
    assert cache_usage_bytes(leftover) == 0


def _listing(filename: str, size_mb: str = "0.0002", url: str | None = None) -> dict:
    payload = load_fixture("market_data_history.json")
    row = payload["data"][0]["details"][0]["groupDetails"][0]
    row["filename"] = filename
    row["sizeMB"] = size_mb
    if url is not None:
        row["url"] = url
    return payload


def test_path_traversal_filename_rejected(tmp_path: Path) -> None:
    """``dest_root / '../../escaped.zip'`` used to write outside dest_root."""
    transport = FakeTransport()
    transport.add("/api/v5/public/market-data-history", _listing("../../escaped.zip"))
    client = make_client(transport)
    dest_root = tmp_path / "trades" / "SWAP" / "BTC-USDT-SWAP"
    dest_root.mkdir(parents=True)
    escaped = (dest_root / "../../escaped.zip").resolve()
    assert escaped == (tmp_path / "trades" / "escaped.zip").resolve()
    with pytest.raises(ValueError, match="filename"):
        download_daily_trades(
            client,
            "BTC-USDT-SWAP",
            "2024-01-01",
            "2024-01-01",
            cache_dir=tmp_path,
            cache_budget_bytes=10_000_000,
            run_budget_bytes=10_000_000,
            streamer=_streamer(_zip_bytes(CSV)),
        )
    assert not escaped.exists()
    with pytest.raises(ValueError):
        safe_archive_filename("../../escaped.zip")
    with pytest.raises(ValueError):
        safe_archive_filename("/tmp/foo.zip")
    with pytest.raises(ValueError):
        safe_archive_filename("")


def test_inst_id_rejects_traversal(tmp_path: Path) -> None:
    transport = FakeTransport()
    transport.add("/api/v5/public/market-data-history", load_fixture("market_data_history.json"))
    client = make_client(transport)
    with pytest.raises(ValueError, match="inst_id"):
        download_daily_trades(
            client,
            "../ETC-USDT-SWAP",
            "2024-01-01",
            "2024-01-01",
            cache_dir=tmp_path,
            cache_budget_bytes=10_000_000,
            run_budget_bytes=10_000_000,
            streamer=_streamer(b"x"),
        )
    with pytest.raises(ValueError, match="inst_id"):
        list_daily_trade_files(client, "btc-usdt-swap", "2024-01-01", "2024-01-01")
    dest = cache_dest(tmp_path, "BTC-USDT-SWAP", "BTC-USDT-SWAP-trades-2024-01-01.zip")
    assert dest.is_relative_to(tmp_path.resolve())
    assert dest.name == "BTC-USDT-SWAP-trades-2024-01-01.zip"


def test_part_files_count_toward_cache_usage(tmp_path: Path) -> None:
    part = tmp_path / "BTC-USDT-SWAP-trades-2024-01-01.zip.part"
    part.write_bytes(b"hello")
    assert cache_usage_bytes(tmp_path) == 5


def test_part_deleted_on_streamer_error(tmp_path: Path) -> None:
    payload = _zip_bytes(CSV)
    listing = _listing("BTC-USDT-SWAP-trades-2024-01-01.zip", f"{len(payload) / 1_000_000:.8f}")
    transport = FakeTransport()
    transport.add("/api/v5/public/market-data-history", listing)
    client = make_client(transport)

    def exploding(url: str, headers: dict[str, str], timeout: float) -> StreamResponse:
        def chunks(_n: int):
            yield b"PK"
            raise RuntimeError("network dropped")

        return StreamResponse(status_code=200, headers={}, iter_bytes=chunks, url=url)

    with pytest.raises(RuntimeError, match="network dropped"):
        download_daily_trades(
            client,
            "BTC-USDT-SWAP",
            "2024-01-01",
            "2024-01-01",
            cache_dir=tmp_path,
            cache_budget_bytes=10_000_000,
            run_budget_bytes=10_000_000,
            streamer=exploding,
        )
    assert list(tmp_path.rglob("*.part")) == []
    assert list(tmp_path.rglob("*.zip")) == []


def test_wrong_listing_size_no_content_length_aborts_in_stream(tmp_path: Path) -> None:
    payload = _zip_bytes(CSV)
    assert len(payload) > 80
    listing = _listing("BTC-USDT-SWAP-trades-2024-01-01.zip", "0")
    transport = FakeTransport()
    transport.add("/api/v5/public/market-data-history", listing)
    client = make_client(transport)

    def chunked(url: str, headers: dict[str, str], timeout: float) -> StreamResponse:
        def chunks(_n: int):
            for i in range(0, len(payload), 8):
                yield payload[i : i + 8]

        return StreamResponse(status_code=200, headers={}, iter_bytes=chunks, url=url)

    with pytest.raises(CacheBudgetExceeded, match="per-run"):
        download_daily_trades(
            client,
            "BTC-USDT-SWAP",
            "2024-01-01",
            "2024-01-01",
            cache_dir=tmp_path,
            cache_budget_bytes=10_000_000,
            run_budget_bytes=80,
            streamer=chunked,
        )
    assert list(tmp_path.rglob("*.part")) == []
    assert list(tmp_path.rglob("*.zip")) == []


def test_zip_bomb_rejected_by_header_and_stream(tmp_path: Path) -> None:
    bloated = "trade_id,side,size,price,timestamp_ms\n" + ("1,buy,1,1,1700000000000\n" * 8000)
    path = tmp_path / "bomb.zip"
    path.write_bytes(_zip_bytes(bloated, "t.csv"))
    with pytest.raises(UnsafeArchiveError):
        parse_trade_zip(
            path,
            inst_id="BTC-USDT-SWAP",
            max_uncompressed_bytes=2048,
            max_compression_ratio=3,
        )
    inner = io.BytesIO(b"x" * 500)
    reader = _BoundedReader(inner, max_bytes=10_000, max_ratio=4, compressed_size=10)
    with pytest.raises(UnsafeArchiveError, match="ratio"):
        while reader.read(50):
            pass


def test_multi_member_zip_rejected(tmp_path: Path) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("a.csv", CSV)
        zf.writestr("b.csv", CSV)
    path = tmp_path / "multi.zip"
    path.write_bytes(buf.getvalue())
    with pytest.raises(UnsafeArchiveError, match="single file"):
        parse_trade_zip(path, inst_id="BTC-USDT-SWAP")


def test_trade_ids_use_int_not_float(tmp_path: Path) -> None:
    big = "9007199254740993"
    assert int(float(big)) != int(big)
    csv_text = (
        "trade_id,side,size,price,timestamp_ms\n"
        f"{big},buy,0.01,42000.5,1704067200500\n"
        "1e6,sell,0.02,42001.0,1704067200600\n"
        "12,buy,0.03,42000.7,1704067202000\n"
    )
    path = tmp_path / "ids.zip"
    path.write_bytes(_zip_bytes(csv_text, "t.csv"))
    trades = parse_trade_zip(path, inst_id="BTC-USDT-SWAP")
    assert [t.trade_id for t in trades] == [int(big), 12]
    assert parse_trade_id(big) == int(big)
    with pytest.raises(ValueError):
        parse_trade_id("1e6")
    with pytest.raises(ValueError):
        parse_trade_id("1.0")


def test_inst_id_accepts_live_okx_symbols(tmp_path: Path) -> None:
    real_ids = [
        "BTC-USDT",
        "SPCX-USDT-SWAP",
        "BTC-USD_UM-261030",
        "BTC-USD-261030",
        "BTC-USD-261030-60000-C",
    ]
    for inst_id in real_ids:
        assert validate_inst_id(inst_id) == inst_id
        dest = cache_dest(tmp_path, inst_id, f"{inst_id}-trades-2024-01-01.zip")
        assert dest.is_relative_to(tmp_path.resolve())
        assert dest.name == f"{inst_id}-trades-2024-01-01.zip"
    for bad in ["../x", "a/b", "A.B", ""]:
        with pytest.raises(ValueError, match="inst_id"):
            validate_inst_id(bad)


def test_symlinked_cache_subdir_cannot_escape(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "trades").symlink_to(outside)
    payload = _zip_bytes(CSV)
    listing = _listing(
        "BTC-USDT-SWAP-trades-2024-01-01.zip",
        f"{len(payload) / 1_000_000:.8f}",
    )
    transport = FakeTransport()
    transport.add("/api/v5/public/market-data-history", listing)
    client = make_client(transport)
    with pytest.raises(ValueError, match="outside cache"):
        download_daily_trades(
            client,
            "BTC-USDT-SWAP",
            "2024-01-01",
            "2024-01-01",
            cache_dir=cache,
            cache_budget_bytes=10_000_000,
            run_budget_bytes=10_000_000,
            streamer=_streamer(payload),
        )
    assert list(outside.rglob("*")) == []
    assert list(cache.rglob("*.zip")) == []


def _with_lying_uncompressed_size(payload: bytes, fake_size: int) -> bytes:
    buf = bytearray(payload)
    if buf[:4] != b"PK\x03\x04":
        raise AssertionError("expected local file header")
    struct.pack_into("<I", buf, 22, fake_size)
    cen = buf.rfind(b"PK\x01\x02")
    if cen < 0:
        raise AssertionError("expected central directory")
    struct.pack_into("<I", buf, cen + 24, fake_size)
    return bytes(buf)


def test_declared_uncompressed_size_header_check_is_enforced(tmp_path: Path) -> None:
    """Fails if ``_select_csv_member`` drops the declared-size check.

    The payload is tiny, so the stream inflator would not trip. Mutation
    testing showed that check was otherwise untested.
    """
    import inspect

    from tools.okx import archive as archive_mod

    path = tmp_path / "small.zip"
    path.write_bytes(_zip_bytes(CSV, "t.csv"))
    with zipfile.ZipFile(path) as zf:
        info = zf.infolist()[0]
        assert info.file_size < 4096
        info.file_size = 10_000_000
        with pytest.raises(UnsafeArchiveError, match="declared uncompressed size"):
            _select_csv_member(zf, max_uncompressed_bytes=4096)
    source = inspect.getsource(archive_mod._select_csv_member)
    assert "declared uncompressed size" in source
    assert "uncompressed > max_uncompressed_bytes" in source


def test_lying_zip_header_stream_cap_aborts(tmp_path: Path) -> None:
    bloated = "trade_id,side,size,price,timestamp_ms\n" + ("1,buy,1,1,1700000000000\n" * 4000)
    honest = _zip_bytes(bloated, "t.csv")
    lying = _with_lying_uncompressed_size(honest, fake_size=200)
    path = tmp_path / "lie.zip"
    path.write_bytes(lying)
    with zipfile.ZipFile(path) as zf:
        declared = zf.infolist()[0].file_size
    assert declared == 200
    with pytest.raises(UnsafeArchiveError, match="uncompressed size"):
        parse_trade_zip(
            path,
            inst_id="BTC-USDT-SWAP",
            max_uncompressed_bytes=4096,
            max_compression_ratio=100,
        )


def test_part_deleted_on_keyboard_interrupt(tmp_path: Path) -> None:
    listing = _listing("BTC-USDT-SWAP-trades-2024-01-01.zip", "0.0002")
    transport = FakeTransport()
    transport.add("/api/v5/public/market-data-history", listing)
    client = make_client(transport)

    def interrupting(url: str, headers: dict[str, str], timeout: float) -> StreamResponse:
        def chunks(_n: int):
            yield b"PK"
            raise KeyboardInterrupt()

        return StreamResponse(status_code=200, headers={}, iter_bytes=chunks, url=url)

    with pytest.raises(KeyboardInterrupt):
        download_daily_trades(
            client,
            "BTC-USDT-SWAP",
            "2024-01-01",
            "2024-01-01",
            cache_dir=tmp_path,
            cache_budget_bytes=10_000_000,
            run_budget_bytes=10_000_000,
            streamer=interrupting,
        )
    assert list(tmp_path.rglob("*.part")) == []
    assert list(tmp_path.rglob("*.zip")) == []


def test_redirect_to_unofficial_host_is_rejected(tmp_path: Path) -> None:
    payload = _zip_bytes(CSV)
    listing = _listing(
        "BTC-USDT-SWAP-trades-2024-01-01.zip",
        f"{len(payload) / 1_000_000:.8f}",
    )
    transport = FakeTransport()
    transport.add("/api/v5/public/market-data-history", listing)
    client = make_client(transport)

    def bounced(url: str, headers: dict[str, str], timeout: float) -> StreamResponse:
        return StreamResponse(
            status_code=200,
            headers={"Content-Length": str(len(payload))},
            iter_bytes=lambda _n: [payload],
            url="https://evil.example/t.zip",
        )

    with pytest.raises(ValueError, match="refusing host"):
        download_daily_trades(
            client,
            "BTC-USDT-SWAP",
            "2024-01-01",
            "2024-01-01",
            cache_dir=tmp_path,
            cache_budget_bytes=10_000_000,
            run_budget_bytes=10_000_000,
            streamer=bounced,
        )
    assert list(tmp_path.rglob("*.zip")) == []
    assert list(tmp_path.rglob("*.part")) == []


def test_client_unofficial_hosts_does_not_apply_to_archive(tmp_path: Path) -> None:
    listing = _listing(
        "BTC-USDT-SWAP-trades-2024-01-01.zip",
        url="https://127.0.0.1/t.zip",
    )
    transport = FakeTransport()
    transport.add("/api/v5/public/market-data-history", listing)
    client = make_client(
        transport,
        allow_unofficial_hosts=True,
        base_url="https://127.0.0.1:9",
    )
    with pytest.raises(ValueError, match="refusing host"):
        download_daily_trades(
            client,
            "BTC-USDT-SWAP",
            "2024-01-01",
            "2024-01-01",
            cache_dir=tmp_path,
            cache_budget_bytes=10_000_000,
            run_budget_bytes=10_000_000,
            streamer=_streamer(b"x"),
        )


def test_http_and_unofficial_hosts_rejected(tmp_path: Path) -> None:
    from tools.okx.client import PublicClient

    with pytest.raises(ValueError, match="non-https"):
        PublicClient(base_url="http://www.okx.com")
    with pytest.raises(ValueError, match="refusing host"):
        PublicClient(base_url="https://evil.example")
    allowed = PublicClient(
        base_url="https://127.0.0.1:9",
        allow_unofficial_hosts=True,
        transport=FakeTransport(),
    )
    assert allowed.base_url.startswith("https://127.0.0.1")
    listing = _listing(
        "BTC-USDT-SWAP-trades-2024-01-01.zip",
        url="http://static.okx.com/cdn/okex/traderecords/x.zip",
    )
    transport = FakeTransport()
    transport.add("/api/v5/public/market-data-history", listing)
    client = make_client(transport)
    with pytest.raises(ValueError, match="non-https"):
        download_daily_trades(
            client,
            "BTC-USDT-SWAP",
            "2024-01-01",
            "2024-01-01",
            cache_dir=tmp_path,
            cache_budget_bytes=10_000_000,
            run_budget_bytes=10_000_000,
            streamer=_streamer(b"x"),
        )
