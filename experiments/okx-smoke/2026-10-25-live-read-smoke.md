# 2026-10-25 OKX live read smoke

Isaac’s live read-only smoke of the stage-2 private client, run on 2026-10-25 after [PR #24](https://github.com/phd15wz/Apollo/pull/24) merged to `main` (`2db3efc`, live read-only balance and positions).

This file records pass/fail and the local CLI refusal. It omits API keys, passphrases, account identifiers, order ids, and any balance JSON.

## Environment

- Live Read-only API key. `OKX_DEMO` unset. No `--allow-orders`.
- Balance and positions use the PR #24 live-read path (`demo=False`), which omits `x-simulated-trading` and signs against `https://www.okx.com`.
- Place and cancel were not sent to OKX. A place with `OKX_DEMO` unset and without `--allow-orders` was refused locally before any order request.
- Withdraw, transfer, and deposit paths were not called.
- Live Trade stayed locked. Jessie’s ladder parks Trade until Manshui funds a tiny budget and a Trade key with Withdraw off. `LIVE_TRADE_ENABLED` remains a tripwire in `tools/okx/guards.py`. Setting it does not open a live order path.

Jessie’s risk ladder, still in force: demo (2026-10-24), then live read-only (this run), then Trade.

CLI entry points for this run (credentials stay in the environment):

```bash
python3 -m tools.okx.private_cli balance
python3 -m tools.okx.private_cli positions
python3 -m tools.okx.private_cli place ...
```

The place invocation had `OKX_DEMO` unset and did not pass `--allow-orders`. No live place or cancel was attempted.

## Results

| Step | Request | Result |
| --- | --- | --- |
| Balance | Live account balance, `OKX_DEMO` unset, no `--allow-orders` | Pass. Authenticated. Book empty / $0 equity. |
| Positions | Live positions, same flags | Pass. Empty list. |
| Place | Place with `OKX_DEMO` unset and without `--allow-orders` | Fail locally. No request reached OKX. `LiveTradingDisabled` (CLI exit 2): “CLI place, cancel, and order lookup require OKX_DEMO=1. Live Trade is not enabled.” |
| Cancel | — | Not attempted. |

No OKX error code came back on the place attempt. The refusal is the local CLI message above. The Read key authenticated on balance.

## Live Trade

Still locked, per Jessie. Park until Manshui funds a tiny budget and a Trade key with Withdraw off. This smoke did not place or cancel on the live key.
