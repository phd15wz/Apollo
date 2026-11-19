# 2026-11-19 selected-plan runbook

This is a no-trade checkpoint. It is not live clearance. Approval of the
risk plan is not permission to arm, enter, or leave a process watching
the book. Nothing here starts a daemon, a cron job, or an order.

Offline tests use synthetic prices and a fake transport. They do not prove
a live exit, a live fee, a live leverage setting, or that the liquidation
projection matches OKX `liqPx`.

## Three separate pieces

Permanent capabilities stay on the clients and in `tools/okx/guards.py`:

- Sanctioned live Read GETs. They do not turn on Trade.
- The mild-3.1 stub, including the LITE reduce-only allowlist row. That
  row is not a portfolio control and was not live-proven.
- Dual opt-in for any live place or cancel: `OKX_LIVE_TRADE` and the
  order go flag. `OKX_READ_ONLY` still wins. `LIVE_TRADE_ENABLED` stays
  a tripwire. Withdraw, transfer, deposit, amend, and set-position-mode
  stay refused.

Temporary policy lives in `tools/okx/pilot_policy.py` and is meant to be
retired with this pilot. `pilot_plan` is a constructor bool. It defaults
off. `OKX_PILOT_PLAN` does not turn it on. There is no CLI place command.

The exact book is isolated, 2x, and not resized:

- LONG `MU-USDT-SWAP` 0.01
- LONG `LITE-USDT-SWAP` 0.01
- LONG `COHR-USDT-SWAP` 0.02
- SHORT `QQQ-USDT-SWAP` 0.01

Caps: gross at most 30 USDT, initial margin at most 15 USDT. The exit
trigger is combined net loss at or past 7 USDT from the arm baseline.
That 7 is a trigger, not a guaranteed maximum loss. Margin is not loss.
At the recorded Nov 19 06:22 PT public quotes the book was about 24.5554
gross, 12.2777 initial margin, and 11.0882 net long. Those numbers are
an observation, not a size target.

Recovery state lives in `tools/okx/pilot_audit.py`. The file stores the
phase, the baseline, the policy fingerprint, and submitted client order
ids. It does not store the policy numbers or API keys. A fingerprint
mismatch refuses to load. A restart does not continue an entry that was
in progress.

## What was exercised offline

`python3 -m pytest tools/okx/tests/test_pilot_plan.py tools/okx/tests/test_guards.py tools/okx/tests/test_private.py tools/okx/tests/test_mild31_stub.py -q`

The synthetic suite covers one-leg entry failure, a partial fill, an IOC
that rests and fills on cancel, a limit that rests too long, a delayed
fill, restart without a second entry, the -7 trigger, a leg stop on all
open legs, funding sign and fees in the pure PnL function, the
realized/unrealized handoff, unknown pending orders, missing tier, fee,
and leverage, a gross above 30, a stale mark, a zero mark on that leg,
close-attempt exhaustion, an oversized position, and `enter` without
`send_orders`. None of those calls leave the process.

## What is not done

- No live read and no live order has been run for this book.
- COHR entry and close exist only behind `pilot_plan=True` and were not
  sent to OKX. The adapter test refuses a place when the go flag is off.
- The public position-tier table is not this account's VIP tier. Arming
  requires `acknowledge_public_tier_table=True` because no Read endpoint
  returns this account's maintenance rate before a position exists.
  `GET /api/v5/account/position-tiers` is not used: it does not return
  `mmr`.
- The isolated liquidation price is a projection at leverage 2. It is
  not exchange `liqPx`. Once a position exists, the controller uses the
  more conservative of the projection and `liqPx` when both are on the
  correct side of the mark. If neither is usable it will not invent a
  buffer. This checkpoint does not claim the formula matches OKX.
- Each leg's isolated margin is a few USDT, so one leg can be liquidated
  before combined net loss reaches 7. Arming requires that acknowledgement.
  Do not increase size and do not add collateral to change that.
