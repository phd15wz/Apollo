# OKX public market data (stage 1)

Keyless Python client for OKX v5 **public** REST data. It cannot trade, cannot
withdraw, and has no API-key or signing path. Stage 2 (read-only keys from a
secret store) and orders (demo mode, explicit owner approval) are out of
scope.

Docs: [OKX API v5](https://www.okx.com/docs-v5/en/). This package is written
from those public endpoints. It does not vendor [OKX Agent Trade Kit](https://github.com/okx/agent-trade-kit).

Requires Python 3.11+. Runtime deps already in the repo: `requests`, `numpy`.
No Numba. Pandas is not used.

## Install / run

From the repository root:

```bash
python3 -m pip install -r requirements.txt
python3 -m pytest tools/okx/tests -q
```

Live HTTP is skipped unless you opt in:

```bash
OKX_LIVE=1 python3 -m pytest tools/okx/tests -q -m okx_live
```

## REST client

Base URL is `https://www.okx.com` (`site="global"`). EU is `eea.okx.com`,
US/AU is `us.okx.com`. Only `https` URLs on documented OKX hosts are accepted.
JSON bodies with `code != "0"` raise. Timeouts default to 10s. 429, 5xx, and
OKX `50011` retry with exponential backoff. A client-side limiter honours the
documented per-endpoint IP limits (for example tickers 20/2s, books 40/2s,
history-trades 10/2s, system status 1/5s). **The limiter is per `PublicClient`
instance.** Two processes or two client objects sharing an IP can still exceed
OKX's per-IP caps; prefer one client per process.

```python
from tools.okx import PublicClient, candles_range, history_trades_range, upcoming_maintenance

client = PublicClient()  # site="global" | "eea" | "us"
ticker = client.ticker("BTC-USDT-SWAP")
book = client.order_book("BTC-USDT-SWAP", depth=25)
trades = history_trades_range(client, "BTC-USDT-SWAP", start_ts_ms=..., end_ts_ms=...)
candles = candles_range(client, "BTC-USDT-SWAP", start_ts_ms=..., end_ts_ms=..., bar="1m")
```

Market methods: instruments (`SPOT` / `SWAP` / `FUTURES`), ticker(s), order
book, recent trades, history trades (default: page by exchange trade id),
candles / history candles, funding rate and history, open interest, mark
price, index tickers. Results are dataclasses; trades and candles also convert
to NumPy structured arrays (`trades_to_array`, `TRADE_DTYPE`) with integer
millisecond timestamps.

`history_trades_range(..., pagination="ts")` is an explicit, **lossy** option:
OKX `after` is exclusive on timestamp, so same-millisecond prints that sit on
a page boundary are dropped. Timestamp mode never sends `before`. Prefer the
default trade-id pager.

History-trades on REST only covers about three months. Older ticks come from
the bulk archive below.

## Bulk historical trades

Official source (verified, not guessed):

- Product page: <https://www.okx.com/historical-data>
- Keyless listing: `GET /api/v5/public/market-data-history` (`module=1` =
  tick-by-tick trades), documented at
  [Get historical market data](https://www.okx.com/docs-v5/en/#public-data-rest-api-get-historical-market-data)
- Each row includes a `url` on `static.okx.com` and a `sizeMB`. Daily listing
  windows are at most 10 days (OKX changelog).
- Terms: <https://www.okx.com/en-us/help/historicaldata-terms-and-conditions>
  (personal use; do not redistribute).

BTC-USDT-SWAP trade history can be several GB per month. Downloads **must
not** be committed.

```python
from tools.okx import download_daily_trades, parse_trade_zip

result = download_daily_trades(
    client, "BTC-USDT-SWAP", "2024-01-01", "2024-01-03"
)
```

### Cache directory (one setting)

All bulk files go to a single directory:

| Setting | Default |
| --- | --- |
| `OKX_DATA_DIR` | `~/.cache/apollo/okx` (or `$XDG_CACHE_HOME/apollo/okx`) |

That path is outside the repo. Do not point it at `data/` or `tools/okx/`.
`.gitignore` already ignores in-repo accidents (`.okx-data/`, `okx-data/`,
`data/okx/`, `tools/okx/data/`, `*.okx.zip`).

### Size cap

| Setting | Default | Meaning |
| --- | --- | --- |
| `OKX_DATA_BUDGET_BYTES` | 4 GiB | Total cache size |
| `OKX_DATA_RUN_BUDGET_BYTES` | 4 GiB | Bytes written in one `download_daily_trades` call |

Either cap stops the run with `CacheBudgetExceeded` (partial `.part` files
are deleted). Override explicitly with `cache_budget_bytes=` /
`run_budget_bytes=` on the call, or by setting the env vars. Existing valid
zips are skipped (resume). Each file is CRC-checked as a zip and compared to
`sizeMB` / `Content-Length`; a `.sha256` sidecar is stored after a good fetch.

## Same-timestamp trades (Hawkes)

Matching engines batch at 1 ms. Keep millisecond timestamps and exchange
`trade_id` order. Do not add random jitter (it biases Hawkes α/β).

```python
from tools.okx import handle_ties, report_ties

report = report_ties(trades)
ordered, report = handle_ties(trades)  # report only; still sorted by (ts, trade_id)
collapsed, report = handle_ties(trades, method="aggregate_by_timestamp_side")
```

`stable_within_ms` keeps every print. `aggregate_by_timestamp_side` makes one
event per `(ts_ms, side)` (size summed, price size-weighted).

## Exchange status (Isaac)

`GET /api/v5/system/status` is public. `upcoming_maintenance(client)` returns
scheduled/ongoing windows with UTC `begin_utc` / `end_utc` and affected
services.

`GET /api/v5/support/announcements` and `GET /api/v5/support/announcement-types`
are also listed as public/keyless in the v5 docs and the public REST catalog.

## Skipped in stage 1

Public WebSocket (`wss://ws.okx.com:8443/ws/v5/public`) with reconnect/ping
was optional. It is not shipped half-done. REST + the official zip archive
cover research and maintenance reminders.

## Safety

- Allowlisted GET paths only. Account, trade, asset, and withdrawal URLs raise
  `UnsafeEndpointError`.
- No `api_key` constructor argument, no `OK-ACCESS-*` headers, no HMAC.
- Unit tests mock HTTP with recorded JSON under `tests/fixtures/`.
