# skills

Reusable skills for agents.

Each skill lives in `skills/<slug>/SKILL.md`. `<slug>` is lowercase and hyphenated. Put files that skill needs in the same directory, and add a one-line entry here when you add one.

## Skills

- `exchange-calendar` — Look up NYSE (Nasdaq aliases to NYSE), CME, HKEX, SSE, JPX, and KRX holidays and refresh the sourced dataset.
- `hawkes-process` — Fit and diagnose Hawkes models on event times (OKX trade arrivals, buy/sell cross-excitation). Toolkit: `tools/hawkes/`.
- `okx-market-data` — Fetch OKX public (keyless) market data, bulk trade history, and maintenance windows. Toolkit: `tools/okx/` `PublicClient`. `PrivateClient` is live read-only balance, positions, and funding balances, demo orders, opt-in BTC-USDT cash plus BTC-USDT-SWAP `isolated` place-then-cancel smokes, and isolated set-leverage 1-5 on a USDT-SWAP (`OKX_LIVE_TRADE=1`). No withdrawals.
