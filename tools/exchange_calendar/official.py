"""Official holiday facts parsed from pages fetched on 2026-10-11.

These records are a snapshot, not a live scrape. ``refresh.py`` re-checks that
the source URLs still respond and still contain the expected phrase. It does
not invent dates when a fetch fails.

SSE and KRX years after 2026 are intentionally absent. China and Korea publish
the next year's schedule around December; those dates are not projected here.
"""

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class OfficialEvent:
    exchange: str
    date: str
    event_type: str
    name: str
    source_url: str
    open_time: str = ""
    close_time: str = ""


NYSE_URL = "https://www.nyse.com/markets/hours-calendars"
NASDAQ_URL = "https://www.nasdaqtrader.com/trader.aspx?id=calendar"
JPX_URL = "https://www.jpx.co.jp/english/corporate/about-jpx/calendar/"
SSE_EN_URL = "https://english.sse.com.cn/start/trading/schedule/"
SSE_2026_URL = (
    "https://www.sse.com.cn/disclosure/announcement/general/c/c_20251222_10802507.shtml"
)

# Years whose closure list on the fetched page is the full weekday closure set,
# not a handful of weather notices. Library dates in these years that are not
# on the page are disagreements.
COMPLETE_CLOSURE_YEARS = {
    "NYSE": frozenset({2026, 2027, 2028}),
    "Nasdaq": frozenset({2026}),
    "JPX": frozenset({2026, 2027}),
    "SSE": frozenset({2024, 2025, 2026}),
    "HKEX": frozenset(),
    "CME": frozenset(),
    "KRX": frozenset(),
}

# Phrase that must still appear in the live page for the snapshot to be treated
# as re-checked. Failure does not delete official rows; it is an open item.
FETCH_CHECKS = (
    (NYSE_URL, "Martin Luther King"),
    (NASDAQ_URL, "Holiday Schedule 2026"),
    (JPX_URL, "Market Holidays"),
    (SSE_EN_URL, "Shanghai Stock Exchange"),
    (SSE_2026_URL, "2026"),
    (
        "https://www.hkex.com.hk/News/Market-Communications/2023/2309083news?sc_lang=en",
        "all trading sessions",
    ),
    (
        "https://www.hkex.com.hk/News/Market-Communications/2023/2309012news?sc_lang=en",
        "all trading sessions",
    ),
    (
        "https://www.hkex.com.hk/News/Market-Communications/2024/2409062news?sc_lang=en",
        "all trading sessions",
    ),
    (
        "https://www.hkex.com.hk/News/Market-Communications/2021/2110132news?sc_lang=en",
        "cancelled",
    ),
    (
        "https://www.hkex.com.hk/News/Market-Communications/2023/2307172news?sc_lang=en",
        "cancelled",
    ),
    (
        "https://www.hkex.com.hk/News/Market-Communications/2020/2010132news?sc_lang=en",
        "cancelled",
    ),
    (
        "https://www.hkex.com.hk/News/Market-Communications/2016/1608022news?sc_lang=en",
        "cancelled",
    ),
    (
        "https://www.hkex.com.hk/News/Market-Communications/2016/1610212news?sc_lang=en",
        "cancelled",
    ),
    (
        "https://www.hkex.com.hk/News/Market-Communications/2017/1708232news?sc_lang=en",
        "cancelled",
    ),
)


def _us_early(exchange: str, date: str, name: str, url: str) -> OfficialEvent:
    return OfficialEvent(
        exchange=exchange,
        date=date,
        event_type="early_close",
        name=name,
        source_url=url,
        open_time="09:30",
        close_time="13:00",
    )


def _closure(exchange: str, date: str, name: str, url: str, event_type: str = "full_closure") -> OfficialEvent:
    return OfficialEvent(
        exchange=exchange,
        date=date,
        event_type=event_type,
        name=name,
        source_url=url,
    )


