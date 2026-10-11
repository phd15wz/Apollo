import pandas as pd
import pytest

from tools.exchange_calendar.build import COLUMNS
from tools.exchange_calendar.query import CONFIRMED_STATUSES, HOLIDAYS_PATH, load_confirmed


def _row(status: str, date: str = "2026-01-01") -> dict:
    return {
        "exchange": "NYSE",
        "date": date,
        "event_type": "full_closure",
        "open_time": "",
        "close_time": "",
        "name": "New Year's Day",
        "source_url": "https://example.test/holidays",
        "retrieved_at": "2026-10-11T00:00:00Z",
        "status": status,
    }


def test_load_confirmed_keeps_official_and_secondary_only():
    frame = pd.DataFrame(
        [
            _row("official", "2026-01-01"),
            _row("secondary-confirmed", "2026-01-02"),
            _row("unverified", "2026-01-03"),
            _row("pending", "2026-01-04"),
        ],
        columns=COLUMNS,
    )
    kept = load_confirmed(frame=frame)
    assert list(kept["date"]) == ["2026-01-01", "2026-01-02"]
    assert set(kept["status"]) == set(CONFIRMED_STATUSES)


def test_load_confirmed_rejects_unknown_status():
    frame = pd.DataFrame([_row("likely")], columns=COLUMNS)
    with pytest.raises(ValueError, match="unknown status"):
        load_confirmed(frame=frame)


def test_committed_dataset_has_no_unverified_or_pending_rows_after_filter():
    kept = load_confirmed(HOLIDAYS_PATH)
    assert not kept.empty
    assert set(kept["status"]) <= set(CONFIRMED_STATUSES)
    raw = pd.read_csv(HOLIDAYS_PATH, dtype=str, keep_default_na=False)
    assert len(kept) == int(raw["status"].isin(CONFIRMED_STATUSES).sum())
