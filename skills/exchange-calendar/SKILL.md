# Exchange calendar

Look up exchange holidays, early closes, and late opens, and refresh the checked-in dataset. Use this when someone asks whether NYSE, Nasdaq, CME, HKEX, SSE, TSE/JPX, or KRX is closed or on a short session.

Read `docs/exchange-calendar-coverage.md` before treating a library as complete. Read `data/exchange_calendar/metadata.json` before treating the dataset as current.

## Dataset

`data/exchange_calendar/holidays.csv` is the current file. `data/exchange_calendar/versions/<dataset_version>/` keeps that build. Columns:

| Column | Meaning |
| --- | --- |
| `exchange` | `NYSE`, `CME`, `HKEX`, `SSE`, `JPX`, `KRX`. `Nasdaq` is an alias of `NYSE` and is not stored. |
| `date` | Session date `YYYY-MM-DD` |
| `event_type` | See the event types below |
| `open_time`, `close_time` | Exchange-local clock. Empty on a full closure. A CME time on the previous evening is `YYYY-MM-DDTHH:MM`. |
| `name` | Holiday or notice name when one was known |
| `source_url` | Page or library tree the row was taken from |
| `retrieved_at` | UTC timestamp of the refresh that wrote the row |
| `status` | `official`, `secondary-confirmed`, `unverified`, or `pending` |
| `note` | Provenance flags. Empty on most rows. Weaker-B market-wrap rows and CME clocks that still need a tier-A check are marked here. |

`JPX` is the Tokyo Stock Exchange cash calendar (`XTKS`). `CME` rows describe **CME equity index futures (Globex) hours only**, from `exchange_calendars` `CMES`. They are not a product calendar. Strategies on other CME products (interest-rate, FX, crypto futures) must not rely on these rows until a product column exists.

Event types:

- `full_closure` — no session that day. Hours are empty.
- `typhoon_closure` — HKEX cancelled every session because of a typhoon signal. Hours are empty.
- `weather_closure` — HKEX cancelled every session because of other severe weather, such as a Black Rainstorm Warning or Extreme Conditions (2023-09-08). Hours are empty.
- `early_close` — the session closed early. `open_time` and `close_time` are the hours that traded.
- `late_open` — the session opened late. `open_time` and `close_time` are the hours that traded.
- `typhoon_partial` — HKEX traded only part of the day because of a typhoon or rainstorm. `open_time` and `close_time` are required and give the hours that traded. It is not a closure. No row uses it yet because no partial-day notice has been fetched.
- `other` — anything else, such as a suspension that does not fit the types above. `name` is required and says what happened. CME 2025-01-09 (Carter mourning: abbreviated equity session then a same-day Globex reopen) uses this type. Hours stay empty; the fetched equity-index close is in `name`. Short equity-index Globex sessions that halt in the morning without a same-day reopen are `early_close`, not `other`.

`build.validate_rows` rejects unknown types, closures with hours, session rows without both hours, `other` rows without a name, and weather rows that are not backed by an official HKEX notice.

Status:

- `official` — a fetched exchange or regulator page states this fact. HKEX `typhoon_closure`, `weather_closure`, and `typhoon_partial` rows are allowed only at this status, and only with an `hkex.com.hk` notice URL.
- `secondary-confirmed` — `exchange_calendars` and an independent `pandas_market_calendars` calendar agree, a fetched news article cites the exchange notice, a fetched Mondovisione (or similar) copy of an exchange release, or a licensed Bloomberg settlement calendar confirmed an existing unverified `full_closure`. The mirror calendars do not count. A weaker-B market-wrap sentence stays B and is flagged in `note`. A terminal source has no URL; the dataset stores the label `Bloomberg settlement calendar (licensed terminal), retrieved 2026-10-13`. Mondovisione CME mirrors are never `official`.
- `unverified` — one library only, the libraries disagree, or the only `pandas_market_calendars` calendar is a mirror. Remaining KRX year-end closures not on that settlement calendar, KRX late opens, and disagreed CME rows stay in this bucket.
- `pending` — reserved for a future SSE or KRX year after an announcement says the schedule is not final. Do not create dated rows for a year that has not been announced.

History in the file starts at 2001-01-01 (more than ten years before 2026-10-11) and has a closure in every year from 2001 through 2025 for each exchange. Upcoming rows stop at the last year the chosen library can build, except SSE and KRX, which stop at 2026.

## When you need a date

1. Open `metadata.json`. If `last_refreshed` is more than 35 days before today, tell the user the file is stale and run the refresh below before relying on upcoming dates.
2. Call `load_confirmed(exchange=...)` (or `for_exchange` on a loaded frame). Nasdaq must be queried through those helpers; do not filter the CSV for `Nasdaq`. `Nasdaq` resolves to `NYSE`. An unknown exchange name raises.
3. No row does not by itself mean the market is open. Weekends are not stored. CME 2001-09-11 and 2001-09-12 are `secondary-confirmed` full closures from fetched Mondovisione copies of CME releases (tier B, not official). CME 2001-09-13 remains `unverified`. Every library calendar tested treats 2001-09-11 to 2001-09-14 as sessions. Do not present an `unverified` row as a confirmed closure, and do not describe Mondovisione as CME's own page. NYSE has closure rows for 2001-09-11 to 2001-09-14; Nasdaq uses those same rows. HKEX can still be halted for weather after the file was built. CME answers are equity-index Globex hours only.
4. Quote `status` and `source_url` with the answer. Do not describe `unverified` as confirmed. Do not describe `secondary-confirmed` as the exchange's own notice.
5. For HKEX weather, use a `typhoon_*` or `weather_closure` row only when `status` is `official`. A closure reconstructed from news stays `unverified` and must not be labeled with a weather type.

