# OKX market data and demo trading

Stage 1 is a keyless Python client for OKX v5 **public** REST and WebSocket
data. It cannot trade. Stage 2 (`PrivateClient`) signs account reads and
demo orders. Live mode reads trading balance, positions, and funding
balances. One opt-in smoke may place and immediately cancel a resting
BTC-USDT cash limit on a live Trade key. Amend stays on demo. It cannot
withdraw. Withdrawal stays out.

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
end time, it first makes one timestamp-mode request (`type=2`) and takes the
**highest trade id at that timestamp** (`ts <= end`), then pages backward by
trade id. Hitting `max_pages` before the window start raises
`PaginationBudgetExceeded` (the trades already fetched are on `exc.trades`,
including when there was no start time) rather than returning a silent
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
   message emits `recovered` and clears the flag. Lag is the larger of
   writer delay (write time minus socket `recv` time) and absolute delay
   (OKX `ts` versus local wall, corrected by keyless `GET /api/v5/public/time`
   about every 60 s, off the socket loop). Either side above
   `lag_threshold` (default 5 s) emits `lag` / `lag_cleared`. A failed
   time poll emits one `clock_sync_unavailable` and falls back to
   uncorrected local time. `stream.writer_lag` / `stream.writer_lagging`
   expose the flag.
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

### Measured recording size (SPCX-USDT-SWAP)

Weekday US session, Mon 19 Oct 2026, 06:25–06:36 PT (across the 6:30 open):

| Channel | Messages / min | Compressed |
| --- | --- | --- |
| `trades` | ~1,190 | ~26 KB/min |
| `books5` | ~548 | ~36 KB/min |

Together ~3.8 MB/hour compressed, about 5–9× smaller than raw JSONL (~14% of
raw). The 6:30 open itself spiked to ~20,000 contracts per 15 s.

Those rates suggest a **512 MiB** per-run recording budget covers on the order
of 100 hours of this pair (trades + books5) even through the open, and the
**4 GiB** cache budget covers many such runs. Defaults stay at
`OKX_DATA_BUDGET_BYTES=4 GiB`, recorder per-run 512 MiB when that env is unset,
`OKX_MIN_FREE_DISK_BYTES=10 GiB`, and daily rotation. Raise the run budget
only if you record many instruments or full `books` snapshots; lower it if
disk is tight. Do not commit recordings.

**Overflow policy (in-memory consumer queue):** default 10_000 *data*
events. Repeated alerts for one `(kind, channel, instId)` merge in place
(the default 64-slot cap no longer books a new instrument onto another
feed). `stale`/`recovered` for one feed keep a single current state (a
feed that ends stale delivers `stale` last). Other kinds merge repeats of
`(kind, channel, instId)`.
Gap events keep exact trade-id or timestamp ranges and merge only when
those ranges touch, up to 256 pending holes; beyond that one summary
gap is emitted (`precise=false`, hole count, trades missed, overall
first/last). When the data lane is full, the oldest data event is
folded into an overflow `gap` (invariant:
`overflow_count == sum(gap.dropped)`). Consumer overflow is **not**
written to disk. The recorder enqueues whole WebSocket/backfill batches
and waits up to 2s for writer-queue space without blocking the
event-loop thread. Only the writer thread opens, writes, or closes
recording files. `stop()` waits for in-flight offers, then a shutdown
sentinel lets the writer drain and close. Anything still unwritten
becomes a recorded gap. Downstream Hawkes fits must treat `kind='gap'`
as a hole in the tape, never as a quiet period.

## Stage 2 — private client (demo writes, live reads, opt-in smoke)

`PublicClient` is unchanged: keyless, GET-only, no `OK-ACCESS-*` headers.
`PrivateClient` is a separate object. OKX demo trading uses the same REST
host as production (`DEMO_REST_BASE_URL` = `https://www.okx.com`) plus the
header `x-simulated-trading: 1`. There is no second REST hostname.

Demo stays the library default: `PrivateClient()` with `OKX_DEMO` unset
still sends the simulated-trading header. Live read-only is `demo=False`,
or `OKX_DEMO` set to `0` / `false` / `no` / `off`. That mode signs **balance,
positions, and funding balances** and omits `x-simulated-trading`, so a
live Read key works. Amend and the other private GETs still raise
`LiveTradingDisabled` on a live key. `OKX_ALLOW_ORDERS` alone does not turn
writes live. A value of `OKX_DEMO` other than on or off still raises.

