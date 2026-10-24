# Rates and DXY market-state feed

Spec only. Series, units, clocks, and the live gate are locked. The puller is not in this repository yet.

Committee decision, with Isaac's series locks and Jessie's gate and `available_at` overrides. Reliability grades: [`data-source-reliability.md`](data-source-reliability.md).

## Purpose

Daily US Treasury yields and a dollar-index print are market-state inputs. They sit beside the exchange holiday calendar (`data/exchange_calendar`, read only through `load_confirmed()`) and beside FOMC blackout flags. A strategy uses them to gate risk on the same pass as those flags. Holiday rows stay in the exchange calendar. This feed carries the four FRED series below and the research DXY print.

## Sources

| Input | Source | Grade | Role |
| --- | --- | --- | --- |
| 10Y yield `DGS10`, 10Y TIPS real yield `DFII10` | FRED (St. Louis Fed) | A / `official` | Live hard gate and backtest truth |
| 2Y yield `DGS2`, 2s10s `T10Y2Y` | FRED | A / `official` | Pulled and flagged; research only |
| DXY | Yahoo Finance via `yfinance`, ticker `DX-Y.NYB` | Secondary, terms-of-service fragile | Research only |

The FRED API key is `FRED_API_KEY` in the secret store. The value never goes in git, in a committed `.env`, or in a notebook.

Price rule in the reliability doc: market data used as truth comes from an exchange or a licensed feed. FRED's Treasury yields are an official Fed redistribution of H.15. The Yahoo dollar index is a vendor quote. Research may read it. Live orders and backtest truth use the FRED live-gate series. DXY stays a research print until an official or licensed dollar source is wired.

## Series

| Concept | Id | Role |
| --- | --- | --- |
| US 2-year Treasury constant-maturity yield | `DGS2` | Research. Sharp-move flag is computed. |
| US 10-year Treasury constant-maturity yield | `DGS10` | Live hard gate. |
| 2s10s slope | `T10Y2Y` | Research. Published FRED spread. Sharp-move flag is computed. |
| 10-year TIPS real yield | `DFII10` | Live hard gate. |
| ICE U.S. Dollar Index | `DX-Y.NYB` | Research. Yahoo ticker is locked. |

`T10Y2Y` is the stored slope. The puller does not replace it with `DGS10 − DGS2`. When `DGS2`, `DGS10`, and `T10Y2Y` all have a level on the same `obs_date`, assert `|T10Y2Y − (DGS10 − DGS2)| ≤ 0.01` (1 basis point, in percent units). A failed assert stops that day's refresh. The recomputed difference is not written as the series.

`DX-Y.NYB` is the only DXY ticker. Research pulls and the cache share that string. Store the pulled number and the ticker. Leave page HTML and bulk Yahoo dumps out of the repository.

`DGS2` and `T10Y2Y` stay research-only until a measurement shows how often a four-series 2σ rule would have fired. That measurement is not a second live gate.

## Units and changes

FRED levels are percent, as FRED publishes them (`4.25`, not `0.0425`). Changes are in those same percentage points. The DXY level is the Yahoo index print; its changes are in index points.

On each series, for each observation:

- **level** — the print.
- **1-day change** — level minus the previous available observation.
- **5-day change** — level minus the observation five available prints earlier.

Both windows are observation counts (prior available prints). They are not calendar days. A weekend or an NYSE closure does not shrink the count.

## Sharp move

`sharp_move` uses a rolling standard deviation of **signed** 1-day changes. Expanding σ is not used.

For the print under test, σ is the standard deviation of the preceding signed 1-day changes: at most the last 60, and at least 40. The current change is not part of its own window. It joins the window on the next print.

`sharp_move` is true when at least 40 preceding signed 1-day changes exist and the absolute value of the current 1-day change is greater than 2σ. Both signs count. The threshold stays 2σ.

With fewer than 40 preceding changes, `sharp_move` is false and the row records a short sample.

Signed changes are the scale because a standard deviation of absolute 1-day changes fired on about 20% of days since 2024, too often for a hard gate in the FOMC-blackout class. Signed σ targets about 5% of days.

## Hard gate and soft signal

The sharp-move flag on the live set is a **hard gate** in the same class as an FOMC blackout.

Live hard-gate set: `DGS10` and `DFII10` only. Either series tripping `sharp_move`, on a row already available at decision time, turns the gate on.

Research flags, computed and stored, that do not turn the gate on:

- `DGS2`
- `T10Y2Y`
- `DX-Y.NYB`

A Yahoo-only print is never the sole reason a live order is sent or blocked. A backtest that needs `DGS2`, `T10Y2Y`, or DXY to reach its result is a research run, not backtest truth.

While the gate is on:

- Allowed long size is half the size the existing risk caps would otherwise allow (Jessie's caps in `config/candidate_pool.yaml` and any tighter strategy cap). This spec adds no new numeric cap.
- A new long that would increase exposure waits until the gate clears. This spec does not define an exit algorithm for a position already above that half-size cap.

The gate is independent of `load_confirmed()` holidays and independent of the FOMC blackout flag. Either flag is sufficient for this action. This spec defines no stricter combined size.

Soft signals (score tilts, event-layer features, direction-specific reactions) wait until a backtest shows the hard gate is not only cutting winners. The first puller emits the flags and enforces the `DGS10` / `DFII10` gate. Soft channels stay out of that puller.

## No lookahead

Same spirit as the FOMC flags: a print is invisible until `available_at`.

- `obs_date` is the day the level refers to.
- A backtest or a live decision may read the row only at or after `available_at`.
- 1-day changes, 5-day changes, and σ use only rows that were already available.

NY business days are NYSE sessions. Full closures come from `load_confirmed()` only. Early-close sessions still count as business days. Weekend dates and full closures are not pull days and are not "next session" dates.

**FRED.** Pull once per NY business day after 16:15 America/New_York (H.15 window). For obs_date T, once that day's print is in hand from that pull, `available_at` is 09:30 America/New_York on the next NYSE session. The print is hidden for the rest of day T, and the gate can fire at the next cash open. A print first fetched after that 09:30 keeps `available_at` at the fetch time, so a late pull is not stamped back to the open.

**DXY.** `available_at` is the Yahoo bar close timestamp expressed in America/New_York. The flag stays research-only.

**Vintage.** A latest-only cache is the store for `DGS2`, `DGS10`, `T10Y2Y`, and `DFII10` (these series are rarely revised). Each print keeps the `available_at` written when it was first stored. A later refresh may replace the level with FRED's latest value and must leave that original `available_at` in place.

## Storage

This repository keeps the spec. History and Yahoo payloads stay outside the git tree.

Cache path: `$MACRO_DATA_DIR` when set. When unset, `~/.cache/apollo/macro`, the sibling of the default OKX cache `~/.cache/apollo/okx`. No FRED key, no Yahoo HTML, and no bulk dump in the repository.
