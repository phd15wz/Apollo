from __future__ import annotations

import logging
import pickle

import pytest

from tools.okx.auth import OkxCredentials, access_headers, load_credentials, redact_secrets, sign
from tools.okx.errors import LiveTradingDisabled, MissingCredentials

pytestmark = pytest.mark.usefixtures("clean_okx_env")

_TS = "2020-12-08T09:08:57.715Z"
_GET_SIGN = "Yt2R9ATAX6Jz6+ud53rCYenVK09+CEpafaWGadqhybE="
_POST_BODY = '{"instId":"BTC-USDT-SWAP","ordType":"limit","px":"60000","side":"buy","sz":"1","tdMode":"cross"}'
_POST_SIGN = "Jdf+JbmfeRAdVIlIw5+3ulieVquVb5Gakq+wNSGitQI="


def test_sign_matches_known_vectors() -> None:
    get_sig = sign("test-secret", _TS, "GET", "/api/v5/account/balance?ccy=USDT", "")
    assert get_sig == _GET_SIGN
    post_sig = sign("test-secret", _TS, "post", "/api/v5/trade/order", _POST_BODY)
    assert post_sig == _POST_SIGN


def test_access_headers_demo_and_live_read() -> None:
    creds = OkxCredentials("test-key", "test-secret", "test-passphrase")
    headers = access_headers(
        creds,
        timestamp=_TS,
        method="GET",
        request_path="/api/v5/account/balance?ccy=USDT",
        body="",
        demo=True,
    )
    assert headers["OK-ACCESS-KEY"] == "test-key"
    assert headers["OK-ACCESS-SIGN"] == _GET_SIGN
    assert headers["OK-ACCESS-TIMESTAMP"] == _TS
    assert headers["OK-ACCESS-PASSPHRASE"] == "test-passphrase"
    assert headers["x-simulated-trading"] == "1"
    live = access_headers(
        creds,
        timestamp=_TS,
        method="GET",
        request_path="/api/v5/account/balance?ccy=USDT",
        demo=False,
    )
    assert live["OK-ACCESS-SIGN"] == _GET_SIGN
    assert "x-simulated-trading" not in live
    with pytest.raises(LiveTradingDisabled):
        access_headers(
            creds,
            timestamp=_TS,
            method="POST",
            request_path="/api/v5/trade/order",
            body=_POST_BODY,
            demo=False,
        )


def test_credentials_repr_and_pickle_hide_secrets() -> None:
    creds = OkxCredentials("test-key", "test-secret", "test-passphrase")
    assert "test-secret" not in repr(creds)
    assert "test-passphrase" not in str(creds)
    with pytest.raises(TypeError):
        pickle.dumps(creds)


def test_load_credentials_from_env_and_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OKX_KEY", "alias-key")
    monkeypatch.setenv("OKX_SECRET", "alias-secret")
    monkeypatch.setenv("OKX_PASSPHRASE", "alias-pass")
    creds = load_credentials()
    assert creds.api_key == "alias-key"
    assert creds.api_secret == "alias-secret"
    assert creds.passphrase == "alias-pass"

    monkeypatch.setenv("OKX_API_KEY", "primary-key")
    monkeypatch.setenv("OKX_API_SECRET", "primary-secret")
    monkeypatch.setenv("OKX_API_PASSPHRASE", "primary-pass")
    primary = load_credentials()
    assert primary.api_key == "primary-key"
    assert primary.api_secret == "primary-secret"
    assert primary.passphrase == "primary-pass"


def test_missing_credentials_do_not_echo_a_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OKX_API_SECRET", "super-secret-value")
    with pytest.raises(MissingCredentials) as exc:
        load_credentials()
    message = str(exc.value)
    assert "OKX_API_KEY" in message
    assert "OKX_API_PASSPHRASE" in message
    assert "super-secret-value" not in message


def test_redactor_scrubs_http_debug_logs() -> None:
    secret = "super-secret-value"
    redact_secrets(secret)
    logger = logging.getLogger("urllib3.connectionpool")
    records: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record.getMessage())

    handler = _Capture(level=logging.DEBUG)
    logger.addHandler(handler)
    previous = logger.level
    logger.setLevel(logging.DEBUG)
    try:
        logger.debug("sending passphrase header %s", secret)
    finally:
        logger.setLevel(previous)
        logger.removeHandler(handler)
    assert records
    assert secret not in records[0]
    assert "***" in records[0]