Live place and cancel are a separate opt-in, off by default. Set
`OKX_LIVE_TRADE=1` (or pass `live_trade=True`) **and** the order go flag
(`allow_orders=True`, `OKX_ALLOW_ORDERS=1`, or CLI `--allow-orders`).
`OKX_READ_ONLY=1` still wins. The only live order is a BTC-USDT cash limit
buy, size `0.001`, price at most `40000` (the 2026-10-24 demo smoke: a
resting limit far from the market). `smoke_place_cancel` places that order
and cancels it before returning. Anything else, including amend, swap, a
larger size, or a higher price, stays `LiveTradingDisabled`.

`LIVE_TRADE_ENABLED` in `guards.py` is a tripwire, not a switch. It is
`False`. Setting it to `True` makes the client refuse to start, including
when `OKX_LIVE_TRADE=1` is set. Do not flip the constant.

### Keys

Environment only. Never commit them, never pass them as CLI arguments, and
never read them from the repo.

| Variable | Aliases |
| --- | --- |
| `OKX_API_KEY` | `OKX_KEY` |
| `OKX_API_SECRET` | `OKX_SECRET_KEY`, `OKX_SECRET` |
| `OKX_API_PASSPHRASE` | `OKX_PASSPHRASE` |

`OkxCredentials` redacts `repr` and cannot be pickled. HTTP debug loggers
(`urllib3`, `requests`) get a filter that replaces those values. Values
shorter than 4 characters are not scrubbed. Missing variables raise
`MissingCredentials` with the names only.

### Calls

```python
from tools.okx import PrivateClient

client = PrivateClient()  # demo, read-only; keys from the environment
balance = client.balance(ccy="USDT")
positions = client.positions(inst_type="SWAP", inst_id="SPCX-USDT-SWAP")
funding = client.funding(ccy="USDT")

live = PrivateClient(demo=False)  # live Read key; balance, positions, funding
live.balance(ccy="USDT")
live.funding()
order = client.get_order("SPCX-USDT-SWAP", ord_id="123")
pending = client.open_orders(inst_type="SWAP")
history = client.order_history("SWAP", inst_id="SPCX-USDT-SWAP")

# Writes need an explicit go flag. Still demo. Still no withdrawals.
trading = PrivateClient(allow_orders=True)
ack = trading.place_order(
    "SPCX-USDT-SWAP",
    side="buy",
    sz="1",
    td_mode="cross",
    ord_type="limit",
    px="100",
    margin_usd=4,
)
trading.cancel_order("SPCX-USDT-SWAP", ord_id=ack.ord_id)
trading.amend_order("SPCX-USDT-SWAP", ord_id=ack.ord_id, new_px="101")

# Live smoke: opt-in plus the go flag. Places, then cancels, same client.
# Withdraw is not a method on this client.
smoke = PrivateClient(demo=False, allow_orders=True, live_trade=True)
placed, cancelled = smoke.smoke_place_cancel(px="40000", margin_usd=1)
```

Allowlisted routes only:

| Method | Path |
| --- | --- |
| GET | `/api/v5/account/balance`, `/api/v5/account/positions` |
| GET | `/api/v5/asset/balances` (funding account; optional `ccy`) |
| GET | `/api/v5/trade/order`, `/api/v5/trade/orders-pending`, `/api/v5/trade/orders-history`, `/api/v5/trade/orders-history-archive` |
| POST | `/api/v5/trade/order`, `/api/v5/trade/cancel-order`, `/api/v5/trade/amend-order` |

Anything else, including other `/api/v5/asset` routes, `/api/v5/finance`, and
any path containing withdraw, transfer, or deposit, raises `UnsafeEndpointError`.
`GET /api/v5/asset/balances` is the only asset exception.

### Guards

Enforced now:

1. **Read-only kill switch.** POSTs, including cancel, refuse unless
   `allow_orders=True` or `OKX_ALLOW_ORDERS=1`. `read_only=True`,
   `--read-only`, and `OKX_READ_ONLY=1` override the go flag.
2. **Demo writes, live reads, one live smoke.** Demo requests send
   `x-simulated-trading: 1`. Live mode omits that header on balance,
   positions, and funding balances. Live place and cancel also omit it, and
   only when `OKX_LIVE_TRADE=1` or `live_trade=True` is set with the go
   flag. The live order is BTC-USDT, cash, limit, buy, size `0.001`,
   px `<= 40000`, then cancel on that same client. Amend stays on demo.
