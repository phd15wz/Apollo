"""Rates and DXY market-state scaffold.

Pure transforms, the FRED availability clock, and the outside-git cache
layout are implemented. FRED and Yahoo HTTP are TODOs. The locked spec is
``docs/macro-rates-dxy.md``.
"""

from tools.macro.availability import (
    as_new_york,
    dxy_available_at,
    fred_available_at,
    fred_scheduled_open,
    is_fred_pull_time,
    is_nyse_session,
    next_nyse_session,
    nyse_full_closure_dates,
    select_visible_indices,
)
from tools.macro.cache import (
    DATA_DIR_ENV,
    CachedPrint,
    assert_outside_repo,
    default_data_dir,
    load_series,
    save_series,
    upsert_print,
)
from tools.macro.fred import (
    FRED_API_KEY_ENV,
    FRED_OBSERVATIONS_URL,
    MissingFredApiKey,
    fetch_observations,
    fred_api_key,
    fred_query_params,
)
from tools.macro.series import (
    ALL_SERIES,
    DXY_TICKER,
    FRED_SERIES,
    LIVE_HARD_GATE_SERIES,
    RESEARCH_SERIES,
    SERIES_ROLE,
    SHARP_PRIOR_MAX,
    SHARP_PRIOR_MIN,
    SHARP_SIGMA,
    SPREAD_TOLERANCE_PCT,
)
from tools.macro.transforms import (
    ObservationChanges,
    SharpMove,
    SpreadMismatch,
    assert_published_spread,
    live_hard_gate,
    observation_changes,
    sharp_move,
)
from tools.macro.yahoo import fetch_dxy_history

__all__ = [
    "ALL_SERIES",
    "DATA_DIR_ENV",
    "DXY_TICKER",
    "FRED_API_KEY_ENV",
    "FRED_OBSERVATIONS_URL",
    "FRED_SERIES",
    "LIVE_HARD_GATE_SERIES",
    "RESEARCH_SERIES",
    "SERIES_ROLE",
    "SHARP_PRIOR_MAX",
    "SHARP_PRIOR_MIN",
    "SHARP_SIGMA",
    "SPREAD_TOLERANCE_PCT",
    "CachedPrint",
    "MissingFredApiKey",
    "ObservationChanges",
    "SharpMove",
    "SpreadMismatch",
    "as_new_york",
    "assert_outside_repo",
    "assert_published_spread",
    "default_data_dir",
    "dxy_available_at",
    "fetch_dxy_history",
    "fetch_observations",
    "fred_api_key",
    "fred_available_at",
    "fred_query_params",
    "fred_scheduled_open",
    "is_fred_pull_time",
    "is_nyse_session",
    "live_hard_gate",
    "load_series",
    "next_nyse_session",
    "nyse_full_closure_dates",
    "observation_changes",
    "save_series",
    "select_visible_indices",
    "sharp_move",
    "upsert_print",
]