## Reading rows in code

Strategies and reminders must use `tools.exchange_calendar.load_confirmed()` (defined in `tools/exchange_calendar/query.py`). It returns only `official` and `secondary-confirmed` rows and drops `unverified` and `pending`. Do not read `holidays.csv` directly for scheduling. An unknown status value raises instead of passing through. An unknown exchange name on `for_exchange` or `load_confirmed(exchange=...)` raises. Nasdaq has no stored rows; query it only through those helpers.

Example:

```python
from tools.exchange_calendar import load_confirmed

confirmed = load_confirmed()
nasdaq = load_confirmed(exchange="Nasdaq")  # same rows as NYSE
```

`ALIASES` in `tools/exchange_calendar/exchanges.py` is `{"Nasdaq": "NYSE"}`. `resolve_exchange` and `for_exchange` apply it. `XNAS`, `NASDAQ`, and other library codes are not aliases and raise.

## SSE and KRX next year

China and Korea publish the next year's schedule around December. Do not fill the following year from `exchange_calendars`, from `pandas_market_calendars`, or from a rule of thumb. `exchange_calendars` 4.13.2 refuses SSE dates after 2026. Its KRX calendar will project through 2050, and `pandas_market_calendars` SSE uses guessed rules from 2027. None of that belongs in the dataset. Leave the year out until an official notice has been read and added to `tools/exchange_calendar/official.py`. If a row is ever added before the notice is final, its status is `pending`.

## Refresh

From the repository root, with `exchange_calendars==4.13.2` and `pandas_market_calendars==5.5.0` installed:

```bash
python3 -m tools.exchange_calendar.refresh
python3 -m tools.exchange_calendar.refresh --check
```

The refresh rebuilds every row from the libraries, reapplies the official snapshot in `tools/exchange_calendar/official.py` and the reported CME closures in the same file, and re-fetches the snapshot URLs. A failed fetch does not delete official rows and does not invent replacements. It is recorded in `fetch-log.json` and added to `open_items`.

Each fetched page is also searched for the dates the dataset lists for it (`tools/exchange_calendar/page_dates.py`). If a page names some of those dates but not others, the refresh exits 3 and writes only `fetch-log.json`. A list page with none of the known date layouts is recorded as an open item instead.

`retrieved_at` changes on every refresh. Edits to `name`, hours, `status`, `source_url`, or `event_type` on a date before today are written to `past-changes.json`, and the process exits 2. Review that file before accepting the refresh. `--check` does not rebuild; it warns when `last_refreshed` is older than 35 days and exits 1 if any disagreement is unresolved. Each version folder keeps `holidays.csv`, `metadata.json`, and `disagreements.csv`, and the folder is named with the refresh date.

Tests:

```bash
python3 -m pytest tools/exchange_calendar/tests -q
```

`disagreements.csv` has one row for each place where a library disagrees with a dataset closure or early close. `resolution` is one of:

- `resolved` — a fetched official fact disagrees with a library, and the pair is listed in `RESOLVED_OFFICIAL_CONFLICTS` in `official.py`. The dataset follows the official page.
- `labeled-unverified` — the libraries disagree and no official page settles the row. The row must be `unverified`.
- `unresolved` — anything else. `build_dataset` raises on it, so the build cannot write a dataset that hides it.

A library closure in a year whose fetched official list is complete, and that the list omits, is always `unresolved`. HKEX, CME, and KRX disagreements are never resolved by year.

## Do not

- Do not call `get_calendar` without `start` and `end` and then claim a ten-year history. The default window is shorter.
- Do not treat Nasdaq as a separate holiday calendar. It is an alias of NYSE. The CSV has no Nasdaq rows. Query Nasdaq only through `for_exchange` or `load_confirmed(exchange=...)`. The Nasdaq Trader page is only a check that Nasdaq's announced 2026 schedule equals NYSE.
- Do not say HKEX typhoon history is complete. `typhoon_closure` rows start at 2016-08-02 and only cover fetched notices.
- Do not answer a CME product question from the `CME` rows alone. Those rows are CME equity index futures (Globex) hours only. Say so, and do not apply them to interest-rate, FX, or crypto futures until a product column exists. Point at the coverage note.
- Do not add a typhoon or black-rainstorm row from a news article. Fetch the HKEX market-communications notice, or leave the row unverified and not typed as `typhoon_*` or `weather_closure`.
- Do not type a rainstorm closure as `typhoon_closure`. Use `weather_closure`.
- Do not present CME 2001-09-13 as confirmed. 2001-09-11 and 2001-09-12 are `secondary-confirmed` from Mondovisione CME-release mirrors; they are not `official`. Every tested CME library calendar still treats 2001-09-11 to 2001-09-14 as sessions.
- Do not mark HKEX half-days or KRX CSAT late opens as `official` without a fetched page that names the date. Circular CT/166/2025 names 2025-12-24, 2025-12-31, and 2026-02-16. The HKEX trading-hours page names no dates. The KRX pages were shells.
