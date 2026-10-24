# Rates and DXY puller (scaffold)

Market-state feed for US Treasury yields and a research dollar index. The locked rules are in [`docs/macro-rates-dxy.md`](../../docs/macro-rates-dxy.md). This package is the scaffold Isaac fills. It does not call FRED or Yahoo.

Pure transforms, the FRED `available_at` clock, and the cache layout are implemented. Live HTTP is a TODO in `fred.py`, `yahoo.py`, and `refresh.py`.

Requires Python 3.11+. Runtime deps already in the repo: `numpy`, `pandas` (only so closures go through `load_confirmed()`). No Numba.

## Run

From the repository root:

```bash
python3 -m pip install -r requirements.txt
python3 -m pytest tools/macro/tests -q
```

The refresh command checks the key and the cache path, then stops. It does not open a socket:

```bash
FRED_API_KEY=... python3 -m tools.macro.refresh --cache-dir "$MACRO_DATA_DIR"
```

Exit 2 means the key is missing or the cache path is inside the repo. Exit 3 means the HTTP refresh is still unimplemented.

## Series

| Id | Role |
| --- | --- |
| `DGS2` | Research. FRED. |
| `DGS10` | Live hard gate. FRED. |
| `T10Y2Y` | Research. Published FRED spread. Not replaced with `DGS10 - DGS2`. |
| `DFII10` | Live hard gate. FRED. |
| `DX-Y.NYB` | Research. Yahoo ticker, locked. |

`live_hard_gate` is true only when `DGS10` or `DFII10` is sharp on a row already visible. Research flags never open it. The half-size long cap stays in the strategy; this package only returns the flag.

## Transforms

`observation_changes` counts available prints. A NaN level is a missing print and does not take a slot in the 1-day or 5-day window. Pass rows in `obs_date` order, already limited to `available_at <=` the decision time (`select_visible_indices`).

`sharp_move` takes signed 1-day changes. σ is the population standard deviation (divide by N) of the preceding changes: at most the last 60, and only when at least 40 exist. The current change is not in its own window. Both signs count. `|change| > 2σ` is a sharp move; equal to 2σ is not. Fewer than 40 priors sets `short_sample` and leaves the flag false.

`assert_published_spread` stops the refresh when `|T10Y2Y - (DGS10 - DGS2)|` is more than 1 basis point (0.01 in percent units) on a date where all three levels exist. It does not write the recomputed spread.

```python
from tools.macro import (
    assert_published_spread,
    live_hard_gate,
    observation_changes,
    select_visible_indices,
    sharp_move,
)

visible = select_visible_indices(available_at, decision_at)
changes = observation_changes(levels[visible])
flags = sharp_move(changes.change_1d)
gate = live_hard_gate({"DGS10": bool(flags.sharp_move[-1])})
assert_published_spread(obs_dates, dgs10, dgs2, t10y2y)
```

## Clock

NYSE business days are weekdays minus confirmed full closures from `load_confirmed()` (`official` and `secondary-confirmed` only). Early closes still count. Weekends and full closures are not pull days and are not the next session.

FRED: pull on a session at or after 16:15 America/New_York. For `obs_date` T, `fred_available_at` is 09:30 America/New_York on the next NYSE session. A fetch after that open keeps the fetch time.

DXY: `dxy_available_at` is the Yahoo bar close in America/New_York.

## Cache

`$MACRO_DATA_DIR`, or `~/.cache/apollo/macro` when that variable is unset. `XDG_CACHE_HOME` is not used. `save_series` refuses a directory inside this repository. One JSON file per series under `series/`. `upsert_print` may replace `level` and must keep the original `available_at`.

Do not commit cache files, the FRED key, or Yahoo HTML.

## What Isaac fills

1. `fred.fetch_observations` — `GET https://api.stlouisfed.org/fred/series/observations` with `fred_query_params`. Drop FRED `"."` values. Do not log the params; they contain the key.
2. `yahoo.fetch_dxy_history` — `yfinance` ticker `DX-Y.NYB` only. Store the close and the ticker.
3. `refresh.refresh_once` — the steps are in that function's docstring. Wire the transforms, the spread check, the vintage merge, and the live gate.

`FRED_API_KEY` comes from the environment, which the secret store exports for the process. A missing key raises `MissingFredApiKey`.
