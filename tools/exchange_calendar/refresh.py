"""Refresh the exchange-holiday dataset and flag edits to past dates.

Usage from the repository root:

    python -m tools.exchange_calendar.refresh
    python -m tools.exchange_calendar.refresh --check

Exit status:

- 0: the dataset was written and nothing needs review.
- 1: the build or the check failed, for example an unresolved disagreement.
- 2: a past date (date < today) was added, removed, or changed. The new dataset is
  still written so the diff can be reviewed. ``retrieved_at`` alone does not count.
- 3: a fetched official page does not name a date the dataset lists for it. Only
  ``fetch-log.json`` is written, so the dataset cannot silently follow a page that
  disagrees with it.

``--check`` does not rebuild; it reports staleness and fails if any
library/official disagreement is unresolved.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from tools.exchange_calendar.build import build_dataset
from tools.exchange_calendar.official import (
    FETCH_CHECKS,
    nasdaq_check_events,
    nasdaq_nyse_mismatches,
    official_events,
)
from tools.exchange_calendar.page_dates import check_page, normalise
from tools.exchange_calendar.quality import (
    assert_no_unresolved,
    diff_datasets,
    dataset_metadata,
    staleness_warning,
    write_json,
)

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data" / "exchange_calendar"
HOLIDAYS_PATH = DATA_DIR / "holidays.csv"
DISAGREEMENTS_PATH = DATA_DIR / "disagreements.csv"
METADATA_PATH = DATA_DIR / "metadata.json"
FETCH_LOG_PATH = DATA_DIR / "fetch-log.json"
DIFF_PATH = DATA_DIR / "last-diff.json"
PAST_CHANGES_PATH = DATA_DIR / "past-changes.json"


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _log_nasdaq_nyse_mismatches() -> None:
    """Warn if Nasdaq's own 2026 page disagrees with NYSE. Nasdaq is not stored."""
    mismatches = nasdaq_nyse_mismatches()
    if not mismatches:
        return
    print("NASDAQ/NYSE SCHEDULE MISMATCH — Nasdaq is an NYSE alias; these dates differ:", file=sys.stderr)
    for message in mismatches:
        print(f"  {message}", file=sys.stderr)


def fetch_official_pages(timeout: float = 25.0) -> list[dict]:
    """Re-check snapshot URLs and the dates each page names.

    A transport failure does not drop official rows. A page that loads but does not
    name a date the dataset lists for it is recorded in ``dates_missing``.
    """
    by_url: dict[str, list] = {}
    for event in list(official_events()) + nasdaq_check_events():
        if "://" not in event.source_url:
            continue
        by_url.setdefault(event.source_url, []).append(event)
    results = []
    for url, phrase in FETCH_CHECKS:
        item = {"url": url, "phrase": phrase, "ok": False, "status": None, "error": ""}
        request = urllib.request.Request(url, headers={"User-Agent": "exchange-calendar-refresh"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8", "replace")
                item["status"] = getattr(response, "status", 200)
                item["ok"] = phrase.lower() in body.lower()
                if not item["ok"]:
                    item["error"] = "expected phrase not found"
                check = check_page(url, normalise(body), by_url.get(url, []))
                item["dates_expected"] = check.expected
                item["dates_matched"] = check.matched
                item["dates_missing"] = check.missing
                item["dates_unparsed"] = check.unparsed
        except urllib.error.HTTPError as exc:
            item["status"] = exc.code
            item["error"] = str(exc)
        except Exception as exc:  # noqa: BLE001 - record any transport failure
            item["error"] = f"{type(exc).__name__}: {exc}"
        results.append(item)
    return results


def _read_holidays(path: Path) -> pd.DataFrame | None:
    if not path.exists():
        return None
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    return frame


def refresh(*, check_only: bool = False) -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if check_only:
        if not METADATA_PATH.exists() or not DISAGREEMENTS_PATH.exists():
            print("dataset has not been built", file=sys.stderr)
            return 1
        metadata = json.loads(METADATA_PATH.read_text(encoding="utf-8"))
        stale = staleness_warning(metadata["last_refreshed"])
        disagreements = pd.read_csv(DISAGREEMENTS_PATH, dtype=str, keep_default_na=False)
        assert_no_unresolved(disagreements)
        _log_nasdaq_nyse_mismatches()
        print(f"last_refreshed={metadata['last_refreshed']} stale={stale}")
        return 0

    retrieved_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    _log_nasdaq_nyse_mismatches()
    frame, disagreements = build_dataset(retrieved_at=retrieved_at)
    assert_no_unresolved(disagreements)
    fetch_log = fetch_official_pages()
    date_failures = [item for item in fetch_log if item.get("dates_missing")]
    if date_failures:
        write_json(FETCH_LOG_PATH, fetch_log)
        for item in date_failures:
            print(
                f"official page {item['url']} does not name {item['dates_missing']} "
                "that the dataset lists for it",
                file=sys.stderr,
            )
        return 3
    previous = _read_holidays(HOLIDAYS_PATH)
    as_of = _today()
    if previous is None:
        diff = {
            "as_of": as_of,
            "past_change_count": 0,
            "past": {"added": [], "removed": [], "changed": []},
            "future": {"added": [], "removed": [], "changed": []},
            "initial_build": True,
        }
    else:
        diff = diff_datasets(previous, frame, as_of=as_of)

    frame.to_csv(HOLIDAYS_PATH, index=False)
    disagreements.to_csv(DISAGREEMENTS_PATH, index=False)
    metadata = dataset_metadata(frame, retrieved_at=retrieved_at, fetch_log=fetch_log)
    write_json(METADATA_PATH, metadata)
    write_json(FETCH_LOG_PATH, fetch_log)
    write_json(DIFF_PATH, diff)

    version_dir = DATA_DIR / "versions" / metadata["dataset_version"]
    version_dir.mkdir(parents=True, exist_ok=True)
    frame.to_csv(version_dir / "holidays.csv", index=False)
    disagreements.to_csv(version_dir / "disagreements.csv", index=False)
    write_json(version_dir / "metadata.json", metadata)

    if diff["past_change_count"]:
        write_json(PAST_CHANGES_PATH, diff)
        print(
            f"flagged {diff['past_change_count']} past-date changes in {PAST_CHANGES_PATH}",
            file=sys.stderr,
        )
        return 2
    if PAST_CHANGES_PATH.exists() and previous is not None:
        PAST_CHANGES_PATH.unlink()
    print(
        f"wrote {len(frame)} rows version={metadata['dataset_version']} "
        f"fetch_failures={len(metadata['fetch_failures'])}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh the exchange holiday dataset")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report staleness and unresolved disagreements without rebuilding",
    )
    args = parser.parse_args(argv)
    try:
        return refresh(check_only=args.check)
    except (AssertionError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
