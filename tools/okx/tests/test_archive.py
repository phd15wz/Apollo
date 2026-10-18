from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from tools.okx.archive import (
    DEFAULT_BUDGET_BYTES,
    cache_usage_bytes,
    default_data_dir,
    download_daily_trades,
    list_daily_trade_files,
    load_cached_trades,
    parse_trade_zip,
)
from tools.okx.errors import CacheBudgetExceeded
from tools.okx.tests.conftest import FakeTransport, load_fixture, make_client
from tools.okx.archive import StreamResponse


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
    with pytest.raises(ValueError, match="refusing download host"):
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
    assert cache_usage_bytes(tmp_path) == 0
