"""Demo private REST smoke. Skipped unless OKX_DEMO=1 and a key are set.

The call is a read of the demo account. It does not place or cancel.
"""

from __future__ import annotations

import os

import pytest

from tools.okx.private import PrivateClient

pytestmark = [
    pytest.mark.okx_demo,
    pytest.mark.skipif(
        os.environ.get("OKX_DEMO") != "1" or not os.environ.get("OKX_API_KEY"),
        reason="demo OKX private REST skipped unless OKX_DEMO=1 and OKX_API_KEY are set",
    ),
]


def test_demo_balance_is_read_only() -> None:
    client = PrivateClient(read_only=True, demo=True)
    assert client.allow_orders is False
    assert client.demo is True
    balance = client.balance()
    assert balance.total_eq_usd >= 0
    assert isinstance(balance.details, tuple)
