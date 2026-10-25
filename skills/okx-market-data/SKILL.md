# OKX public market data

Use `tools/okx/` for **keyless** OKX v5 market data, the public WebSocket,
bulk trade history, and exchange maintenance windows. Python 3.11+,
`requests` + `numpy`. The stream client needs optional `websockets`. Do not
import OKX's Agent Trade Kit. Do not put API keys on the public client or
in the repository.

## When to use

- Public instruments, tickers, books, trades, candles, funding, open interest,
  mark/index price, including the public WebSocket for live trades / books.
- Historical ticks for Hawkes / order-flow research (`BTC-USDT-SWAP` and
  similar).
- Isaac's maintenance reminders: upcoming OKX windows in UTC.

Use this skill for public market data. Account balances, positions, funding
balances, and orders are `PrivateClient` in `tools/okx/README.md` (stage 2).
Live mode reads balance, positions, and `GET /api/v5/asset/balances`
(`demo=False`, or CLI with `OKX_DEMO` unset). Demo writes need a go flag.
The only live writes are the BTC-USDT cash place-then-cancel smoke and one
BTC-USDT-SWAP place-then-cancel smoke (`tdMode` `isolated`, the strategy
default), and only with `OKX_LIVE_TRADE=1` plus that go flag. There are no
withdrawal methods and no leverage changes. Do not use it as a market-data
source. Do not widen the live smokes or add a withdrawal path.

## Safety stance

1. **Stage 1 (public client):** public endpoints only. No keys, no signing,
   no order or withdrawal URLs on `PublicClient` or the public WebSocket.
2. **Stage 2 (demo private client):** keys come from the environment
   (`OKX_API_KEY`, `OKX_API_SECRET`, `OKX_API_PASSPHRASE` or the aliases in
   the README). Never from the repo, chat, or committed env files. Writes
   need `OKX_ALLOW_ORDERS=1` or `allow_orders=True`, stay on demo
   (`x-simulated-trading: 1`), and refuse withdraw / transfer / deposit URLs.
   Live balance, positions, and funding balances omit that header.
   `OKX_ALLOW_ORDERS` does not by itself make a write live. The only asset
   route is `GET /api/v5/asset/balances`.
3. **Live Trade smokes** are opt-in. `OKX_LIVE_TRADE=1` (or `live_trade=True`)
   plus the order go flag allows two resting limit buys, each cancelled on
   that same client: BTC-USDT cash, size `0.001`, px at most `40000`; and
   BTC-USDT-SWAP, size `0.01`, `tdMode` `isolated` only, px at most `40000`.
   `cross` is not the live SWAP shape. Amend, other instruments, other
   sizes, leverage changes, and asset mutations stay refused.
   `LIVE_TRADE_ENABLED` remains a source tripwire, not the switch. Live
   reads do not flip it. Do not widen this path.

If a task needs an endpoint outside the public allowlist and outside the
stage-2 allowlist (trade, account, and `GET /api/v5/asset/balances`), stop
and tell the owner. Do not work around either allowlist.

## How to call it

```python
from tools.okx import (
    OkxPublicStream,
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
    client, "BTC-USDT-SWAP", start_ts_ms=start, end_ts_ms=end
)  # locates the newest trade at or before the end, then pages by trade id; pagination="ts" is lossy
```

REST history-trades is about three months. Windows older than that raise
`HistoryWindowTooOld` — use the official zip archive (below), not an empty
list. `PaginationBudgetExceeded.trades` keeps any rows already fetched, including
when the call had no start time.

Live public stream (optional `websockets` package):

```python
async with OkxPublicStream(
    [("trades", "SPCX-USDT-SWAP"), ("books5", "SPCX-USDT-SWAP")],
    rest=client,
) as stream:
    async for msg in stream:
        if msg.kind == "gap" or stream.is_stale(msg.channel, msg.inst_id):
            continue  # hole in the tape; never a quiet Hawkes period
```

```bash
python3 -m tools.okx.stream_demo --inst SPCX-USDT-SWAP --channels trades,books5 --seconds 60
# 8443 blocked: set OKX_WS_PUBLIC_URL=wss://ws.okx.com:443/ws/v5/public
# or --public-url (wss + official hosts only; env cannot enable unofficial hosts)
```

Recording is **off by default**. `--record` writes gzip JSONL under
`OKX_DATA_DIR/recordings/` (daily rotation by default; hourly optional).
`--record-channels trades` records a subset. The same cache/run budgets
apply. `OKX_MIN_FREE_DISK_BYTES` (default 10 GiB) stops recording. Full
`books` records periodic snapshots, not every update. Consumer-queue
overflow coalesces into one gap event (not written to disk). Writer-queue
overflow coalesces into a `writer_overflow` gap that is written so Hawkes
fits cannot mistake a hole for silence.

Measured on SPCX-USDT-SWAP, Mon 19 Oct 2026 06:25–06:36 PT (weekday US
session, across the 6:30 open): trades ~1,190 msgs/min / ~26 KB/min
compressed; books5 ~548 msgs/min / ~36 KB/min compressed; ~3.8 MB/hour
total compressed (~5–9× smaller than raw, about 14% of raw). The open
spiked to ~20,000 contracts per 15 s. Keep the current 512 MiB recorder
run budget and 4 GiB cache budget unless disk is tight or you record many
instruments / full `books`.

Maintenance:

```python
windows = upcoming_maintenance(client)
for w in windows:
    print(w.title, w.begin_utc, w.end_utc, w.services)
```

Announcements (`/api/v5/support/announcements` and announcement types) are
keyless; use them as context, not as a trading signal.

## Rate-limit etiquette

Public REST limits are **per IP**. The limiter on `PublicClient` is **per
client object**, not per IP: two processes or two clients on the same IP can
still exceed OKX's caps. This client paces documented limits and retries 429 /
`50011` / 5xx with backoff. Still:

- Prefer one process and one `PublicClient`. Do not fan out many agents against
  the same IP.
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
