# Exchange calendar coverage

Probed on 2026-10-11 with `exchange_calendars` 4.13.2 and `pandas_market_calendars` 5.5.0. Counts below come from instantiating those calendars. Official pages named in a sentence were fetched the same day. `tools/exchange_calendar/tests/test_coverage.py` recomputes the identity checks, the 2001–2026 closure counts, and the HKEX disagreement dates.

## How this was measured

`exchange_calendars.get_calendar(code, start=, end=)` builds `sessions`. A closure is a Monday–Friday in the requested window that is not a session. Early closes are `early_closes`. Local hours come from the schedule, converted to the calendar timezone.

`pandas_market_calendars.get_calendar(code).schedule(start, end)` is the same test: a weekday missing from the schedule is a closure. Close clocks are `market_close` in the exchange timezone. `MarketCalendar.early_closes()` was not used. On NYSE for 2016–2026 it raises `ValueError: iterator too short`.

Calling `get_calendar("XNYS")` with no start or end, on this probe day, built only 2006-10-11 through 2027-10-11. A ten-year history needs an explicit start. The dataset window starts at 2001-01-01 so that 2001-09-11 is inside it.

`pandas_market_calendars` mirrors many `exchange_calendars` calendars (`XNYS`, `XHKG`, `XSHG`, `XTKS`, `XKRX`, `CMES`). A mirror is not a second source. The comparison below uses native `pandas_market_calendars` classes where they exist.

## Codes that were actually opened

| Exchange | exchange_calendars | Class | pandas_market_calendars used for comparison | Independent of exchange_calendars? |
| --- | --- | --- | --- | --- |
| NYSE | `XNYS` (alias `NYSE`) | `XNYSExchangeCalendar` | native `NYSE` | Yes |
| Nasdaq | `XNAS` (alias `NASDAQ`) | `XNYSExchangeCalendar` | `NASDAQ`, which is native `NYSEExchangeCalendar` | No distinct Nasdaq calendar |
| CME | `CMES` (alias `CME`) | `CMESExchangeCalendar` | native `CME_Equity` and `CME Globex Equity` | The two native calendars are independent of each other and of CMES. There is no class named `CME`. |
| HKEX | `XHKG` (alias `HKEX`) | `XHKGExchangeCalendar` | native `HKEX` | Yes. `XHKG` in pandas_market_calendars is only the mirror. |
| SSE | `XSHG` (alias `SSE`) | `XSHGExchangeCalendar` | native `SSE` | Yes |
| JPX (TSE) | `XTKS` (alias `JPX`) | `XTKSExchangeCalendar` | native `JPX` | Yes |
| KRX | `XKRX` | `XKRXExchangeCalendar` | `XKRX` mirror only | No |

## NYSE

`XNYS` instantiated for 1970-01-01 through 2030-12-31 with no year in that span lacking a weekday closure. For 2001-01-01 through 2026-12-31, weekday closures are 246 in both libraries, and the sets are equal. For 2016–2026 both libraries have 23 early closes, all at 13:00 America/New_York against a 16:00 regular close.

The NYSE holiday page (`https://www.nyse.com/markets/hours-calendars`) was fetched and lists full closures for 2026, 2027, and 2028 plus early closes at 1:00 p.m. ET on 2026-11-27, 2026-12-24, 2027-11-26, 2028-07-03, and 2028-11-24. Those dates match `XNYS`. The page does not publish 2001–2025. Those rows stay `secondary-confirmed`.

2001-09-11, 2001-09-12, 2001-09-13, and 2001-09-14 are closures in both libraries. 2001-09-10 and 2001-09-17 are sessions.

## Nasdaq

`XNAS` is the same class as `XNYS`. Their session indexes are equal for 2000-01-01 through 2027-12-31, and their early-close indexes are equal. `pandas_market_calendars` `NASDAQ` is `NYSEExchangeCalendar`, the same object family as `NYSE`.

The Nasdaq Trader 2026 holiday schedule (`https://www.nasdaqtrader.com/trader.aspx?id=calendar`) was fetched. Its closures and 1:00 p.m. early closes match the shared calendar. No other Nasdaq year was on that page, so only 2026 Nasdaq rows are `official`. There is no tested difference between Nasdaq and NYSE in either library.

Because `NASDAQ` in `pandas_market_calendars` is the NYSE class, Nasdaq is never counted as an independent second source. Every Nasdaq row outside the 2026 official set is `unverified`, even where `XNYS` and `XNAS` agree.

## CME

`exchange_calendars` alias `CME` is `CMES`. `pandas_market_calendars` has no calendar named `CME`. Native calendars that were opened: `CME_Equity` and `CME Globex Equity`. The mirror `CMES` matches `exchange_calendars` and was not treated as confirmation.

Weekday closures from 2001-01-01 through 2026-12-31:

| Calendar | Closures |
| --- | --- |
| `CMES` | 79 |
| `CME_Equity` | 71 |
| `CME Globex Equity` | 67 |

