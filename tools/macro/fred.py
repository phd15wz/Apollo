"""FRED observations client.

The API key is ``FRED_API_KEY`` in the environment, supplied from the secret
store for the process. It is never hardcoded, logged, or written next to the
cache. Query parameters contain the key, so errors from this module omit the
URL and the response body.

Observations endpoint (keyless description):
https://fred.stlouisfed.org/docs/api/fred/series_observations.html
"""

from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime
from typing import AbstractSet, Callable, Mapping
from zoneinfo import ZoneInfo

from tools.macro.availability import fred_available_at
from tools.macro.cache import CachedPrint
from tools.macro.series import FRED_SERIES, FRED_SOURCE

FRED_API_KEY_ENV = "FRED_API_KEY"
FRED_OBSERVATIONS_URL = "https://api.stlouisfed.org/fred/series/observations"
FRED_TIMEOUT_SECONDS = 30.0

NY = ZoneInfo("America/New_York")

# ``(url, params, timeout) -> (status, body)``. Params contain the API key.
# Callers must not log them.
FredGet = Callable[[str, Mapping[str, str], float], tuple[int, bytes]]


class MissingFredApiKey(RuntimeError):
    """``FRED_API_KEY`` is unset or blank.

    Export it from the secret store into the process environment. Do not
    commit it, do not put it in a tracked ``.env``, and do not paste it into
    the repository.
    """

    def __init__(self) -> None:
        super().__init__(
            "FRED_API_KEY is not set. Export it from the secret store for this "
            "process. Do not commit the key."
        )


class FredHttpError(RuntimeError):
    """FRED responded with a non-200 status. The message has no URL and no body."""

    def __init__(self, status: int) -> None:
        self.status = status
        super().__init__(f"FRED observations request failed with HTTP {status}")


class FredResponseError(RuntimeError):
    """The observations payload could not be read. The message has no body."""


def fred_api_key(environ: Mapping[str, str] | None = None) -> str:
    """Read ``FRED_API_KEY``. Raise :class:`MissingFredApiKey` when it is missing."""
    source = os.environ if environ is None else environ
    key = str(source.get(FRED_API_KEY_ENV, "")).strip()
    if not key:
        raise MissingFredApiKey()
    return key


def _require_fred_series(series_id: str) -> None:
    if series_id not in FRED_SERIES:
        known = ", ".join(FRED_SERIES)
        raise ValueError(f"{series_id!r} is not a locked FRED series ({known})")


def _as_date_bound(value: date | None, name: str) -> date | None:
    if value is None:
        return None
    # datetime is a date subclass; a timestamp is not an observation bound.
    if isinstance(value, datetime) or not isinstance(value, date):
        raise TypeError(f"{name} must be a datetime.date")
    return value


def fred_query_params(
    series_id: str,
    key: str,
    *,
    observation_start: date | None = None,
    observation_end: date | None = None,
) -> dict[str, str]:
    """Query string for the observations endpoint. Does not perform I/O.

    Callers must not log the returned mapping: it contains the API key.
    """
    _require_fred_series(series_id)
    stripped = key.strip()
    if not stripped:
        raise MissingFredApiKey()
    start = _as_date_bound(observation_start, "observation_start")
    end = _as_date_bound(observation_end, "observation_end")
    params = {
        "series_id": series_id,
        "api_key": stripped,
        "file_type": "json",
    }
    if start is not None:
        params["observation_start"] = start.isoformat()
    if end is not None:
        params["observation_end"] = end.isoformat()
    return params


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """A redirect would resend the query string. Refuse it."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        raise FredHttpError(int(code))


def _http_get(url: str, params: Mapping[str, str], timeout: float) -> tuple[int, bytes]:
    """GET the locked observations URL. Exceptions omit the URL and the body."""
    if url != FRED_OBSERVATIONS_URL:
        raise FredResponseError("FRED observations URL is not the locked endpoint")
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(f"{url}?{query}", method="GET")
    opener = urllib.request.build_opener(_RefuseRedirects)
    try:
        with opener.open(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 200))
            body = response.read()
    except FredHttpError:
        raise
    except urllib.error.HTTPError as exc:
        raise FredHttpError(int(exc.code)) from None
    except urllib.error.URLError:
        raise FredResponseError("FRED observations request failed") from None
    if not isinstance(body, bytes):
        raise FredResponseError("FRED observations response was not bytes")
    return status, body


def _fetch_clock(fetched_at: datetime | None) -> datetime:
    clock = fetched_at if fetched_at is not None else datetime.now(NY)
    if not isinstance(clock, datetime) or clock.tzinfo is None or clock.tzinfo.utcoffset(clock) is None:
        raise ValueError("fetched_at must be a timezone-aware datetime")
    return clock


def _parse_observations(
    series_id: str,
    body: bytes,
    fetched_at: datetime,
    closures: AbstractSet[date] | None,
) -> list[CachedPrint]:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise FredResponseError("FRED observations response was not JSON") from None
    rows = payload.get("observations") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise FredResponseError("FRED observations response has no observations list")
    by_date: dict[date, CachedPrint] = {}
    for item in rows:
        if not isinstance(item, dict):
            raise FredResponseError("FRED observation was not an object")
        raw_date = item.get("date")
        raw_value = item.get("value")
        if not isinstance(raw_date, str) or not isinstance(raw_value, str):
            raise FredResponseError(f"FRED observation for {series_id} is missing date or value")
        if raw_value == ".":
            continue
        try:
            obs_date = date.fromisoformat(raw_date)
            level = float(raw_value)
        except ValueError:
            raise FredResponseError(f"FRED observation for {series_id} could not be parsed") from None
        if not math.isfinite(level):
            continue
        by_date[obs_date] = CachedPrint(
            series_id=series_id,
            obs_date=obs_date,
            level=level,
            available_at=fred_available_at(obs_date, fetched_at, closures=closures),
            source=FRED_SOURCE,
            ticker=None,
        )
    return [by_date[key] for key in sorted(by_date)]


def fetch_observations(
    series_id: str,
    *,
    observation_start: date | None = None,
    observation_end: date | None = None,
    key: str | None = None,
    environ: Mapping[str, str] | None = None,
    get: FredGet | None = None,
    fetched_at: datetime | None = None,
    timeout: float = FRED_TIMEOUT_SECONDS,
    closures: AbstractSet[date] | None = None,
) -> list[CachedPrint]:
    """GET one locked FRED series and return cache prints.

    Missing FRED values (``"."``) are dropped. ``level`` is the published
    percent. ``available_at`` comes from :func:`tools.macro.availability.fred_available_at`
    and ``fetched_at`` (default: now in America/New_York). ``get`` replaces
    the HTTP call in tests; it receives the query params and must not log them.
    """
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    resolved = fred_api_key(environ) if key is None else key
    params = fred_query_params(
        series_id,
        resolved,
        observation_start=observation_start,
        observation_end=observation_end,
    )
    clock = _fetch_clock(fetched_at)
    getter = get if get is not None else _http_get
    status, body = getter(FRED_OBSERVATIONS_URL, params, timeout)
    if status != 200:
        raise FredHttpError(status)
    return _parse_observations(series_id, body, clock, closures)