- Bills are the last 7 days. A baseline older than that, or a short page
  that does not finish, fails closed. Archive bill endpoints are not reads.
- `distance_to_liq_fraction`, slippage, input age, step gap, order type,
  and the close-attempt bound have no production default. Test files use
  stand-in numbers. Those numbers are not risk-approved.
- `enter(..., send_orders=True)` can submit the four opens only after the
  policy is complete, the book passes the pre-entry checks, and the
  existing live-order gates are on. That call is still not live clearance.
  There is no second approval token. Do not call it.
- `step` is called by an operator. A gap larger than the configured step
  gap is a health failure. A one-shot cron is not a monitor. This PR
  starts no service.
- An oversized leg, or a remainder below the lot, escalates and does not
  round. The automatic flatten stops. A person has to finish.
- After escalation, `step` sends nothing.
- Fills are not atomic. One leg can fill and another can fail. The
  controller then cancels its own prefix and reduces what it can see.

## Runtime versus this source

The read-only runtime noted at `d7fa452c9ddad790501e01fdc1372b8dca61f002`
does not contain the LITE reduce-only allowlist row. `main` at
`3394944f6f6fbf809feeba1ad33aa5ffe9b45c9e` has that row from `f33c729`.
The row does not auto-flatten and was not live-proven. COHR is not a
stub name. Do not treat the runtime checkout as this branch.

## Arming checks

`arm` and `assess` place nothing. They block unless all of the following
are true:

- Futures mode (`acctLv` 2), `net_mode` or `long_short_mode`, Read permission.
- Trade permission is required only for `enter`, not for a read.
- Isolated leverage already 2 on the side being opened. This code does
  not call set-leverage or set-position-mode.
- The four names are flat and there is no pending order.
- Marks, instruments, the matching public tier, and the account fee group
  are fresh. The caller supplies the maximum age. Above 120000 ms is refused.
- Gross is at most 30 and initial margin at most 15 at the plan size.
  A breach refuses. Size is not changed.
- Both acknowledgements above are true.
- `live_clearance` on the result is false and cannot be set true.

`enter` additionally requires `send_orders=True`. Omitting it places nothing.

## Endpoints

| Path | Permission | Why |
| --- | --- | --- |
| `/api/v5/account/balance` | Read | Equity. Not a loss measure. |
| `/api/v5/account/positions` | Read | Position, avgPx, upl, mark, and liqPx once a position exists. |
| `/api/v5/asset/balances` | Read | Funding balance only. Not a transfer. |
| `/api/v5/trade/orders-pending` | Read | Pending was unknown while this GET was refused live. |
| `/api/v5/trade/order` | Read | Reconcile one order after place or cancel. |
| `/api/v5/trade/orders-history` | Read | Terminal orders in the last 7 days. |
| `/api/v5/account/config` | Read | acctLv and posMode. This GET does not set them. |
| `/api/v5/account/trade-fee` | Read | Account fee tier. Not a guessed rate. |
| `/api/v5/account/leverage-info` | Read | Leverage actually set. Not set-leverage. |
| `/api/v5/account/bills` | Read | Trade pnl, fee, and funding since the baseline. Last 7 days. |
| `/api/v5/public/position-tiers` | keyless | Pre-entry maintenance rate. Not the account PM endpoint. |

`/api/v5/trade/orders-history-archive`, `/api/v5/account/position-tiers`,
amend, withdraw, transfer, and deposit stay refused on a live key.
Place and cancel still need both live-order gates and, for this book,
`pilot_plan=True`.

## If something fails

A failed close, a stale mark, an unknown pending order on a selected
name, or a ledger that cannot be priced stops new orders and sets the
phase to `escalated`. Cancel only ids this process submitted (`APL6065…`).
Do not cancel a foreign order from this tool. Flatten anything still
open by hand. Do not add collateral and do not increase size to "make
the 7 work."
