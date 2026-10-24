# data
Shared datasets, knowledge bases, and cached resources.

Include ingestion scripts, data schemas, and small sample data.

- `exchange_calendar/` — versioned holiday and early-close rows. Schema and refresh live in `tools/exchange_calendar/`.
- Rates and DXY market-state feed: spec in `docs/macro-rates-dxy.md`. Cache is `$MACRO_DATA_DIR` or `~/.cache/apollo/macro` (sibling of the default OKX cache). Keep the FRED API key and any Yahoo dump out of this tree.
- OKX bulk trade zips are **not** stored here. They go to `OKX_DATA_DIR` (default `~/.cache/apollo/okx`) via `tools/okx`. Never commit them.