3. **No withdraw / transfer / deposit** paths. Same exception type as the
   public client's blocked routes. `GET /api/v5/asset/balances` is unchanged.
4. **Soft cap.** Incremental `margin_usd` plus caller-supplied
   `open_margins` must stay within **$10** per name
   (`NAME_MARGIN_CAP_USD`). A `shared_cap: true` group in
   `config/candidate_pool.yaml` (chips, memory, optical, and the other
   shared groups) shares that $10 across members. `risk_gate=True`,
   `--risk-gate`, or `OKX_RISK_GATE=1` halves the ceiling to **$5**.
   A new risk order without `margin_usd` is refused. `reduce_only=True`
   may omit it.
5. **Missing go flag or `--read-only`** refuses private writes.

Deferred (not done in this package):

- Live amend, and any live order besides the BTC-USDT place-then-cancel smoke.
- Mark × contract value × size / leverage margin math. Callers pass
  `margin_usd`. `open_margins_from_positions` only sums the `margin` field
  already on those objects; it does not convert currency and it is not
  called automatically.
- A shared-cap ledger across processes. `open_margins` is an in-memory stub.
- Connecting `OKX_RISK_GATE` to the macro hard gate (`DGS10` / `DFII10`).
  The flag is manual. While it is on, the ceiling is half size. This
  package does not block every new long until that gate clears.
- Pool membership as an order allowlist. Names outside the pool are
  solo-capped at the same $10.
- Repricing an order on a price-only amend. Changing `new_sz` re-checks
  `margin_usd`. Batch, algo, and private WebSocket orders are absent.

### CLI

No key arguments. `balance`, `positions`, and `funding` hit the live account
when `OKX_DEMO` is unset or off, without `--allow-orders`. Demo place, cancel,
and order lookup still require `OKX_DEMO=1`. The live smoke requires
`OKX_LIVE_TRADE=1`, `--allow-orders`, and `OKX_DEMO` unset or off. `smoke`
cancels in the same process. A live `place` of the smoke shape cancels
before the process exits. Live `cancel` is only BTC-USDT, for a retry.
Order lookup and amend are not live.

```bash
python3 -m tools.okx.private_cli balance
python3 -m tools.okx.private_cli positions --inst SPCX-USDT-SWAP
python3 -m tools.okx.private_cli funding --ccy USDT
OKX_DEMO=1 python3 -m tools.okx.private_cli balance
OKX_DEMO=1 python3 -m tools.okx.private_cli order --inst SPCX-USDT-SWAP --ord-id 123
OKX_DEMO=1 python3 -m tools.okx.private_cli --allow-orders cancel \
  --inst SPCX-USDT-SWAP --ord-id 123
OKX_DEMO=1 python3 -m tools.okx.private_cli --allow-orders place \
  --inst SPCX-USDT-SWAP --side buy --sz 1 --td-mode cross \
  --ord-type limit --px 100 --margin-usd 4
OKX_LIVE_TRADE=1 python3 -m tools.okx.private_cli --allow-orders smoke \
  --px 40000 --margin-usd 1
```

`--read-only` forces the kill switch even when `OKX_ALLOW_ORDERS=1`.
Default tests mock HTTP. A demo smoke test is skipped unless you opt in:

```bash
OKX_DEMO=1 python3 -m pytest tools/okx/tests -q -m okx_demo
```

That smoke test reads balance. It does not place or cancel. CI does not
need API keys.

## Safety

- Public client: allowlisted GET paths only. Account, trade, asset, and
  withdrawal URLs raise `UnsafeEndpointError`.
- Allowlisted public WebSocket channels and hosts only. `login` and private
  channels raise `PrivateChannelError`. The public stream still has no
  login path.
- `PublicClient` takes no key argument and sends no `OK-ACCESS-*` headers.
  Signing lives in `auth.py` and is used only by `PrivateClient`.
- Private client: demo header on demo calls; live balance, positions, and
  funding balances omit it. Allowlisted trade and account routes, plus
  `GET /api/v5/asset/balances`. No withdrawal endpoints. Live place and
  cancel omit the demo header only for the BTC-USDT smoke under
  `OKX_LIVE_TRADE=1` and the order go flag. Live amend stays refused.
- Unit tests mock HTTP with recorded JSON under `tests/fixtures/` and use an
  in-memory fake WebSocket (no network). Live public tests stay behind
  `OKX_LIVE=1`. Demo private tests stay behind `OKX_DEMO=1` plus a key.
