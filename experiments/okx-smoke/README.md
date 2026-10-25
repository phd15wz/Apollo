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

## Log

- [2026-10-24 demo smoke](2026-10-24-demo-smoke.md) — Isaac, PR #22 branch, `OKX_DEMO=1`.
- [2026-10-25 live read smoke](2026-10-25-live-read-smoke.md) — Isaac, `main` after PR #24, `OKX_DEMO` unset, no `--allow-orders`.