`CME_Equity` is missing eight `CMES` closures in that window: 2010-04-02, 2012-04-06, 2015-04-03, 2021-04-02, 2023-04-07, 2024-03-29, 2025-04-18, 2026-04-03. `CME Globex Equity` is missing those plus 2004-06-11, 2007-01-02, 2018-12-05, and 2025-01-09. The dataset marks a `CMES` closure `secondary-confirmed` only when both native calendars are also closed. The other `CMES` closures are `unverified`, including 2027-03-26.

For 2016–2026, `CMES` has 83 early closes at 12:00 America/Chicago. Session open on those days is the previous local evening (stored as `YYYY-MM-DDTHH:MM`). `CME_Equity` in the same window has 99 sessions whose close is not 16:00 (94 at 12:00 and 5 at 08:15). Early-close rows are `secondary-confirmed` only when both native calendars show the same close clock. The rest are `unverified`. In this build that split is 82 secondary-confirmed and 123 unverified across 2001–2027.

2001-09-11 through 2001-09-14 are sessions in `CMES`, `CME_Equity`, and `CME Globex Equity`. The dataset still carries three reported CME closures for that week, all `unverified`, because a fetched source says the US stock exchanges were closed through 2001-09-17 (`https://en.wikipedia.org/wiki/September_11_attacks`): 2001-09-11 `full_closure`, 2001-09-12 `full_closure` (Chicago exchanges closed), and 2001-09-13 `other` (equity-index products closed). The 9/12 and 9/13 details were not found in any fetched source. 2001-09-14 has no row. 2001-12-25 is a closure in all three and is `secondary-confirmed`.

The CME early-close clocks for 2026-11-27, 2026-12-24, and 2027-11-26 are set to 12:15 in the dataset because `CME Globex Equity` says 12:15. `CMES` and `CME_Equity` say 12:00. The CME Group pages returned HTTP 403 to every request in this run, so these rows stay `unverified`. The Juneteenth 2027 (2027-06-18) row is an unverified `early_close` at 12:00, the value both native calendars give. `CMES` treats that session as a normal day. The same native pattern holds for the 2026-06-19 session, and earlier Juneteenth dates were not checked. None of those dates are added.

The CME Group holiday page (`https://www.cmegroup.com/trading-hours.html`) was fetched. It is a product-level hour table, not a single session calendar, and it was not parsed into rows.

## HKEX

`XHKG` instantiated for 1970–2030. For 2001-01-01 through 2026-12-31, weekday closures are 375 in `XHKG` and 373 in native `HKEX`.

Only in `XHKG`: 2020-10-13, 2021-04-06, 2021-10-13, 2024-09-06.

Only in native `HKEX`: 2023-09-01, 2023-09-08.

`XHKG` treats 2023-09-08 as a normal session (09:30–16:00 Asia/Hong_Kong). The fetched HKEX notice says all securities and derivatives sessions that day were cancelled because a Black Rainstorm Warning and Extreme Conditions remained in force: `https://www.hkex.com.hk/News/Market-Communications/2023/2309083news?sc_lang=en`.

2023-09-08 is `weather_closure` / `official`, not a typhoon closure. Final typhoon cancellation notices were also fetched for 2016-08-02, 2016-10-21, 2017-08-23 (Signal No. 10), 2020-10-13, 2021-10-13, 2023-07-17, 2023-09-01 (Signal No. 8), and 2024-09-06. Those eight dates are `typhoon_closure` / `official`. Where a library still has the day open, `disagreements.csv` records a resolved conflict. 2021-04-06 is closed only in `XHKG`. No notice was fetched, so that row stays `unverified` `full_closure`.

For 2016–2026, `XHKG` has 23 early closes and native `HKEX` has none: every schedule close is 16:00. The dataset has 52 HKEX early-close rows from 2001 through 2027, all `unverified`. Before 2011-03-07 the library’s early close is 10:00–12:30 (2001-12-24). From 2012-12-24 onward in this build it is 09:30–12:00. Not every year has a weekday early close.

HKEX half-days were checked against the HKEX Securities trading-hours page (`https://www.hkex.com.hk/Services/Trading-hours-and-Severe-Weather-Arrangements/Trading-Hours/Securities-Market?sc_lang=en`), fetched on 2026-10-11. The page gives a half-day session (morning to 12:00 noon, then a closing auction) and says there is no extended morning or afternoon session on the eves of Christmas, New Year, and Lunar New Year. It does not list dates. The HKEX derivatives calendar lists 2026 and 2027 eve dates, but only for derivatives. The HKEX early closes in the dataset fall on those eve dates, but no fetched securities page dates them, so they stay `unverified`.

Both libraries contain commented-out morning-only weather dates (for example 2008-06-25 and 2013-09-23 in the installed sources). Those dates are not sessions with a short close, and they were not added. HKEX Severe Weather Trading, which the exchange introduced on 23 September 2024, is not modeled. No full-year HKEX holiday calendar was fetched, so ordinary holidays are `secondary-confirmed` only where the two libraries agree.

