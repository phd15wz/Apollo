# Exchange calendar

Builds `data/exchange_calendar/holidays.csv` for NYSE, CME, HKEX, SSE, JPX (Tokyo Stock Exchange), and KRX. Nasdaq is an alias of NYSE and is not stored.

The base layer is `exchange_calendars` 4.13.2. `pandas_market_calendars` 5.5.0 is used only as an independent check where it has its own calendar. Official pages that were fetched on 2026-10-11 live in `official.py` and override the libraries. Coverage, including calendars that do not exist or do not agree, is in `docs/exchange-calendar-coverage.md`. Agents should follow `skills/exchange-calendar/SKILL.md`.

## Run

From the repository root:

```bash
python3 -m pip install -r requirements.txt
python3 -m tools.exchange_calendar.refresh
python3 -m pytest tools/exchange_calendar/tests -q
```

`python3 -m tools.exchange_calendar.refresh --check` reports staleness (warning after 35 days) and exits 1 when `disagreements.csv` has an unresolved row. It does not rebuild.

A normal refresh rewrites `holidays.csv`, `metadata.json` (`last_refreshed`), `disagreements.csv`, `fetch-log.json`, `last-diff.json`, and `versions/<dataset_version>/`. If a date before today changes in any column other than `retrieved_at`, the process also writes `past-changes.json` and exits 2. The new file is still written so the diff can be reviewed.

Live fetches that fail stay in `metadata.json` under `open_items`. Official rows from the snapshot are kept. The refresh does not scrape a new SSE or KRX December notice; add that notice to `official.py` after reading it. Do not copy library projections for those future years.

## Columns

`exchange`, `date`, `event_type`, `open_time`, `close_time`, `name`, `source_url`, `retrieved_at`, `status`.

`event_type` is `full_closure`, `typhoon_closure`, `weather_closure`, `early_close`, `late_open`, `typhoon_partial`, or `other`. The three closure types have empty hours. `early_close`, `late_open`, and `typhoon_partial` must carry both `open_time` and `close_time`. `other` must have a name. `build.validate_rows` enforces this. The definitions are in `skills/exchange-calendar/SKILL.md`.

`status` is `official`, `secondary-confirmed`, `unverified`, or `pending`. Scheduling code must read the file through `load_confirmed()` (`tools/exchange_calendar/query.py`), which keeps only `official` and `secondary-confirmed`. `load_confirmed(exchange="Nasdaq")` returns the confirmed NYSE rows.

`CME` rows are the `CMES` calendar. `JPX` rows are TSE (`XTKS`). Nasdaq is `ALIASES["Nasdaq"] = "NYSE"`. HKEX typhoon and weather rows exist only for fetched HKEX notices.

## Tests

`tests/test_quality.py` checks required fields, closure names, each exchange's library closures in every year from 2001, disagreement resolutions, the 2001-09 CME rows and the 2023-09-08 HKEX spot check, typhoon provenance, the SSE/KRX 2027 omission, staleness relative to today, and past-date diffs. `tests/test_page_dates.py` checks the official-page date parser and the refresh exit code for a disagreeing page. `tests/test_query.py` checks `load_confirmed`, including that Nasdaq queries equal NYSE and that the CSV has no Nasdaq rows.

`tests/test_coverage.py` recomputes the library facts cited in the coverage note.
