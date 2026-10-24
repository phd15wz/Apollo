# docs

Design notes, decisions, and how-to guides for this workspace.

Keep each page short. Link to the code, skill, or tool it describes.

- [Exchange calendar coverage](exchange-calendar-coverage.md) — what `exchange_calendars` and `pandas_market_calendars` actually returned for the six stored exchanges. Nasdaq is an NYSE alias.
- [Rates and DXY feed](macro-rates-dxy.md) — daily Treasury yields (FRED) and a research-only DXY print as market-state inputs next to FOMC blackouts. Spec only; Isaac fills series IDs and the puller.

Hawkes-process usage for agents is in `skills/hawkes-process/SKILL.md`; the toolkit is `tools/hawkes/`.
