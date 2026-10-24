"""FRED and Yahoo clients with injected responses. No live HTTP."""

from __future__ import annotations

import ast
import json
import sys
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from tools.macro import fred as fred_mod
from tools.macro.fred import (
    FRED_OBSERVATIONS_URL,
    FRED_TIMEOUT_SECONDS,
    FredHttpError,
    FredResponseError,
    MissingFredApiKey,
    _http_get,
    fetch_observations,
)
from tools.macro.yahoo import fetch_dxy_history

NY = ZoneInfo("America/New_York")
FAKE_KEY = "test-fred-key"
FETCHED = datetime(2025, 11, 26, 17, 0, tzinfo=NY)
CLOSURES = {date(2025, 11, 27)}


def _body(rows: list[dict[str, str]]) -> bytes:
    return json.dumps({"observations": rows}).encode("utf-8")


def test_fetch_drops_dots_and_stamps_the_next_open() -> None:
    seen: dict[str, object] = {}

    def getter(url: str, params: dict[str, str], timeout: float) -> tuple[int, bytes]:
        seen["url"] = url
        seen["params"] = dict(params)
        seen["timeout"] = timeout
        return 200, _body(
            [
                {"date": "2025-11-26", "value": "4.25"},
                {"date": "2025-11-27", "value": "."},
                {"date": "2025-11-28", "value": "4.5"},
            ]
        )

    rows = fetch_observations(
        "DGS10",
        key=FAKE_KEY,
        observation_start=date(2025, 11, 1),
        get=getter,
        fetched_at=FETCHED,
        closures=CLOSURES,
    )
    assert seen["url"] == FRED_OBSERVATIONS_URL
    assert seen["timeout"] == FRED_TIMEOUT_SECONDS
    params = seen["params"]
    assert isinstance(params, dict)
    assert params["series_id"] == "DGS10"
    assert params["file_type"] == "json"
    assert params["observation_start"] == "2025-11-01"
    assert params["api_key"] == FAKE_KEY
    assert [row.obs_date for row in rows] == [date(2025, 11, 26), date(2025, 11, 28)]
    assert rows[0].level == 4.25
    assert rows[1].level == 4.5
    assert rows[0].source == "fred"
    assert rows[0].ticker is None
    assert rows[0].available_at == datetime(2025, 11, 28, 9, 30, tzinfo=NY)
    assert rows[1].available_at == datetime(2025, 12, 1, 9, 30, tzinfo=NY)


def test_late_fetch_keeps_the_fetch_clock() -> None:
    late = datetime(2025, 11, 28, 10, 0, tzinfo=NY)

    def getter(url: str, params: dict[str, str], timeout: float) -> tuple[int, bytes]:
        return 200, _body([{"date": "2025-11-26", "value": "4.25"}])

    rows = fetch_observations(
        "DGS10",
        key=FAKE_KEY,
        get=getter,
        fetched_at=late,
        closures=CLOSURES,
    )
    assert rows[0].available_at == late


def test_missing_key_and_unknown_series_do_not_call_http() -> None:
    def getter(url: str, params: dict[str, str], timeout: float) -> tuple[int, bytes]:
        raise AssertionError("HTTP should not run")

    with pytest.raises(MissingFredApiKey):
        fetch_observations("DGS10", environ={}, get=getter, fetched_at=FETCHED)
    with pytest.raises(ValueError, match="not a locked FRED series"):
        fetch_observations("UNRATE", key=FAKE_KEY, get=getter, fetched_at=FETCHED)
    with pytest.raises(ValueError, match="DX-Y.NYB"):
        fetch_observations("DX-Y.NYB", key=FAKE_KEY, get=getter, fetched_at=FETCHED)


def test_http_and_html_errors_omit_the_key_and_the_body() -> None:
    def denied(url: str, params: dict[str, str], timeout: float) -> tuple[int, bytes]:
        raw = FAKE_KEY.encode("utf-8")
        return 400, b'{"error_message":"bad","api_key":"' + raw + b'"}'

    with pytest.raises(FredHttpError, match="HTTP 400") as exc:
        fetch_observations("DFII10", key=FAKE_KEY, get=denied, fetched_at=FETCHED)
    assert FAKE_KEY not in str(exc.value)
    assert "bad" not in str(exc.value)

    def html(url: str, params: dict[str, str], timeout: float) -> tuple[int, bytes]:
        return 200, b"<html><body>quote page " + FAKE_KEY.encode("utf-8") + b"</body></html>"

    with pytest.raises(FredResponseError, match="not JSON") as parsed:
        fetch_observations("DGS2", key=FAKE_KEY, get=html, fetched_at=FETCHED)
    assert FAKE_KEY not in str(parsed.value)
    assert "html" not in str(parsed.value).lower()
    assert "quote page" not in str(parsed.value)


def test_default_getter_hides_the_request_url(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeOpener:
        def open(self, request: urllib.request.Request, timeout: float | None = None):
            assert timeout == FRED_TIMEOUT_SECONDS
            assert FAKE_KEY in request.full_url
            raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", None, None)

    monkeypatch.setattr(fred_mod.urllib.request, "build_opener", lambda *args, **kwargs: FakeOpener())
    with pytest.raises(FredHttpError, match="HTTP 403") as exc:
        _http_get(
            FRED_OBSERVATIONS_URL,
            {"series_id": "DGS10", "api_key": FAKE_KEY, "file_type": "json"},
            FRED_TIMEOUT_SECONDS,
        )
    assert FAKE_KEY not in str(exc.value)
    assert "stlouisfed" not in str(exc.value)


def test_dxy_bar_close_is_new_york_and_skips_missing_closes() -> None:
    sys.modules.pop("yfinance", None)
    index = pd.to_datetime(["2025-11-26 21:00:00+00:00", "2025-11-28 21:00:00+00:00"])
    frame = pd.DataFrame({"Close": [104.2, float("nan")]}, index=index)
    rows = fetch_dxy_history(history=lambda: frame)
    assert "yfinance" not in sys.modules
    assert len(rows) == 1
    assert rows[0].series_id == "DX-Y.NYB"
    assert rows[0].ticker == "DX-Y.NYB"
    assert rows[0].source == "yahoo"
    assert rows[0].level == 104.2
    assert rows[0].obs_date == date(2025, 11, 26)
    assert rows[0].available_at == datetime(2025, 11, 26, 16, 0, tzinfo=NY)


def test_naive_yahoo_timestamp_is_read_as_new_york() -> None:
    index = pd.to_datetime(["2025-11-26 16:00:00"])
    frame = pd.DataFrame({"Close": [101.0]}, index=index)
    rows = fetch_dxy_history(history=lambda: frame)
    assert rows[0].available_at == datetime(2025, 11, 26, 16, 0, tzinfo=NY)


def test_yahoo_frame_without_close_raises() -> None:
    frame = pd.DataFrame({"Open": [1.0]})
    with pytest.raises(ValueError, match="Close"):
        fetch_dxy_history(history=lambda: frame)


def test_yfinance_import_lives_inside_the_download_helper() -> None:
    path = Path(__file__).resolve().parents[1] / "yahoo.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    helper = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_yahoo_daily_frame"
    )
    imported = [
        alias.name
        for node in ast.walk(helper)
        if isinstance(node, ast.Import)
        for alias in node.names
    ]
    assert "yfinance" in imported
