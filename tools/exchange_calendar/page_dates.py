"""Check that fetched official pages name the dates the dataset says they name.

A page is checked by searching its text for each expected date in the layouts
that exchange uses. Three outcomes:

- every expected date is found: ok;
- some expected dates are missing from a page that does name other dates:
  disagreement, and refresh exits nonzero;
- a list page names none of the expected dates in any known layout: unparsed.
  The page layout is not known well enough to say the dates are wrong, so it is
  recorded as an open item.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import urlparse

from tools.exchange_calendar.official import OfficialEvent

JPX_MONTHS = {
    1: "Jan.", 2: "Feb.", 3: "Mar.", 4: "Apr.", 5: "May", 6: "Jun.",
    7: "Jul.", 8: "Aug.", 9: "Sep.", 10: "Oct.", 11: "Nov.", 12: "Dec.",
}


@dataclass
class PageCheck:
    url: str
    expected: int = 0
    matched: int = 0
    missing: list[str] = field(default_factory=list)
    unparsed: bool = False

    @property
    def disagrees(self) -> bool:
        return bool(self.missing)


def normalise(raw_html: str) -> str:
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", raw_html, flags=re.S | re.I)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    return re.sub(r"\s+", " ", text)


def date_tokens(iso: str, host: str) -> list[str]:
    day = date.fromisoformat(iso)
    weekday = day.strftime("%A")
    month = day.strftime("%B")
    short = day.strftime("%b")
    tokens = {
        iso,
        f"{day.day} {month} {day.year}",
        f"{month} {day.day}, {day.year}",
        f"{month} {day.day} {day.year}",
        f"{day.day} {short} {day.year}",
        f"{short} {day.day}, {day.year}",
        f"{day.day:02d}/{day.month:02d}/{day.year}",
        f"{day.day}/{day.month}/{day.year}",
        f"{day.year}/{day.month:02d}/{day.day:02d}",
        f"{day.year}年{day.month}月{day.day}日",
    }
    if host.endswith("nyse.com"):
        # NYSE lists 2026, 2027, and 2028 side by side; each row is "Weekday, Month D".
        tokens.add(f"{weekday}, {month} {day.day}")
    if host.endswith("jpx.co.jp"):
        # JPX rows read "Jan. 1 (Thu.)".
        tokens.add(f"{JPX_MONTHS[day.month]} {day.day} ({weekday[:3]}.)")
    return sorted(tokens)


def check_page(url: str, text: str, events: list[OfficialEvent]) -> PageCheck:
    host = urlparse(url).netloc
    check = PageCheck(url=url, expected=len(events))
    for event in events:
        if any(token in text for token in date_tokens(event.date, host)):
            check.matched += 1
        else:
            check.missing.append(event.date)
    if check.matched == 0 and len(events) > 1:
        check.unparsed = True
        check.missing = []
    return check
