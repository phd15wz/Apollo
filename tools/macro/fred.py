"""FRED client scaffold.

The API key is ``FRED_API_KEY`` in the environment, supplied from the secret
store for the process. It is never hardcoded, logged, or written next to the
cache. HTTP itself is a TODO for Isaac.

Observations endpoint (keyless description):
https://fred.stlouisfed.org/docs/api/fred/series_observations.html
"""

from __future__ import annotations

import os
from datetime import date, datetime
from typing import Mapping, NoReturn

from tools.macro.series import FRED_SERIES

FRED_API_KEY_ENV = "FRED_API_KEY"
FRED_OBSERVATIONS_URL = "https://api.stlouisfed.org/fred/series/observations"


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


def fetch_observations(
    series_id: str,
    *,
    observation_start: date | None = None,
    observation_end: date | None = None,
    key: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> NoReturn:
    """Pull one locked FRED series. The HTTP body is not implemented.

    TODO(Isaac): ``GET`` ``FRED_OBSERVATIONS_URL`` with
    :func:`fred_query_params`. Use a timeout. Parse ``observations``. FRED
    missing values are ``"."``; drop those rows. Map ``date`` to ``obs_date``
    and ``value`` to ``level`` (percent, as published). Stamp ``available_at``
    with :func:`tools.macro.availability.fred_available_at` and the fetch
    clock. Do not log the query params. Do not write the response into the
    repository.
    """
    resolved = fred_api_key(environ) if key is None else key
    params = fred_query_params(
        series_id,
        resolved,
        observation_start=observation_start,
        observation_end=observation_end,
    )
    # ``params`` is built so a missing key fails before this TODO. The error
    # text deliberately omits it.
    series = params["series_id"]
    raise NotImplementedError(
        f"FRED HTTP fetch for {series} is not implemented ({FRED_OBSERVATIONS_URL}). "
        "See docs/macro-rates-dxy.md."
    )
