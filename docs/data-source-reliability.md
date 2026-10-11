# Data source reliability

Policy for classifying the reliability of data, information, news, and rumors used by the owner's AI agents (quant trading on OKX). First application: the exchange holiday calendar in `data/exchange_calendar` and `tools/exchange_calendar`.

## Reliability dictionary

Agents normalize status synonyms to grades A / B / C. Existing dataset values `official` and `secondary-confirmed` stay as they are and are equivalent to A and B.

| Grade | Tier | Status values |
| --- | --- | --- |
| A | Tier 1 | `official`, `verified` |
| B | Tier 2 | `secondary-confirmed`, `secondary-verified`, `trustworthy` |
| C | Tier 3 | `unverified`, `rumor` |
| — | (non-grade) | `pending` — announced as not final, or not yet announced |

```
A: ['official', 'verified']
B: ['secondary-confirmed', 'secondary-verified', 'trustworthy']
C: ['unverified', 'rumor']
# pending — not yet announced; its own non-grade status
```

## Tiers

**Tier 1 / grade A — primary sources.** Exchanges, central banks, regulators, and the original data owner (Fed, SEC, exchange websites).

**Tier 2 / grade B — reliable news.** Reuters, Bloomberg, FT, BBC, CNN, VOA, Fox, Yahoo Finance, and similar. Counts as B only when the story reports a fact **and** cites a primary source or a named official. Two independent tier-2 outlets agreeing is stronger than one.

**Tier 3 / grade C — everything else.** Social media, blogs, commercial platforms, rumors.

## Rules

1. **Weakest link.** Confidence is capped by the weakest link. A tier-2 story that only repeats a tweet or anonymous rumor is tier 3.
2. **Conflicts.** Log both sources. The higher tier wins. If a risk limit is affected, alert the Risk Officer.
3. **Tier 3 never acts.** Tier-3 items may trigger an alert for review. They never trigger a trade, limit change, or automated action. Any step toward live trading needs the owner's explicit approval.
4. **Staleness.** Each item has a max age by tier/type. Calendar entries are re-checked at the next refresh and at least every 35 days. Market-moving news expires quickly. Label expired items `stale`.
5. **Provenance.** Every item records source URL, retrieval time, and tier/grade. Market-moving information also records the original publisher.
6. **Reason codes.** Low-confidence rows carry a reason, e.g. `primary_unreadable` when a primary site cannot be read by AI (KRX, CME, sometimes SSE). A secondary row with that reason is not a disagreement with a primary.
7. **Labels in output.** Reminders and reports carry the tier label on every item (e.g. `secondary-verified, re-check`).
8. **Prices and market data.** Come only from exchange or licensed feeds, never from news articles.
9. **Risk limits.** Risk limits and thresholds are never derived from news.

## Worked examples

### Holiday calendar

NYSE publishes 2026 closures on `nyse.com`. The fetch succeeds → status `official` (A). An older year exists only in agreeing library calendars, not on a fetched exchange page → `secondary-confirmed` (B). KRX has no readable official page (`primary_unreadable`) and only a library mirror → `unverified` (C); do not treat that as a conflict with a primary. A future SSE year mentioned as “not final” → `pending` (non-grade). Strategies use only `load_confirmed()` (`official` and `secondary-confirmed`).

### Market rumor

A Telegram post claims OKX will halt BTC perpetuals. No primary cite → tier 3 / `rumor`. Alert for review only; no trade, no limit change. Later Reuters reports the same fact and quotes a named exchange official → upgrade to tier 2 / `secondary-verified`. If the exchange notice is fetched → tier 1 / `official`. Until then, reminders label the item with its current tier.
