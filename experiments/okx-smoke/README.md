# OKX smoke notes

Dated notes for Apollo’s OKX private client. The committee’s home for these notes is `experiments/`.

Client code stays in `tools/okx`. This directory is markdown only.

## What belongs here

- Pass or fail for each step.
- Instrument, side, order type, size, and limit price when those are the point of the test.
- OKX error codes and the account-mode or environment reading from the run.
- The ladder step the run was allowed to reach.

## What stays out

Never commit secrets, API keys, passphrases, account identifiers, order ids, or full balance dumps. Demo paper balances may be described as present or empty. A live book may be described as empty or $0 equity. Do not paste equity totals beyond that, or raw balance JSON.

## Live place-cancel (not yet run)

The client can place and cancel two resting limits on a live Trade key when `OKX_LIVE_TRADE=1` and `--allow-orders` are both set and `OKX_DEMO` is unset. Cash: `BTC-USDT`, size `0.001`, px at most `40000`, via `smoke`. SWAP: `BTC-USDT-SWAP`, size `0.01`, `tdMode` `isolated` only (strategy default), px at most `40000`, via `smoke-swap`. Both cancel in the same process. `cross` is not the live SWAP shape. Withdraw stays off. Log a run here only after it happens. Do not record secrets, order ids, or balances.

`set-leverage` can set isolated leverage from 1 to 5 on a USDT-SWAP under those same two flags. Demo uses `OKX_DEMO=1` and `--allow-orders`. `cross` stays refused. The five mild-3.1 names (MU, SKHY, LITE, TSM, QQQ USDT-SWAP) are capped at lever 2. Log a run here only after it happens.

The locked mild-3.1 stub can place and cancel under the same two flags: isolated only, MU/LITE buy sz ≤ 0.01, SKHY buy sz ≤ 0.03, TSM buy sz ≤ 0.02, QQQ sell sz ≤ 0.01. Those orders rest. This directory does not place them. Log a run here only after it happens.

## Log

- [2026-10-24 demo smoke](2026-10-24-demo-smoke.md) — Isaac, PR #22 branch, `OKX_DEMO=1`.
- [2026-10-25 live read smoke](2026-10-25-live-read-smoke.md) — Isaac, `main` after PR #24, `OKX_DEMO` unset, no `--allow-orders`.
