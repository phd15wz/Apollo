# Rates and DXY market-state feed

Spec only. No puller and no stored history in this change. Isaac fills the FRED series IDs, the column schema, the refresh cadence, and the σ window, then adds the puller.

Committee decision, recorded here so the puller has a fixed brief. Reliability grades: [`data-source-reliability.md`](data-source-reliability.md).

## Purpose

Daily US Treasury yields and a dollar-index print are market-state inputs. They sit beside the exchange holiday calendar (`data/exchange_calendar`, read only through `load_confirmed()`) and beside FOMC blackout flags. A strategy uses them to gate risk on the same pass as those flags. Holiday rows stay in the exchange calendar. This feed only carries yields, the slope, the real yield, and the research DXY print.

## Sources

| Input | Source | Grade | Role |
| --- | --- | --- | --- |
| 2Y yield, 10Y yield, 2s10s slope, real yield | FRED (St. Louis Fed) | A / `official` | Live gate and backtest truth may use these once the series IDs are locked |
| DXY | Yahoo Finance via `yfinance` | Secondary, terms-of-service fragile | Research only |

The FRED API key stays in the secret store. The runtime name is `FRED_API_KEY` until Isaac records a different secret-store name. The value never goes in git, in a committed `.env`, or in a notebook.

Price rule in the reliability doc: market data used as truth comes from an exchange or a licensed feed. FRED's Treasury yields are an official Fed redistribution of H.15. The Yahoo dollar index is a vendor quote. Research may read it. Live orders and backtest truth need an official or licensed dollar source before DXY can stand alone.

## Series placeholders

Isaac locks the ids. This table is the shopping list, not an adopted schema.

| Concept | Source | Id | Notes |
| --- | --- | --- | --- |
| US 2-year Treasury yield | FRED | *(Isaac)* | Daily. Required. |
| US 10-year Treasury yield | FRED | *(Isaac)* | Daily. Required. |
| 2s10s slope | Derived, unless Isaac adopts a published FRED spread | *(Isaac)* | 10Y minus 2Y, in the same units as the two yields. Required. |
| Real yield | FRED | *(Isaac)* | Intended. One official real-yield series. Drop it only if no suitable official series exists, and say so in the schema. |
| DXY | Yahoo `yfinance` | `DX-Y.NYB` | Ticker is locked by this spec. Research only. |

`DX-Y.NYB` is Yahoo's symbol for the ICE U.S. Dollar Index. This spec uses that one ticker everywhere so research pulls and any later cache share a single string. `^DXY` is not a second series.

Store the pulled number and the ticker. Leave page HTML and bulk Yahoo dumps out of the repository.

Unchecked FRED names Isaac may confirm or replace (not adopted): `DGS2`, `DGS10`, `T10Y2Y`, `DFII10`.

## Outputs

Each adopted series emits, on each observation:

- **level** — the print, in the source's units (Isaac records percent versus decimal).
- **1-day change** — level minus the previous available observation.
- **5-day change** — level minus the observation five available prints earlier. Recommended definition: five prior observations, so weekends do not shrink the window. Isaac locks observation-count versus calendar days.
- **sharp-move** — true when the absolute 1-day change is greater than 2 standard deviations of recent 1-day changes. Both signs count. The window length, the minimum number of changes, and rolling versus expanding σ are Isaac's. The 2σ threshold stays until a later committee decision.

When history is too short to compute σ, `sharp_move` is false and the row records a reason. A short sample is not a sharp move.

## Hard gate and soft signal

The sharp-move flag is a **hard gate** in the same class as an FOMC blackout.

While the gate is on, using only rows already available at decision time:

- Allowed long size is half the size the existing risk caps would otherwise allow (Jessie's caps in `config/candidate_pool.yaml` and any tighter strategy cap). This spec adds no new numeric cap.
- A new long that would increase exposure waits until the gate clears. This spec does not define an exit algorithm for a position already above that half-size cap.

The live hard-gate set is the adopted FRED series (yields, slope, and the real yield once it has an id). Any one of those series tripping `sharp_move` is enough. DXY's flag is computed for research and stays out of the live gate until a non-Yahoo dollar source exists. A Yahoo-only print must not be the sole reason a live order is sent or blocked, and a backtest that needs DXY to reach its result is a research run, not backtest truth.

The gate is independent of `load_confirmed()` holidays and independent of the FOMC blackout flag. Either flag is sufficient for this action. This spec defines no stricter combined size.

Soft signals (score tilts, event-layer features, direction-specific reactions) wait until a backtest shows the hard gate is not only cutting winners. The first puller emits the flag and enforces the gate. It does not add a soft channel.

## No lookahead

Same spirit as the FOMC flags: a print is invisible until this feed could have known it.

- `obs_date` is the day the level refers to.
- `available_at` is the timestamp the print was public to this feed. A backtest or a live decision may read the row only at or after `available_at`.
- Stamp a value on the decision time when it became knowable. Do not copy a later vintage back onto an earlier `available_at`.
- 1-day and 5-day changes use only rows that were already available. They do not peek at a print that was still unpublished.
- Isaac chooses vintage-aware history versus a latest-only feed and writes that choice into the schema. FRED constant-maturity yields are often unrevised; the real-yield series may differ. Latest-only storage still keeps the original `available_at` of each print.

Refresh cadence and the clock that sets `available_at` (when the H.15 day is treated as public) are Isaac's.

## Storage

This repository keeps the spec. It does not keep the history. When a puller exists, bulk cache lives outside the git tree (same idea as `OKX_DATA_DIR` for OKX zips). A committed sample, if any, is a few rows Isaac adds with the schema.

## Open TODOs for Isaac

1. Lock FRED series IDs for the 2Y, the 10Y, and the real yield. Confirm whether 2s10s is computed here or taken from a FRED spread.
2. Lock the column schema: names, units, the session calendar behind 1-day and 5-day changes, and the vintage policy.
3. Set the refresh cadence and the rule for `available_at`.
4. Set the σ window, the minimum sample, and rolling versus expanding σ. Leave the threshold at 2σ.
5. Confirm the live hard-gate set stays "FRED series only" when the puller lands.
6. Record the secret-store name for the FRED API key if it is not `FRED_API_KEY`.
7. Choose the on-disk cache path outside this repo. No HTML and no bulk Yahoo dump in git.
