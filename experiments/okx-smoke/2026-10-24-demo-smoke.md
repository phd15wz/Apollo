# 2026-10-24 OKX demo smoke

Isaac’s demo smoke of the stage-2 private client, run on 2026-10-24 on the [PR #22](https://github.com/phd15wz/Apollo/pull/22) branch (`cursor/okx-private-client-f5ff`). That branch merged to `main` on 2026-10-25 (`7fa2cf0`).

This file records pass/fail and error codes. It omits API keys, passphrases, account identifiers, the returned `ord_id`, and any balance JSON.

## Environment

- `OKX_DEMO=1`. The CLI refuses to start without it. Signed calls stay on demo (`x-simulated-trading: 1` against `https://www.okx.com`).
- Reads are the default. Place and cancel ran only with `--allow-orders`.
- Withdraw, transfer, and deposit paths were not called.
- Live Trade stayed locked. `LIVE_TRADE_ENABLED` is a tripwire in `tools/okx/guards.py`. Setting it does not open a live order path.

Jessie’s risk ladder, still in force: demo, then live read-only, then Trade. This run is the demo step.

CLI entry points used for the run (credentials stay in the environment):

```bash
OKX_DEMO=1 python3 -m tools.okx.private_cli balance
OKX_DEMO=1 python3 -m tools.okx.private_cli positions
OKX_DEMO=1 python3 -m tools.okx.private_cli --allow-orders place ...
OKX_DEMO=1 python3 -m tools.okx.private_cli --allow-orders cancel --inst <inst> --ord-id <ord_id>
```

A new-risk place also needs caller-supplied `--margin-usd` under the client cap. The dollar figure from this smoke is not recorded.

## Results

| Step | Request | Result |
| --- | --- | --- |
| Balance | Demo account balance | Pass. Demo paper balances came back for USDT, BTC, ETH, and OKB, and they were non-empty. Amounts are not recorded. |
| Swap place | `BTC-USDT-SWAP` limit buy | Fail. OKX **51010**. The demo account was in spot/cash mode, so the swap request is unsupported there. The client reached OKX and returned the exchange code. |
| Spot place | `BTC-USDT` cash limit buy, size `0.001`, price `40000` (far below the market) | Pass. An `ord_id` came back. Order `state` was `live`. |
| Cancel | Same `ord_id` on `BTC-USDT` | Pass. `s_code` `0`. Order `state` was `canceled`. |
| Positions | Positions after the cancel | Pass. Empty. |

OKX documents **51010** as “Request unsupported under current account mode” ([API FAQ](https://www.okx.com/en-us/help/api-faq)). The smoke reading matches that: spot mode on the demo account, not a defect in the PR #22 client.

## Earlier environment mismatch

Before this smoke, a live key against the demo-forced client returned OKX **50101**, “APIKey does not match current environment.” OKX’s FAQ states the same mismatch: a live key with the simulated-trading header, or a demo key without it. That is why the upcoming live-read change exists. This smoke did not turn demo off, and it did not start that live-read path.