## SSE

`XSHG` holidays are recorded from 1991 through 2026. `get_calendar("XSHG", start="1970-01-01", ...)` and `start="1990-01-01"` raise `ValueError` saying holidays are only recorded back to 1991. `end="2027-01-01"` raises `ValueError` saying holidays are only recorded to 2026. There are no early closes and no late opens. For 2001-01-01 through 2026-12-31 both libraries have the same 480 weekday closures.

Native `SSE` in `pandas_market_calendars` sets `start_year = 2027` and the source comment says those later rules are guesses. A schedule request that crosses into 2027 does return sessions, including a closure on 2027-01-01. Those guesses were not copied.

Fetched official lists, expanded to weekdays, match the library:

- 2024 and 2025 on `https://english.sse.com.cn/start/trading/schedule/`
- 2026 on the SSE notice dated 2025-12-22, `https://www.sse.com.cn/disclosure/announcement/general/c/c_20251222_10802507.shtml`

Those rows are `official`. Earlier SSE rows are `secondary-confirmed`. 2027 is absent.

## JPX (Tokyo Stock Exchange)

`XTKS.bound_min()` is 1997-01-01. A start before that raises `ValueError`. There are no early closes and no late opens. The close is 15:00 Asia/Tokyo until 2024-11-05 and 15:30 from that date. For 2001-01-01 through 2026-12-31 both libraries have the same 419 weekday closures.

The JPX English holiday page (`https://www.jpx.co.jp/english/corporate/about-jpx/calendar/`) was fetched. Its 2026 and 2027 weekday holidays, including 2026-09-22 and 2026-12-31, match `XTKS`. Those rows are `official`. Earlier rows are `secondary-confirmed`.

2020-10-01 is closed in both libraries. The `XTKS` docstring calls it an equity-trading-system failure. That comment is not the dataset name; the row is named `Closure` because the date is not on the regular named-holiday calendar and no JPX notice for that day was fetched.

## KRX

`XKRX` in `pandas_market_calendars` is the mirror class in `pandas_market_calendars.calendars.mirror`. Agreement with `exchange_calendars` is the same implementation twice. Every KRX row is `unverified`.

The installed `XKRXExchangeCalendar` docstring says holidays were hardcoded for 1986–2019. `_latest_precomputed_year` returns 2050, and a calendar ending in 2030 does build, including 2027 sessions. 2027 was not copied. Korea announces the next year around December; as of 2026-10-11 that notice is not in this snapshot.

`late_opens` on a 2024-01-02 `XKRX` calendar is empty, while that session opens at 10:00 Asia/Seoul. The mirror opens at 09:00. The dataset therefore records late opens from the schedule, not from `late_opens`. From 2001 through 2026 there are 47 of them, all at 10:00. January rows are the first business day of the year (close unchanged: 15:00, then 15:30 from 2016-08-01). Other rows follow the library’s CSAT offset, which also moves the close (16:00, then 16:30). The last of those non-January rows is 2020-12-03. Years 2021–2026 have only the January late open. That stop was not confirmed with KRX. The 2021 CSAT day and 2025-11-13 were not added: the Korea Exchange holiday and notice pages fetched in this run (`https://open.krx.co.kr/contents/MKD/01/0110/01100305/MKD01100305.jsp`, `https://global.krx.co.kr/contents/GLB/03/0302/0302010000/GLB0302010000.jsp`) returned page shells with a year picker and no dates, and the Korean notice pages were not reachable in a usable form.

`https://open.krx.co.kr/contents/MKD/01/0110/01100305/MKD01100305.jsp` and the KIND market calendar were fetched. Both are shells with a year picker and no holiday dates in the response. No KRX row is `official`.

## Pages that returned dates

NYSE 2026–2028, Nasdaq Trader 2026, JPX 2026–2027, SSE English 2024–2025, SSE notice for 2026, and the nine HKEX cancellation notices listed above were fetched and parsed on 2026-10-11. A later `refresh` re-check is stored in `data/exchange_calendar/fetch-log.json`. On that re-check the two SSE URLs timed out during the TLS handshake. Official SSE rows from the earlier successful fetch were kept, and the timeout is listed in `metadata.json` `open_items`. The other snapshot URLs still returned the expected phrase.

## What this file does not claim

HKEX typhoon history starts at 2016-08-02. Before 2016 no typhoon notice was fetched, so the dataset has no typhoon rows for those years and cannot be used to say the market traded through pre-2016 typhoons. Between 2016 and 2024 only the fetched notice dates are in the dataset.

Lunch breaks are not in the dataset. Partial HKEX weather sessions that the libraries only mention in comments are not in the dataset. CME hours are the `CMES` session, not a full product calendar. `secondary-confirmed` means the two library implementations agreed. It does not mean the exchange website was read for that historical date.
