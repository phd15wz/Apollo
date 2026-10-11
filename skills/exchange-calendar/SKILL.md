# Exchange calendar

Look up exchange holidays, early closes, and late opens, and refresh the checked-in dataset. Use this when someone asks whether NYSE, Nasdaq, CME, HKEX, SSE, TSE/JPX, or KRX is closed or on a short session.

Read `docs/exchange-calendar-coverage.md` before treating a library as complete. Read `data/exchange_calendar/metadata.json` before treating the dataset as current.

## Dataset

`data/exchange_calendar/holidays.csv` is the current file. `data/exchange_calendar/versions/<dataset_version>/` keeps that build. Columns:

| Column | Meaning |
| --- | --- |
| `exchange` | `NYSE`, `Nasdaq`, `CME`, `HKEX`, `SSE`, `JPX`, `KRX` |
| `date` | Session date `YYYY-MM-DD` |
| `event_type` | See the event types below |
| `open_time`, `close_time` | Exchange-local clock. Empty on a full closure. A CME time on the previous evening is `YYYY-MM-DDTHH:MM`. |
| `name` | Holiday or notice name when one was known |
| `source_url` | Page or library tree the row was taken from |
| `retrieved_at` | UTC timestamp of the refresh that wrote the row |
| `status` | `official`, `secondary-confirmed`, `unverified`, or `pending` |

`JPX` is the Tokyo Stock Exchange cash calendar (`XTKS`). `CME` is `exchange_calendars` `CMES` only, not every CME Group product.

Event types:

- `full_closure` — no session that day. Hours are empty.
- `typhoon_closure` — HKEX cancelled every session because of a typhoon signal. Hours are empty.
- `weather_closure` — HKEX cancelled every session because of other severe weather, such as a Black Rainstorm Warning or Extreme Conditions (2023-09-08). Hours are empty.
- `early_close` — the session closed early. `open_time` and `close_time` are the hours that traded.
- `late_open` — the session opened late. `open_time` and `close_time` are the hours that traded.
- `typhoon_partial` — HKEX traded only part of the day because of a typhoon or rainstorm. `open_time` and `close_time` are required and give the hours that traded. It is not a closure. No row uses it yet because no partial-day notice has been fetched.
- `other` — anything else, such as a suspension that does not fit the types above. `name` is required and says what happened.

`build.validate_rows` rejects unknown types, closures with hours, session rows without both hours, `other` rows without a name, and weather rows that are not backed by an official HKEX notice.

Status:

- `official` — a fetched exchange or regulator page states this fact. HKEX `typhoon_closure`, `weather_closure`, and `typhoon_partial` rows are allowed only at this status, and only with an `hkex.com.hk` notice URL.
- `secondary-confirmed` — `exchange_calendars` and an independent `pandas_market_calendars` calendar agree. The mirror calendars do not count.
- `unverified` — one library only, the libraries disagree, or the only `pandas_market_calendars` calendar is a mirror. KRX is in this bucket.
- `pending` — reserved for a future SSE or KRX year after an announcement says the schedule is not final. Do not create dated rows for a year that has not been announced.

History in the file starts at 2001-01-01 (more than ten years before 2026-10-11) and has a closure in every year from 2001 through 2025 for each exchange. Upcoming rows stop at the last year the chosen library can build, except SSE and KRX, which stop at 2026.

## When you need a date

1. Open `metadata.json`. If `last_refreshed` is more than 35 days before today, tell the user the file is stale and run the refresh below before relying on upcoming dates.
2. Filter `holidays.csv` to the exchange and date.
3. No row does not by itself mean the market is open. Weekends are not stored. CME 2001-09-11 has no row because the calendars that were tested leave it open; NYSE and Nasdaq do have closure rows. HKEX can still be halted for weather after the file was built.
4. Quote `status` and `source_url` with the answer. Do not describe `unverified` as confirmed. Do not describe `secondary-confirmed` as the exchange's own notice.
5. For HKEX weather, use a `typhoon_*` or `weather_closure` row only when `status` is `official`. A closure reconstructed from news stays `unverified` and must not be labeled with a weather type.

## Reading rows in code

Strategies and reminders must use `tools.exchange_calendar.load_confirmed()` (defined in `tools/exchange_calendar/query.py`). It returns only `official` and `secondary-confirmed` rows and drops `unverified` and `pending`. Do not read `holidays.csv` directly for scheduling. An unknown status value raises instead of passing through.

Example:

```python
from tools.exchange_calendar import load_confirmed

confirmed = load_confirmed()
```

## SSE and KRX next year

China and Korea publish the next year's schedule around December. Do not fill the following year from `exchange_calendars`, from `pandas_market_calendars`, or from a rule of thumb. `exchange_calendars` 4.13.2 refuses SSE dates after 2026. Its KRX calendar will project through 2050, and `pandas_market_calendars` SSE uses guessed rules from 2027. None of that belongs in the dataset. Leave the year out until an official notice has been read and added to `tools/exchange_calendar/official.py`. If a row is ever added before the notice is final, its status is `pending`.

## Refresh

From the repository root, with `exchange_calendars==4.13.2` and `pandas_market_calendars==5.5.0` installed:

```bash
python3 -m tools.exchange_calendar.refresh
python3 -m tools.exchange_calendar.refresh --check
```

The refresh rebuilds every row from the libraries, reapplies the official snapshot in `tools/exchange_calendar/official.py`, and re-fetches the snapshot URLs. A failed fetch does not delete official rows and does not invent replacements. It is recorded in `fetch-log.json` and added to `open_items`.

`retrieved_at` changes on every refresh. Edits to `name`, hours, `status`, `source_url`, or `event_type` on a date before today are written to `past-changes.json`, and the process exits 2. Review that file before accepting the refresh. `--check` does not rebuild; it warns when `last_refreshed` is older than 35 days and exits 1 if any disagreement is unresolved.

Tests:

```bash
python3 -m pytest tools/exchange_calendar/tests -q
```

Unresolved rows in `disagreements.csv` fail those tests. A library and a fetched official source are allowed to disagree only when `resolution` is `resolved` and the dataset follows the official page.

## Do not

- Do not call `get_calendar` without `start` and `end` and then claim a ten-year history. The default window is shorter.
- Do not treat Nasdaq as a separate holiday calendar. Both libraries alias it to NYSE.
- Do not answer a CME product question from the `CME` rows alone. Say the row is the `CMES` calendar and point at the coverage note.
- Do not add a typhoon or black-rainstorm row from a news article. Fetch the HKEX market-communications notice, or leave the row unverified and not typed as `typhoon_*` or `weather_closure`.
- Do not type a rainstorm closure as `typhoon_closure`. Use `weather_closure`.
