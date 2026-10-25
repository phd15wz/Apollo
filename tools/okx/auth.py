"""OKX v5 REST signing. Credentials come from the environment only.

Never log the key, the secret, the passphrase, or the signature. This module
does not read files, CLI arguments, or the repository.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
from dataclasses import dataclass

from tools.okx.errors import LiveTradingDisabled, MissingCredentials

# Primary name, then accepted aliases. The first non-empty value wins.
_KEY_VARS = ("OKX_API_KEY", "OKX_KEY")
_SECRET_VARS = ("OKX_API_SECRET", "OKX_SECRET_KEY", "OKX_SECRET")
_PASSPHRASE_VARS = ("OKX_API_PASSPHRASE", "OKX_PASSPHRASE")

_REDACT_LOGGERS = ("urllib3", "urllib3.connectionpool", "requests")
_MIN_REDACT_LEN = 4


@dataclass(frozen=True, slots=True)
class OkxCredentials:
    """In-memory API credentials. ``repr`` hides every field."""

    api_key: str
    api_secret: str
    passphrase: str

    def __repr__(self) -> str:
        return "OkxCredentials(redacted)"

    def __str__(self) -> str:
        return "OkxCredentials(redacted)"

    def __getstate__(self) -> dict[str, str]:
        raise TypeError("OkxCredentials cannot be pickled")


class SecretRedactor(logging.Filter):
    """Replace credential material in log records before handlers see them."""

    def __init__(self) -> None:
        super().__init__()
        self._secrets: list[str] = []

    def add(self, value: str) -> None:
        if not value or len(value) < _MIN_REDACT_LEN:
            return
        if value not in self._secrets:
            self._secrets.append(value)

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            rendered = record.getMessage()
        except Exception:
            return True
        redacted = rendered
        for secret in self._secrets:
            if secret in redacted:
                redacted = redacted.replace(secret, "***")
        if redacted != rendered:
            record.msg = redacted
            record.args = ()
        return True


_REDACTOR = SecretRedactor()
_REDACTOR_INSTALLED = False


def redact_secrets(*values: str) -> None:
    """Install a process-wide filter on HTTP debug loggers and remember ``values``."""
    global _REDACTOR_INSTALLED
    for value in values:
        _REDACTOR.add(value)
    if _REDACTOR_INSTALLED:
        return
    for name in _REDACT_LOGGERS:
        logging.getLogger(name).addFilter(_REDACTOR)
    _REDACTOR_INSTALLED = True


def _first_env(names: tuple[str, ...]) -> str:
    for name in names:
        raw = os.environ.get(name)
        if raw is not None and raw.strip() != "":
            return raw.strip()
    return ""


def load_credentials() -> OkxCredentials:
    """Load credentials from the environment. The error names variables only."""
    api_key = _first_env(_KEY_VARS)
    api_secret = _first_env(_SECRET_VARS)
    passphrase = _first_env(_PASSPHRASE_VARS)
    missing: list[str] = []
    if not api_key:
        missing.append(_KEY_VARS[0])
    if not api_secret:
        missing.append(_SECRET_VARS[0])
    if not passphrase:
        missing.append(_PASSPHRASE_VARS[0])
    if missing:
        raise MissingCredentials(missing)
    creds = OkxCredentials(api_key=api_key, api_secret=api_secret, passphrase=passphrase)
    redact_secrets(api_key, api_secret, passphrase)
    return creds


def sign(secret: str, timestamp: str, method: str, request_path: str, body: str = "") -> str:
    """Base64 HMAC-SHA256 of ``timestamp + METHOD + requestPath + body``."""
    if not secret:
        raise ValueError("API secret is empty")
    message = f"{timestamp}{method.upper()}{request_path}{body}"
    digest = hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def access_headers(
    credentials: OkxCredentials,
    *,
    timestamp: str,
    method: str,
    request_path: str,
    body: str = "",
    demo: bool,
) -> dict[str, str]:
    """Build OK-ACCESS-* headers.

    Demo requests set ``x-simulated-trading: 1``. Live read-only requests omit
    that header so a live Read key is not rejected as demo. Live writes are
    not signed.
    """
    if not demo and method.upper() != "GET":
        raise LiveTradingDisabled(
            "refusing to sign a live write; Live Trade is not enabled"
        )
    signature = sign(credentials.api_secret, timestamp, method, request_path, body)
    redact_secrets(credentials.api_key, credentials.api_secret, credentials.passphrase, signature)
    headers = {
        "OK-ACCESS-KEY": credentials.api_key,
        "OK-ACCESS-SIGN": signature,
        "OK-ACCESS-TIMESTAMP": timestamp,
        "OK-ACCESS-PASSPHRASE": credentials.passphrase,
    }
    if demo:
        headers["x-simulated-trading"] = "1"
    return headers
