# OKX public market data (stage 1)

Keyless Python client for OKX v5 **public** REST data. It cannot trade, cannot
withdraw, and has no API-key or signing path. Stage 2 (read-only keys from a
secret store) and orders (demo mode, explicit owner approval) are out of
scope.

Docs: [OKX API v5](https://www.okx.com/docs-v5/en/). This package is written
from those public endpoints. It does not vendor [OKX Agent Trade Kit](https://github.com/okx/agent-trade-kit).

Requires Python 3.11+. Runtime deps already in the repo: `requests`, `numpy`.
The public WebSocket client needs the optional `websockets` package
(`python3 -m pip install 'websockets>=12'`). No Numba. Pandas is not used.

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

`history_trades_range` defaults to trade-id paging. When the window has an
end time, it first makes one timestamp-mode request (`type=2`) to locate the
**newest trade at or before the end**, then pages backward by trade id. Hitting
`max_pages` before the window start raises `PaginationBudgetExceeded` (the
trades already fetched are on `exc.trades`) rather than returning a silent
partial or empty list. A window older than OKX's ~3-month REST retention
raises `HistoryWindowTooOld` and tells you to use the bulk archive.

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

## Public WebSocket

`wss://ws.okx.com:8443/ws/v5/public` for `trades`, `tickers`, `books5`,
`books`, `bbo-tbt`, `funding-rate`, `mark-price`. Candle channels use
`wss://ws.okx.com:8443/ws/v5/business`. If the 8443 handshake times out
(some networks block that port), the client retries the **same host and
path on port 443** and logs the fallback. Override with
`OKX_WS_PUBLIC_URL` / `OKX_WS_BUSINESS_URL` or `--public-url` /
`--business-url`. Overrides are `wss` and allowlisted hosts only — the
same rule as REST. `allow_unofficial_hosts` is a constructor opt-in;
env vars cannot enable it. Hosts: `ws.okx.com`, `wsaws.okx.com`,
`wspap.okx.com`, `wseea.okx.com`, `wsus.okx.com`. `login` and private
channels raise `PrivateChannelError`.

The client sends a text `ping` after ~25 s of silence and expects `pong`.
It reconnects with exponential backoff + jitter and resubscribes. Connection
attempts are capped at 3/s and subscribe/unsubscribe at 480/hour.

```python
from tools.okx import OkxPublicStream, PublicClient

async with OkxPublicStream(
    [("trades", "SPCX-USDT-SWAP"), ("books5", "SPCX-USDT-SWAP")],
    rest=PublicClient(),
) as stream:
    async for msg in stream:
        if stream.is_stale(msg.channel, msg.inst_id) or msg.kind == "gap":
            continue  # never treat a gap as a quiet period
```

CLI demo (prints live trades / top-of-book; recording off unless `--record`):

```bash
python3 -m pip install 'websockets>=12'
python3 -m tools.okx.stream_demo --inst SPCX-USDT-SWAP --channels trades,books5 --seconds 60
# if 8443 is blocked:
python3 -m tools.okx.stream_demo --inst SPCX-USDT-SWAP --channels trades,books5 --seconds 60 \
  --public-url wss://ws.okx.com:443/ws/v5/public
```

### Safeguards

1. **Gap detection.** Last trade id is tracked per instrument. After a
   reconnect or a jump in trade ids the missing range is backfilled from
   REST `/market/history-trades` (same trade-id pager), de-duplicated, and
   emitted in order. The public `trades` channel aggregates prints: `tradeId`
   is the last id and `count` is how many consecutive ids the message
   already covers, so an aggregation is not treated as a hole. If backfill
   cannot finish, a `gap` event is emitted **and** written into the
   recording file (channel, instId, first/last missing trade id,
   timestamps, dropped count). A sequence or CRC32 checksum break on
   `books` triggers a full unsubscribe/subscribe resync. Checksum `0` is
   skipped (OKX deprecated the field; it is always 0).
2. **Stale-feed timeout.** Configurable per stream (`stale_timeout`,
   default 60 s). Silence emits `stale` and sets a flag
   (`stream.is_stale(...)` / `stream.stale_subscriptions`). The next data
   message emits `recovered` and clears the flag. Writer backlog older
   than `lag_threshold` (default 5 s) emits `lag` / `lag_cleared` and is
   exposed as `stream.writer_lag` / `stream.writer_lagging`.
3. **Size caps and bounded queues.** Recording is **off by default**.
   `--record-channels trades` (or `record_channels=`) records a subset.
   When enabled it writes gzip JSONL under `OKX_DATA_DIR/recordings/`
   from a bounded background writer (one open gzip stream per file),
   counts toward `OKX_DATA_BUDGET_BYTES` / `OKX_DATA_RUN_BUDGET_BYTES`,
   and raises `CacheBudgetExceeded`. Free disk below
   `OKX_MIN_FREE_DISK_BYTES` (default 10 GiB) raises `LowDiskError` and
   stops recording. Files rotate **daily** by default (or hourly) per
   instrument/channel. Retention can cap age and/or total recording
   bytes; oldest files are deleted first. The full `books` channel records
   periodic snapshots (configurable interval), not every update.
4. **Public only.** No keys, no `login`, no private path. Unofficial hosts
   still require `wss` unless `allow_insecure=True`.

**Overflow policy (in-memory consumer queue):** default 10_000 *data*
events. Alerts (`stale`, `recovered`, `lag`, …) live in a capped lane
(64): repeats of the same `(kind, channel, instId)` keep the latest
state plus a repeat count. Gap events keep exact trade-id or timestamp
ranges and merge only when those ranges touch; extra pending gaps spill
rather than widen. When the data lane is full, the oldest data event is
folded into one overflow `gap` (invariant:
`overflow_count == sum(gap.dropped)`). Consumer overflow is **not**
written to disk. The recorder enqueues whole WebSocket/backfill batches
and waits up to 2s for writer-queue space without blocking the
event-loop thread. If that timeout expires, drops become a
`writer_overflow` gap that **is** written. `stop()` drains the writer
(bounded `shutdown_timeout`, default 8 s); anything still unwritten
becomes a recorded gap. Downstream Hawkes fits must treat `kind='gap'`
as a hole in the tape, never as a quiet period.

## Safety

- Allowlisted GET paths only. Account, trade, asset, and withdrawal URLs raise
  `UnsafeEndpointError`.
- Allowlisted public WebSocket channels and hosts only. `login` and private
  channels raise `PrivateChannelError`.
- No `api_key` constructor argument, no `OK-ACCESS-*` headers, no HMAC.
- Unit tests mock HTTP with recorded JSON under `tests/fixtures/` and use an
  in-memory fake WebSocket (no network). Live tests stay behind `OKX_LIVE=1`.
