# tools

Custom tools, plugins, and CLI helpers shared by the owner and agents.

Add a short note on how to install and run each tool. This repository is public, so keep credentials out of this directory.

- `exchange_calendar` — Build and refresh `data/exchange_calendar`. See `tools/exchange_calendar/README.md`. No API keys.
- Rates and DXY puller: spec at `docs/macro-rates-dxy.md` (`FRED_API_KEY` in the secret store; cache `$MACRO_DATA_DIR` or `~/.cache/apollo/macro`). This directory has no macro client yet.
- `hawkes` — Simulate and fit linear Hawkes processes (exponential and power-law kernels). See `tools/hawkes/README.md`. Requires `numpy` and `scipy`.
- `macro` — Scaffold for the rates and DXY feed (`docs/macro-rates-dxy.md`). Pure transforms, the FRED `available_at` clock, and the outside-git cache layout are implemented. Live FRED and Yahoo HTTP are TODOs for Isaac. See `tools/macro/README.md`. Cache: `MACRO_DATA_DIR` or `~/.cache/apollo/macro`. `FRED_API_KEY` stays in the secret store.
- `okx` — Stage-1 keyless OKX v5 public market data (REST, public WebSocket, official historical-trade zips, maintenance windows). See `tools/okx/README.md`. No API keys; cannot trade. Bulk downloads and optional stream recordings go to `OKX_DATA_DIR` (default `~/.cache/apollo/okx`) under a 4 GiB cache cap. WebSocket extra: `websockets`.
