# data
Shared datasets, knowledge bases, and cached resources.

Include ingestion scripts, data schemas, and small sample data.

- `exchange_calendar/` — versioned holiday and early-close rows. Schema and refresh live in `tools/exchange_calendar/`.
- OKX bulk trade zips are **not** stored here. They go to `OKX_DATA_DIR` (default `~/.cache/apollo/okx`) via `tools/okx`. Never commit them.