def _nyse_events() -> list[OfficialEvent]:
    # Parsed from the NYSE "Holidays & Trading Hours" table for 2026, 2027, and 2028,
    # including the early-close footnotes on that page. Core open is 9:30 a.m. ET.
    full = [
        ("2026-01-01", "New Year's Day"),
        ("2026-01-19", "Martin Luther King, Jr. Day"),
        ("2026-02-16", "Washington's Birthday"),
        ("2026-04-03", "Good Friday"),
        ("2026-05-25", "Memorial Day"),
        ("2026-06-19", "Juneteenth National Independence Day"),
        ("2026-07-03", "Independence Day (observed)"),
        ("2026-09-07", "Labor Day"),
        ("2026-11-26", "Thanksgiving Day"),
        ("2026-12-25", "Christmas Day"),
        ("2027-01-01", "New Year's Day"),
        ("2027-01-18", "Martin Luther King, Jr. Day"),
        ("2027-02-15", "Washington's Birthday"),
        ("2027-03-26", "Good Friday"),
        ("2027-05-31", "Memorial Day"),
        ("2027-06-18", "Juneteenth National Independence Day (observed)"),
        ("2027-07-05", "Independence Day (observed)"),
        ("2027-09-06", "Labor Day"),
        ("2027-11-25", "Thanksgiving Day"),
        ("2027-12-24", "Christmas Day (observed)"),
        ("2028-01-17", "Martin Luther King, Jr. Day"),
        ("2028-02-21", "Washington's Birthday"),
        ("2028-04-14", "Good Friday"),
        ("2028-05-29", "Memorial Day"),
        ("2028-06-19", "Juneteenth National Independence Day"),
        ("2028-07-04", "Independence Day"),
        ("2028-09-04", "Labor Day"),
        ("2028-11-23", "Thanksgiving Day"),
        ("2028-12-25", "Christmas Day"),
    ]
    early = [
        ("2026-11-27", "Day after Thanksgiving"),
        ("2026-12-24", "Christmas Eve"),
        ("2027-11-26", "Day after Thanksgiving"),
        ("2028-07-03", "Day before Independence Day"),
        ("2028-11-24", "Day after Thanksgiving"),
    ]
    rows = [_closure("NYSE", date, name, NYSE_URL) for date, name in full]
    rows.extend(_us_early("NYSE", date, name, NYSE_URL) for date, name in early)
    return rows


def _nasdaq_2026_events() -> list[OfficialEvent]:
    # Parsed from the Nasdaq Trader 2026 holiday schedule only.
    # Later years are not on that page and are not marked official.
    full = [
        ("2026-01-01", "New Years Day (Observed)"),
        ("2026-01-19", "Martin Luther King, Jr. Day"),
        ("2026-02-16", "President's Day - U.S."),
        ("2026-04-03", "Good Friday"),
        ("2026-05-25", "Memorial Day - U.S."),
        ("2026-06-19", "Juneteenth Holiday (Observed)"),
        ("2026-07-03", "Independence Day - U.S. (Observed)"),
        ("2026-09-07", "Labor Day - U.S."),
        ("2026-11-26", "Thanksgiving Day - U.S."),
        ("2026-12-25", "Christmas Holiday - U.S."),
    ]
    early = [
        ("2026-11-27", "Early Close - U.S."),
        ("2026-12-24", "Early Close - U.S."),
    ]
    rows = [_closure("Nasdaq", date, name, NASDAQ_URL) for date, name in full]
    rows.extend(_us_early("Nasdaq", date, name, NASDAQ_URL) for date, name in early)
    return rows


def _jpx_events() -> list[OfficialEvent]:
    # Weekday rows from the JPX English market-holiday tables for 2026 and 2027.
    # Saturday and Sunday rows on that page are omitted because they are not
    # weekday closures. The page was fetched on 2026-10-11 and matched XTKS.
    named = [
        ("2026-01-01", "New Year's Day"),
        ("2026-01-02", "Market Holiday"),
        ("2026-01-12", "Coming of Age Day"),
        ("2026-02-11", "National Foundation Day"),
        ("2026-02-23", "Emperor's Birthday"),
        ("2026-03-20", "Vernal Equinox"),
        ("2026-04-29", "Showa Day"),
        ("2026-05-04", "Greenery Day"),
        ("2026-05-05", "Children's Day"),
        ("2026-05-06", "Constitution Memorial Day (observed)"),
        ("2026-07-20", "Marine Day"),
        ("2026-08-11", "Mountain Day"),
        ("2026-09-21", "Respect for the Aged Day"),
        ("2026-09-22", "Holiday"),
        ("2026-09-23", "Autumnal Equinox"),
        ("2026-10-12", "Sports Day"),
        ("2026-11-03", "Culture Day"),
        ("2026-11-23", "Labor Thanksgiving Day"),
        ("2026-12-31", "Market Holiday"),
        ("2027-01-01", "New Year's Day"),
        ("2027-01-11", "Coming of Age Day"),
        ("2027-02-11", "National Foundation Day"),
        ("2027-02-23", "Emperor's Birthday"),
        ("2027-03-22", "Vernal Equinox (observed)"),
        ("2027-04-29", "Showa Day"),
        ("2027-05-03", "Constitution Memorial Day"),
        ("2027-05-04", "Greenery Day"),
        ("2027-05-05", "Children's Day"),
        ("2027-07-19", "Marine Day"),
        ("2027-08-11", "Mountain Day"),
        ("2027-09-20", "Respect for the Aged Day"),
        ("2027-09-23", "Autumnal Equinox"),
        ("2027-10-11", "Sports Day"),
        ("2027-11-03", "Culture Day"),
        ("2027-11-23", "Labor Thanksgiving Day"),
        ("2027-12-31", "Market Holiday"),
    ]
    return [_closure("JPX", date, name, JPX_URL) for date, name in named]


