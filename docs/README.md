# docs

Design notes, decisions, and how-to guides for this workspace.

Keep each page short. Link to the code, skill, or tool it describes.

- [Exchange calendar coverage](exchange-calendar-coverage.md) — what `exchange_calendars` and `pandas_market_calendars` actually returned for the six stored exchanges. Nasdaq is an NYSE alias.
- [Rates and DXY feed](macro-rates-dxy.md) — FRED `DGS2`, `DGS10`, `T10Y2Y`, `DFII10`, and research-only Yahoo `DX-Y.NYB`. Live hard gate is `DGS10` and `DFII10`. Spec only; the puller is not in the tree yet.

Hawkes-process usage for agents is in `skills/hawkes-process/SKILL.md`; the toolkit is `tools/hawkes/`.
