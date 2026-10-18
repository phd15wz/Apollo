# OKX public market data

Use `tools/okx/` for **keyless** OKX v5 market data, bulk trade history, and
exchange maintenance windows. Python 3.11+, `requests` + `numpy`. Do not
import OKX's Agent Trade Kit. Do not add API keys.

## When to use

- Public instruments, tickers, books, trades, candles, funding, open interest,
  mark/index price.
- Historical ticks for Hawkes / order-flow research (`BTC-USDT-SWAP` and
  similar).
- Isaac's maintenance reminders: upcoming OKX windows in UTC.

Skip this skill for account balances, positions, orders, withdrawals, or
anything that can trade.

## Safety stance

1. **Stage 1 (this package):** public endpoints only. No keys, no signing, no
   placeholders for keys, no order or withdrawal URLs.
2. **Stage 2 (not implemented):** read-only keys, if ever added, come only
   from a secret store the owner controls. Never from the repo, chat, or env
   files committed to git.
3. **Orders:** later, behind demo trading and the owner's explicit approval.
   This library must not grow a live order path without that.

If a task needs a private endpoint, stop and tell the owner. Do not work
around the allowlist.

## How to call it

```python
from tools.okx import (
    PublicClient,
    candles_range,
    download_daily_trades,
    handle_ties,
    history_trades_range,
    upcoming_maintenance,
)

client = PublicClient(site="global")  # or "eea" / "us"
ticker = client.ticker("BTC-USDT-SWAP")
trades = history_trades_range(
    client, "BTC-USDT-SWAP", start_ts_ms=start, end_ts_ms=end, pagination="ts"
)
```

REST history-trades is about three months. For longer SWAP tapes use the
official zip archive (below).

Maintenance:

```python
windows = upcoming_maintenance(client)
for w in windows:
    print(w.title, w.begin_utc, w.end_utc, w.services)
```

Announcements (`/api/v5/support/announcements` and announcement types) are
keyless; use them as context, not as a trading signal.

## Rate-limit etiquette

Public REST limits are **per IP**. This client already paces documented
limits and retries 429 / `50011` / 5xx with backoff. Still:

- Prefer one process. Do not fan out many agents against the same IP.
- Use `history-candles` / `history-trades` pagination helpers instead of tight
  loops you write yourself.
- For multi-day tapes, use `download_daily_trades` rather than hammering
  history-trades.
- Default timeouts are short; do not disable retries and then hammer.

## Bulk archive, cache, size cap

Official listing (verified): `GET /api/v5/public/market-data-history`
([docs](https://www.okx.com/docs-v5/en/#public-data-rest-api-get-historical-market-data)),
product page [okx.com/historical-data](https://www.okx.com/historical-data).
OKX returns CDN URLs (`static.okx.com`) and `sizeMB`. Do not invent other
hosts.

BTC-USDT-SWAP trades can be several GB per month. **Never commit downloads.**

- One cache directory: `OKX_DATA_DIR` (default `~/.cache/apollo/okx`, outside
  the repo). All zips go there.
- Total cache budget and per-run budget default to **4 GiB**
  (`OKX_DATA_BUDGET_BYTES`, `OKX_DATA_RUN_BUDGET_BYTES`). Exceeding either
  raises `CacheBudgetExceeded` and deletes the partial file. Override only
  with an explicit argument or env var.
- Resume: valid cached zips are skipped. CRC + size checks are required.

```python
result = download_daily_trades(
    client, "BTC-USDT-SWAP", "2024-01-01", "2024-01-02",
    cache_budget_bytes=4 * 1024**3,
)
```

## Same-timestamp handling (Hawkes)

Keep millisecond timestamps. Preserve exchange `trade_id` order. A simple
Hawkes process cannot have ties.

1. `report_ties(trades)` — counts collisions; does not change the tape.
2. `handle_ties(trades)` — default: stable `(ts_ms, trade_id)` sort only.
3. `handle_ties(trades, method="aggregate_by_timestamp_side")` — one event per
   millisecond and aggressor side (preferred before fitting).

Do **not** add random jitter. Jitter on coarse stamps biases α and β. This
library will not jitter even if asked; say no and point here.

After a clean tape, fit with `skills/hawkes-process/SKILL.md`.

## Tests

```bash
python3 -m pytest tools/okx/tests -q
```

Network is off unless `OKX_LIVE=1`.