def _expand_ranges(ranges: list[tuple[str, str, str]], url: str) -> list[OfficialEvent]:
    rows: list[OfficialEvent] = []
    for start, end, name in ranges:
        for day in pd.bdate_range(start, end):
            rows.append(_closure("SSE", day.strftime("%Y-%m-%d"), name, url))
    return rows


def _sse_events() -> list[OfficialEvent]:
    # Weekday expansion of the closure ranges printed on the fetched pages.
    # Weekend substitute days mentioned on those pages are not weekday sessions.
    english = [
        ("2024-01-01", "2024-01-01", "New Year's Day"),
        ("2024-02-09", "2024-02-17", "Chinese New Year"),
        ("2024-04-04", "2024-04-06", "Qingming Festival"),
        ("2024-05-01", "2024-05-05", "Chinese Labor Day"),
        ("2024-06-10", "2024-06-10", "Dragon Boat Festival"),
        ("2024-09-15", "2024-09-17", "Mid-Autumn Festival"),
        ("2024-10-01", "2024-10-07", "China National Day"),
        ("2025-01-01", "2025-01-01", "New Year's Day"),
        ("2025-01-28", "2025-02-04", "Chinese New Year"),
        ("2025-04-04", "2025-04-06", "Qingming Festival"),
        ("2025-05-01", "2025-05-05", "Chinese Labor Day"),
        ("2025-05-31", "2025-06-02", "Dragon Boat Festival"),
        ("2025-10-01", "2025-10-08", "China's National Day and Mid-Autumn Festival"),
    ]
    notice_2026 = [
        ("2026-01-01", "2026-01-03", "New Year's Day"),
        ("2026-02-15", "2026-02-23", "Chinese New Year"),
        ("2026-04-04", "2026-04-06", "Qingming Festival"),
        ("2026-05-01", "2026-05-05", "Labor Day"),
        ("2026-06-19", "2026-06-21", "Dragon Boat Festival"),
        ("2026-09-25", "2026-09-27", "Mid-Autumn Festival"),
        ("2026-10-01", "2026-10-07", "National Day"),
    ]
    return _expand_ranges(english, SSE_EN_URL) + _expand_ranges(notice_2026, SSE_2026_URL)


def _hkex_events() -> list[OfficialEvent]:
    # Final "no trading" notices only. Delay-open notices that do not confirm
    # a full-day cancellation are not listed. Bodies were fetched on 2026-10-11.
    notices = [
        (
            "2016-08-02",
            "Typhoon Signal No. 8",
            "https://www.hkex.com.hk/News/Market-Communications/2016/1608022news?sc_lang=en",
        ),
        (
            "2016-10-21",
            "Typhoon Signal No. 8",
            "https://www.hkex.com.hk/News/Market-Communications/2016/1610212news?sc_lang=en",
        ),
        (
            "2017-08-23",
            "Typhoon Signal No. 10",
            "https://www.hkex.com.hk/News/Market-Communications/2017/1708232news?sc_lang=en",
        ),
        (
            "2020-10-13",
            "Typhoon Signal No. 8",
            "https://www.hkex.com.hk/News/Market-Communications/2020/2010132news?sc_lang=en",
        ),
        (
            "2021-10-13",
            "Typhoon Signal No. 8",
            "https://www.hkex.com.hk/News/Market-Communications/2021/2110132news?sc_lang=en",
        ),
        (
            "2023-07-17",
            "Typhoon Signal No. 8",
            "https://www.hkex.com.hk/News/Market-Communications/2023/2307172news?sc_lang=en",
        ),
        (
            "2023-09-01",
            "Typhoon Signal No. 8",
            "https://www.hkex.com.hk/News/Market-Communications/2023/2309012news?sc_lang=en",
        ),
        (
            "2023-09-08",
            "Black Rainstorm Warning and Extreme Conditions",
            "https://www.hkex.com.hk/News/Market-Communications/2023/2309083news?sc_lang=en",
        ),
        (
            "2024-09-06",
            "Typhoon Signal No. 8",
            "https://www.hkex.com.hk/News/Market-Communications/2024/2409062news?sc_lang=en",
        ),
    ]
    return [
        _closure("HKEX", date, name, url, event_type="typhoon_closure")
        for date, name, url in notices
    ]


def official_events() -> list[OfficialEvent]:
    return (
        _nyse_events()
        + _nasdaq_2026_events()
        + _jpx_events()
        + _sse_events()
        + _hkex_events()
    )


def official_by_exchange() -> dict[str, list[OfficialEvent]]:
    grouped: dict[str, list[OfficialEvent]] = {}
    for event in official_events():
        grouped.setdefault(event.exchange, []).append(event)
    return grouped
