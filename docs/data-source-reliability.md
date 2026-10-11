# Data source reliability

Policy for classifying the reliability of data, information, news, and rumors used by the owner's AI agents (quant trading on OKX). First application: the exchange holiday calendar in `data/exchange_calendar` and `tools/exchange_calendar`.

Maintainer: chief secretary (Grok Bot). Jessie reviews changes.

## Reliability dictionary

Agents normalize status synonyms to grades A / B / C when **reading**. The synonyms are for reading only. Stored dataset statuses and `load_confirmed()` accept only `official`, `secondary-confirmed`, `unverified`, and `pending`. A new stored value requires a code change. Unknown statuses fail loudly in `load_confirmed()` (raise `ValueError`).

Existing dataset values `official` and `secondary-confirmed` stay as they are and map to A and B (library-only `secondary-confirmed` stays grade B; see below).

| Grade | Tier | Status values |
| --- | --- | --- |
| A | Tier 1 | `official`, `verified` |
| B | Tier 2 | `secondary-confirmed`, `secondary-verified`, `trustworthy` |
| C | Tier 3 | `unverified`, `rumor` |
| — | (non-grade) | `pending` (defined below) |

```
A: ['official', 'verified']
B: ['secondary-confirmed', 'secondary-verified', 'trustworthy']
C: ['unverified', 'rumor']
# pending — see definition below
```

**`pending`:** not yet announced; its own non-grade status. Do not treat it as A, B, or C.

## Tiers

**Tier 1 / grade A — primary sources.** Exchanges, central banks, regulators, and the original data owner (Fed, SEC, exchange websites).

**Tier 2 / grade B — reliable news.** Reuters, Bloomberg, FT, BBC, CNN, VOA, Fox, Yahoo Finance, and similar. Counts as B only when the story reports a fact **and** cites a primary source or a named official. Two independent tier-2 outlets agreeing is stronger than one, but they stay grade B and never upgrade to A. Independent means separate reporting, not one republished wire story.

**Tier 3 / grade C — everything else.** Social media, blogs, commercial platforms, rumors.

### Current dataset meaning of `secondary-confirmed`

In `tools/exchange_calendar` and `skills/exchange-calendar/SKILL.md` today, `secondary-confirmed` usually means two independent calendar libraries agree (`exchange_calendars` plus a native `pandas_market_calendars` calendar; mirrors do not count). Library-only agreement (two independent libraries agree, no news or official source) stays grade B / `secondary-confirmed` and is usable via `load_confirmed()`. Library disagreement stays `unverified` (grade C). Strictly, library-only agreement lacks a tier-2 news citation or an official page. (`OfficialEvent` also allows a fetched third-party copy of an exchange announcement to be stored as `secondary-confirmed`; that path does cite a primary notice.)

## Rules

1. **Weakest link.** Confidence is capped by the weakest link. A tier-2 story that only repeats a tweet or anonymous rumor is tier 3.
2. **Conflicts.** Log both sources. The higher tier wins. If a risk limit is affected, alert the Risk Officer (Jessie).
3. **Tier 3 never acts.** Tier-3 items may trigger an alert for review. They never trigger a trade, limit change, or automated action. Any step toward live trading needs the owner's explicit approval.
4. **Tier 2 may inform, not act.** Tier 2 may inform alerts and reminders. Any trade or limit action still needs the owner's approval.
5. **Staleness.** Each item has a max age by tier/type. Stale items must be displayed as stale.
   - Calendar entries: re-check at the next refresh and at least every 35 days.
   - Market-moving news:
     - Breaking / market-moving: **6 hours**
     - Same-day confirmed secondary reports: **24 hours**
     - Non-breaking secondary context: **7 days**
6. **Provenance.** Every item records source URL, retrieval time, and tier/grade. Market-moving information also records the original publisher.
7. **Reason codes.** Low-confidence rows carry a reason, e.g. `primary_unreadable` when a primary site cannot be read by AI (KRX, CME, sometimes SSE). A secondary row with that reason is not a disagreement with a primary. `primary_unreadable` is a policy label until the dataset has a reason-code column.
8. **Labels in output.** Reminders and reports show the tier letter (A, B, or C) beside the status on every item (e.g. `B secondary-verified, re-check`).
9. **Prices and market data.** Come only from exchange or licensed feeds, never from news articles.
10. **Risk limits.** Risk limits and thresholds are never derived from news.

## Worked examples

### Holiday calendar — official fetch

NYSE publishes 2026 closures on `nyse.com`. The fetch succeeds → status `official` (A). Strategies use only `load_confirmed()` (`official` and `secondary-confirmed`).

### Holiday calendar — library-only agreement

An older year exists only in agreeing library calendars, not on a fetched exchange page → stored as `secondary-confirmed` (B). If the libraries disagree → `unverified` (C).

### Holiday calendar — primary unreadable, Reuters cites the notice

The primary exchange page is unreadable by AI. Reuters reports the closures and cites the exchange notice → store as `secondary-confirmed` (B) with reason code `primary_unreadable`. Usable via `load_confirmed()`. Re-check when the primary becomes readable; upgrade to `official` (A) if the notice matches.

### Holiday calendar — unverified / pending

KRX has no readable official page (`primary_unreadable`) and only a library mirror → `unverified` (C); do not treat that as a conflict with a primary. A year that is not yet announced → `pending` (see definition above).

### Market rumor

A Telegram post claims OKX will halt BTC perpetuals. No primary cite → tier 3 / `rumor` (C). Alert for review only; no trade, no limit change. Later Reuters reports the same fact and quotes a named exchange official → upgrade to tier 2 / `secondary-verified` (B). Reminders may cite it; any trade or limit action still needs the owner's approval. If the exchange notice is fetched → tier 1 / `official` (A). Until then, reminders show the tier letter beside the status.
