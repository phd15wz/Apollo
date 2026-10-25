# Rates and DXY puller

Market-state feed for US Treasury yields and a research dollar index. The locked rules are in [`docs/macro-rates-dxy.md`](../../docs/macro-rates-dxy.md).

`fred.fetch_observations` GETs the FRED observations endpoint. `yahoo.fetch_dxy_history` downloads daily `DX-Y.NYB` closes through `yfinance` (imported inside the download helper). `refresh_once` merges them into the cache, checks the published 2s10s spread, and returns the live gate. Tests inject those clients and do not open a socket.

Requires Python 3.11+. Runtime deps already in the repo: `numpy`, `pandas` (only so closures go through `load_confirmed()`). No Numba.

## Run

From the repository root:

```bash
python3 -m pip install -r requirements.txt
python3 -m pytest tools/macro/tests -q
```

A live refresh needs `FRED_API_KEY` in the environment (from the secret store) and a cache outside the repo. `--backfill` is the manual run outside the 16:15 America/New_York window. It does not move `available_at` backward. `--observation-start YYYY-MM-DD` bounds the FRED window (default `1992-01-01`). It does not bound `DX-Y.NYB`.

```bash
FRED_API_KEY=... python3 -m tools.macro.refresh --cache-dir "$MACRO_DATA_DIR"
```

Exit 0 means the cache was updated. A single observation date outside the 1 bp spread band is skipped and logged; it still exits 0 and writes the other rows. Exit 2 means the key is missing or the cache path is inside the repo. Any other failure, including a closed pull window, exits 1. The pytest suite does not call FRED or Yahoo.

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

`assert_published_spread` raises when `|T10Y2Y - (DGS10 - DGS2)|` is more than 1 basis point (0.01 in percent units) on a date where all three levels exist. The band is not widened. On refresh, that date is skipped and logged (date, `DGS10` / `DGS2` / `T10Y2Y` levels, observed published spread, expected `DGS10 - DGS2`, tolerance) and the other rows are still written. `DFII10` and `DX-Y.NYB` stay. The recomputed spread is not stored.

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

## Refresh

`refresh_once` pulls `DGS2`, `DGS10`, `T10Y2Y`, and `DFII10` from `observation_start` (default 1992-01-01), then `DX-Y.NYB` on full Yahoo history. It drops observation dates that miss the 1 bp published-spread band, including dates already cached, logs each one, and writes the remaining rows. `upsert_print` keeps the first `available_at` when a level is replaced. Flags at `now` use only rows already visible. `live_hard_gate` reads `DGS10` and `DFII10`. Research flags are on the summary and do not open the gate.

`FRED_API_KEY` comes from the environment. A missing key raises `MissingFredApiKey`. FRED errors do not include the query string.
