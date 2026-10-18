"""Live smoke tests. Skipped unless OKX_LIVE=1."""

from __future__ import annotations

import os

import pytest

from tools.okx.client import PublicClient
from tools.okx.status import upcoming_maintenance

pytestmark = [
    pytest.mark.okx_live,
    pytest.mark.skipif(os.environ.get("OKX_LIVE") != "1", reason="live OKX HTTP skipped by default; set OKX_LIVE=1"),
]


def test_live_ticker_and_status() -> None:
    client = PublicClient(timeout=15.0, max_retries=2)
    ticker = client.ticker("BTC-USDT-SWAP")
    assert ticker.inst_id == "BTC-USDT-SWAP"
    assert ticker.ts_ms > 0
    windows = upcoming_maintenance(client)
    assert isinstance(windows, list)
