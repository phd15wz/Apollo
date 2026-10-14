"""Official holiday facts parsed from pages fetched on 2026-10-11.

These records are a snapshot, not a live scrape. ``refresh.py`` re-checks that
the source URLs still respond and still contain the expected phrase. It does
not invent dates when a fetch fails.

SSE and KRX years after 2026 are intentionally absent. China and Korea publish
the next year's schedule around December; those dates are not projected here.
"""

from dataclasses import dataclass

import pandas as pd

from tools.exchange_calendar.exchanges import LIBRARY_SOURCE_URL


@dataclass(frozen=True)
class OfficialEvent:
    """A fact read from a fetched page. It overrides library rows on its date.

    ``status`` is ``official`` when the page belongs to the exchange or a
    regulator. A fetched third-party copy of the exchange's own announcement
    (a news wire, a republished press release) is ``secondary-confirmed``.
    """

    exchange: str
    date: str
    event_type: str
    name: str
    source_url: str
    open_time: str = ""
    close_time: str = ""
    status: str = "official"
    note: str = ""


NYSE_URL = "https://www.nyse.com/markets/hours-calendars"
NASDAQ_URL = "https://www.nasdaqtrader.com/trader.aspx?id=calendar"
JPX_URL = "https://www.jpx.co.jp/english/corporate/about-jpx/calendar/"
SSE_EN_URL = "https://english.sse.com.cn/start/trading/schedule/"
SSE_2026_URL = (
    "https://www.sse.com.cn/disclosure/announcement/general/c/c_20251222_10802507.shtml"
)
HKEX_CT_166_2025_URL = (
    "https://www.hkex.com.hk/-/media/HKEX-Market/Services/Circulars-and-Notices/"
    "Participant-and-Members-Circulars/SEHK/2025/ce_SEHK_CT_166_2025.pdf"
)
CME_CARTER_MOURNING_URL = (
    "https://investor.cmegroup.com/news-releases/news-release-details/"
    "cme-group-announces-trading-hours-us-national-day-mourning-honor"
)
CME_BUSH_MOURNING_URL = (
    "https://www.cmegroup.com/notices/clearing/2018/12/Chadv18-474.html"
)
CME_2021_GOOD_FRIDAY_URL = (
    "https://www.cmegroup.com/tools-information/holiday-calendar/files/"
    "2021-good-friday-advisory.pdf"
)
CME_2024_GOOD_FRIDAY_URL = (
    "https://www.cmegroup.com/tools-information/holiday-calendar/files/"
    "2024-good-friday-advisory.pdf"
)
CME_2025_GOOD_FRIDAY_URL = (
    "https://www.cmegroup.com/tools-information/holiday-calendar/files/"
    "2025/2025-good-friday-clearing-advisory.pdf"
)
CME_2026_GOOD_FRIDAY_URL = (
    "https://www.cmegroup.com/tools-information/holiday-calendar/files/"
    "2026/2026-good-friday-clearing-advisory.pdf"
)
MONDO_911_RESUME_URL = (
    "https://mondovisione.com/news/"
    "chicago-mercantile-exchange-inc-to-resume-trading-in-us-equity-index-products-no-2011124/"
)
MONDO_912_CLOSE_URL = (
    "https://mondovisione.com/media-and-resources/news/"
    "cme-cbot-cboe-chx-announce-coordinated-close-of-chicago-financial-markets-sep-12-2011124/"
)
MONDO_REAGAN_URL = (
    "https://mondovisione.com/media-and-resources/news/cme-to-close-on-friday-june-11-2011124/"
)
MONDO_FORD_URL = (
    "https://mondovisione.com/media-and-resources/news/"
    "cme-national-day-of-mourning-for-former-president-gerald-r-ford-2011124/"
)
IBTIMES_2010_GOOD_FRIDAY_URL = "https://www.ibtimes.com/futures-rise-stronger-march-jobs-data-391155"
FOX_2012_GOOD_FRIDAY_URL = (
    "https://www.foxbusiness.com/markets/what-happens-when-jobs-friday-clashes-with-good-friday"
)
CNBC_2015_GOOD_FRIDAY_URL = (
    "https://www.cnbc.com/2015/04/03/weak-jobs-keeps-fed-at-bay-until-second-half.html"
)
CNBC_2015_GOOD_FRIDAY_PREVIEW_URL = (
    "https://www.cnbc.com/2015/04/02/markets-reaction-to-jobs-number-could-be-quite-fierce-trader-warns.html"
)
MARKETWATCH_2023_GOOD_FRIDAY_URL = (
    "https://web.archive.org/web/20230407154500/"
    "https://www.marketwatch.com/story/when-do-u-s-markets-reopen-after-easter-aacef663"
)
CFTC_CME_06_120_URL = (
    "https://www.cftc.gov/IndustryOversight/IndustryFilings/TradingOrganizationRules/13650"
)
CANNON_2015_GOOD_FRIDAY_URL = (
    "https://www.cannontrading.com/tools/support-resistance-levels/"
    "good-friday-holiday-futures-trading-schedule-2015/"
)
SEDAILY_KRX_2026_ELECTIONS_URL = (
    "https://en.sedaily.com/finance/2026/05/20/"
    "korea-exchange-to-close-on-local-election-day-constitution"
)
ASIAE_KRX_2026_ELECTIONS_URL = "https://www.asiae.co.kr/en/article/2026052010280055447"

WEAKER_B_NOTE = (
    "weaker B: market-wrap sentence (no KRX statement cited); "
    "still secondary-confirmed per policy"
)
CME_CLOCK_NOTE = (
    "CMES close 12:00 kept; not changed from the library. Broker bulletins "
    "(Cannon/AMP/RJO, tier C) report 12:15 CT for equity-index. Needs a CME "
    "tier-A check. CME Group holiday pages returned HTTP 403 in this run."
)
CME_EQUITY_INDEX_SCOPE_NOTE = (
    "CME rows describe CME equity index futures (Globex) hours only. "
    "Do not use them for interest-rate, FX, or crypto futures until a product column exists."
)
CME_ABBREVIATED_EARLY_NOTE = (
    "Typed early_close for the US equity-index Globex session (not a full "
    "closure). open_time is the previous local evening 17:00 CT, same format as "
    "other CME early closes. close_time is the equity-index Globex halt. "
    "Interest-rate, FX, and crypto hours on the same date may differ. "
    + CME_EQUITY_INDEX_SCOPE_NOTE
)

# Years whose closure list on the fetched page is the full weekday closure set,
# not a handful of weather notices. Library dates in these years that are not
# on the page are disagreements.
COMPLETE_CLOSURE_YEARS = {
    "NYSE": frozenset({2026, 2027, 2028}),
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
    (HKEX_CT_166_2025_URL, "no afternoon trading session"),
    (CME_CARTER_MOURNING_URL, "8:30 a.m. CT"),
    (CME_BUSH_MOURNING_URL, "8:30 a.m. Central Time"),
    (CME_2021_GOOD_FRIDAY_URL, "abbreviated session"),
    (CME_2024_GOOD_FRIDAY_URL, "March 29"),
    (CME_2025_GOOD_FRIDAY_URL, "April 18"),
    (CME_2026_GOOD_FRIDAY_URL, "abbreviated session"),
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


def _closure(
    exchange: str,
    date: str,
    name: str,
    url: str,
    event_type: str = "full_closure",
    status: str = "official",
    note: str = "",
) -> OfficialEvent:
    return OfficialEvent(
        exchange=exchange,
        date=date,
        event_type=event_type,
        name=name,
        source_url=url,
        status=status,
        note=note,
    )


def _secondary(
    exchange: str,
    date: str,
    event_type: str,
    name: str,
    url: str,
    open_time: str = "",
    close_time: str = "",
    note: str = "",
) -> OfficialEvent:
    return OfficialEvent(
        exchange=exchange,
        date=date,
        event_type=event_type,
        name=name,
        source_url=url,
        open_time=open_time,
        close_time=close_time,
        status="secondary-confirmed",
        note=note,
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


def nasdaq_check_events() -> list[OfficialEvent]:
    """Nasdaq Trader 2026 dates used only as an NYSE equality check.

    These are not written to the dataset. Nasdaq is an alias of NYSE.
    """
    return _nasdaq_2026_events()


def nasdaq_nyse_mismatches() -> list[str]:
    """Dates or hours where the Nasdaq 2026 page disagrees with NYSE 2026."""
    nasdaq = {
        (event.date, event.event_type, event.open_time, event.close_time)
        for event in nasdaq_check_events()
    }
    nyse_2026 = {
        (event.date, event.event_type, event.open_time, event.close_time)
        for event in _nyse_events()
        if event.date.startswith("2026")
    }
    messages = []
    for date, event_type, open_time, close_time in sorted(nasdaq - nyse_2026):
        messages.append(
            f"Nasdaq announces {date} {event_type} {open_time}-{close_time} but NYSE does not"
        )
    for date, event_type, open_time, close_time in sorted(nyse_2026 - nasdaq):
        messages.append(
            f"NYSE announces {date} {event_type} {open_time}-{close_time} but Nasdaq 2026 does not"
        )
    return messages


def _nasdaq_2026_events() -> list[OfficialEvent]:
    # Parsed from the Nasdaq Trader 2026 holiday schedule only.
    # Later years are not on that page. These events are a fetch check, not rows.
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
            "2024-09-06",
            "Typhoon Signal No. 8",
            "https://www.hkex.com.hk/News/Market-Communications/2024/2409062news?sc_lang=en",
        ),
    ]
    events = [
        _closure("HKEX", date, name, url, event_type="typhoon_closure")
        for date, name, url in notices
    ]
    # Not a typhoon: the notice cites a Black Rainstorm Warning and the
    # government's Extreme Conditions announcement.
    events.append(
        _closure(
            "HKEX",
            "2023-09-08",
            "Black Rainstorm Warning and Extreme Conditions",
            "https://www.hkex.com.hk/News/Market-Communications/2023/2309083news?sc_lang=en",
            event_type="weather_closure",
        )
    )
    # Circular CT/166/2025, fetched 2026-10-11: no afternoon session on these
    # securities-market eves. 12:00 is the half-day session on the HKEX
    # trading-hours page; the circular does not print the clock.
    halfday_note = (
        "CT/166/2025: no afternoon session. 12:00 is the half-day session on "
        "the HKEX securities trading-hours page; the circular does not print the clock."
    )
    for date, name in (
        ("2025-12-24", "Christmas Eve"),
        ("2025-12-31", "New Year's Eve"),
        ("2026-02-16", "Lunar New Year's Eve"),
    ):
        events.append(
            OfficialEvent(
                exchange="HKEX",
                date=date,
                event_type="early_close",
                name=name,
                source_url=HKEX_CT_166_2025_URL,
                open_time="09:30",
                close_time="12:00",
                status="official",
                note=halfday_note,
            )
        )
    return events


def _secondary_news_events() -> list[OfficialEvent]:
    """Tier-B news rows read on 2026-10-11 (Isaac's secondary_verify.csv).

    Status stays ``secondary-confirmed``. Weaker-B rows (market-wrap sentence,
    no named KRX statement) are flagged in ``note`` and remain grade B.
    """
    yna_2025_seollal = "https://en.yna.co.kr/view/AEN20250117003800320"
    yna_2025_yearend = "https://en.yna.co.kr/view/AEN20251218005600320"
    yna_2025_chuseok = "https://en.yna.co.kr/view/AEN20251010001800320"
    yna_2026_seollal = "https://en.yna.co.kr/view/AEN20260219007051320"
    yna_2026_chuseok = "https://en.yna.co.kr/view/AEN20260909006300320"
    asiae_2024_yearend = "https://www.asiae.co.kr/en/article/2024121711235993105"
    asiae_2023_yearend = "https://www.asiae.co.kr/en/article/2023121410523267434"
    sedaily_2026_may = (
        "https://en.sedaily.com/finance/2026/05/02/"
        "may-holiday-clusters-iran-uncertainty-prompt-cash-strategy"
    )
    rows = [
        _secondary(
            "KRX",
            "2023-12-29",
            "full_closure",
            "End of Year Holiday",
            "https://www.koreatimes.co.kr/economy/20231214/financial-markets-to-close-on-dec-29-ahead-of-new-year",
            note="Yonhap wire in Korea Times; cites Korea Exchange",
        ),
        _secondary(
            "KRX",
            "2024-01-02",
            "late_open",
            "First business day of the year",
            asiae_2023_yearend,
            open_time="10:00",
            close_time="15:30",
            note="Cites KRX press release; Jan 2 regular open 10:00, close 15:30 unchanged",
        ),
        _secondary(
            "KRX",
            "2024-05-06",
            "full_closure",
            "Children's Day (alternative holiday)",
            "https://www.koreaherald.com/article/3387435",
            note=WEAKER_B_NOTE,
        ),
        _secondary(
            "KRX",
            "2024-10-01",
            "full_closure",
            "Temporary holiday",
            "https://en.yna.co.kr/view/AEN20240910002600320",
            note="Cites Korea Exchange",
        ),
        _secondary(
            "KRX",
            "2024-12-31",
            "full_closure",
            "End of Year Holiday",
            asiae_2024_yearend,
            note="Cites Korea Exchange announcement; Dec 31 year-end holiday",
        ),
        _secondary(
            "KRX",
            "2025-01-02",
            "late_open",
            "First business day of the year",
            asiae_2024_yearend,
            open_time="10:00",
            close_time="15:30",
            note="Cites Korea Exchange announcement; Jan 2 regular open 10:00, close 15:30 unchanged",
        ),
        _secondary(
            "KRX",
            "2025-01-27",
            "full_closure",
            "Temporary holiday",
            yna_2025_seollal,
            note="Yonhap cites KRX/market operators; closure Jan 27-30",
        ),
        _secondary(
            "KRX",
            "2025-01-28",
            "full_closure",
            "Seollal (New Year's Day by the lunar) (-1 day)",
            yna_2025_seollal,
            note="Yonhap cites KRX/market operators; closure Jan 27-30",
        ),
        _secondary(
            "KRX",
            "2025-01-29",
            "full_closure",
            "Seollal (New Year's Day by the lunar)",
            yna_2025_seollal,
            note="Yonhap cites KRX/market operators; closure Jan 27-30",
        ),
        _secondary(
            "KRX",
            "2025-01-30",
            "full_closure",
            "Seollal (New Year's Day by the lunar) (+1 day)",
            yna_2025_seollal,
            note="Yonhap cites KRX/market operators; closure Jan 27-30",
        ),
        _secondary(
            "KRX",
            "2025-05-05",
            "full_closure",
            "Buddha's Birthday",
            "https://en.yna.co.kr/view/AEN20250508009900320",
            note=f"{WEAKER_B_NOTE}. Article lists Children's Day and Buddha's Birthday for Mon-Tue without assigning each date.",
        ),
        _secondary(
            "KRX",
            "2025-05-06",
            "full_closure",
            "Children's Day",
            "https://en.yna.co.kr/view/AEN20250508009900320",
            note=f"{WEAKER_B_NOTE}. Article lists Children's Day and Buddha's Birthday for Mon-Tue without assigning each date.",
        ),
        _secondary(
            "KRX",
            "2025-06-03",
            "full_closure",
            "Presidential election",
            "https://en.yna.co.kr/view/AEN20250528002000320",
            note="Cites Korea Exchange / FX committee",
        ),
        _secondary(
            "KRX",
            "2025-10-03",
            "full_closure",
            "Korean National Foundation Day",
            yna_2025_chuseok,
            note=f"{WEAKER_B_NOTE}. Wrap: reopened after seven-day Chuseok holiday (Oct 3-9).",
        ),
        _secondary(
            "KRX",
            "2025-10-06",
            "full_closure",
            "Chuseok (Korean Thanksgiving Day)",
            yna_2025_chuseok,
            note=f"{WEAKER_B_NOTE}. Wrap: reopened after seven-day Chuseok holiday (Oct 3-9).",
        ),
        _secondary(
            "KRX",
            "2025-10-07",
            "full_closure",
            "Chuseok (Korean Thanksgiving Day) (+1 day)",
            yna_2025_chuseok,
            note=f"{WEAKER_B_NOTE}. Wrap: reopened after seven-day Chuseok holiday (Oct 3-9).",
        ),
        _secondary(
            "KRX",
            "2025-10-08",
            "full_closure",
            "Chuseok (Korean Thanksgiving Day) (-1 day)",
            yna_2025_chuseok,
            note=f"{WEAKER_B_NOTE}. Wrap: reopened after seven-day Chuseok holiday (Oct 3-9).",
        ),
        _secondary(
            "KRX",
            "2025-10-09",
            "full_closure",
            "Hangul Proclamation Day",
            yna_2025_chuseok,
            note=f"{WEAKER_B_NOTE}. Wrap: reopened after seven-day Chuseok holiday (Oct 3-9).",
        ),
        _secondary(
            "KRX",
            "2025-12-31",
            "full_closure",
            "End of Year Holiday",
            yna_2025_yearend,
            note="Cites KRX; Jan 2 opens one hour late at 10:00",
        ),
        _secondary(
            "KRX",
            "2026-01-02",
            "late_open",
            "First business day of the year",
            yna_2025_yearend,
            open_time="10:00",
            close_time="15:30",
            note="Cites KRX; Jan 2 opens one hour late at 10:00",
        ),
        _secondary(
            "KRX",
            "2026-02-16",
            "full_closure",
            "Seollal (New Year's Day by the lunar) (-1 day)",
            yna_2026_seollal,
            note=f"{WEAKER_B_NOTE}. Wrap: three-day Lunar New Year break before Feb 19.",
        ),
        _secondary(
            "KRX",
            "2026-02-17",
            "full_closure",
            "Seollal (New Year's Day by the lunar)",
            yna_2026_seollal,
            note=f"{WEAKER_B_NOTE}. Wrap: three-day Lunar New Year break before Feb 19.",
        ),
        _secondary(
            "KRX",
            "2026-02-18",
            "full_closure",
            "Seollal (New Year's Day by the lunar) (+1 day)",
            yna_2026_seollal,
            note=f"{WEAKER_B_NOTE}. Wrap: three-day Lunar New Year break before Feb 19.",
        ),
        _secondary(
            "KRX",
            "2026-03-02",
            "full_closure",
            "Independence Movement Day (substitute holiday)",
            "https://www.ajupress.com/view/20260302140757337",
            note="Same-day report: market closed for March 1 makeup holiday",
        ),
        _secondary(
            "KRX",
            "2026-05-01",
            "full_closure",
            "Labor Day",
            sedaily_2026_may,
            note=f"{WEAKER_B_NOTE}. Cites financial investment industry; names Labor Day May 1, Children's Day May 5, Buddha's Birthday May 25.",
        ),
        _secondary(
            "KRX",
            "2026-05-05",
            "full_closure",
            "Children's Day",
            sedaily_2026_may,
            note=f"{WEAKER_B_NOTE}. Cites financial investment industry; names Labor Day May 1, Children's Day May 5, Buddha's Birthday May 25.",
        ),
        _secondary(
            "KRX",
            "2026-05-25",
            "full_closure",
            "Buddha's Birthday (substitute holiday)",
            sedaily_2026_may,
            note=f"{WEAKER_B_NOTE}. Cites financial investment industry; names Buddha's Birthday May 25 (May 24 is Sunday).",
        ),
        _secondary(
            "KRX",
            "2026-06-03",
            "full_closure",
            "Local elections",
            SEDAILY_KRX_2026_ELECTIONS_URL,
            note="Seoul Economic Daily and Asia Business Daily (2026-05-20) cite KRX: closed Jun 3 local elections and Jul 17 Constitution Day (reinstated).",
        ),
        _secondary(
            "KRX",
            "2026-07-17",
            "full_closure",
            "Constitution Day",
            SEDAILY_KRX_2026_ELECTIONS_URL,
            note="Seoul Economic Daily and Asia Business Daily (2026-05-20) cite KRX: closed Jun 3 local elections and Jul 17 Constitution Day (reinstated).",
        ),
        _secondary(
            "KRX",
            "2026-08-17",
            "full_closure",
            "National Liberation Day (substitute holiday)",
            "https://www.ajupress.com/view/20260806180870427",
            note="Cites KRX 2026 schedule; substitute holiday Aug 17",
        ),
        _secondary(
            "KRX",
            "2026-09-24",
            "full_closure",
            "Chuseok (Korean Thanksgiving Day) (-1 day)",
            yna_2026_chuseok,
            note="Cites KRX statement; Chuseok holiday runs Sept 24-27",
        ),
        _secondary(
            "KRX",
            "2026-09-25",
            "full_closure",
            "Chuseok (Korean Thanksgiving Day)",
            yna_2026_chuseok,
            note="Cites KRX statement; Chuseok holiday runs Sept 24-27",
        ),
        _secondary(
            "HKEX",
            "2024-12-31",
            "early_close",
            "New Year's Eve",
            "https://www.scmp.com/business/china-business/article/3292833/hong-kong-stocks-head-2024-gain-after-four-straight-years-losses",
            open_time="09:30",
            close_time="12:00",
            note="SCMP: shortened trading session; close time not stated in the article. 12:00 is the library / trading-hours half-day session.",
        ),
        _secondary(
            "HKEX",
            "2025-01-28",
            "early_close",
            "Lunar New Year's Eve",
            "https://www.gmanetwork.com/news/money/economy/934295/hong-kong-stocks-close-firmer-as-lunar-new-year-holiday-kicks-in/story/",
            open_time="09:30",
            close_time="12:00",
            note="Reuters via GMA: half-day trading; close time not stated in the article. 12:00 is the library / trading-hours half-day session.",
        ),
    ]
    return rows


def _cme_carter_mourning() -> OfficialEvent:
    # Fetched investor.cmegroup.com press release, 2026-10-11. US equity-index
    # products ended 8:30 a.m. CT and Globex reopened the same calendar day for
    # the Jan 10 trade date. That is not a full closure and not a simple early
    # close, so the row is ``other``.
    return OfficialEvent(
        exchange="CME",
        date="2025-01-09",
        event_type="other",
        name=(
            "National Day of Mourning for Jimmy Carter: US equity-index "
            "abbreviated session (ended 08:30 CT; Globex reopen 17:00 CT for "
            "the 2025-01-10 trade date). Interest-rate and agricultural markets "
            "closed 12:15 CT; crypto, energy, FX, and metals traded normal hours. "
            "This CMES row is not a full product calendar."
        ),
        source_url=CME_CARTER_MOURNING_URL,
        status="official",
        note=(
            "Fetched CME Group investor press release 2024-12-30. Clearing "
            "notice 25-012 and cmegroup.com holiday pages returned HTTP 403 "
            "in this run. "
            + CME_EQUITY_INDEX_SCOPE_NOTE
        ),
    )


def _cme_prev_evening(date: str) -> str:
    prev = pd.Timestamp(date) - pd.Timedelta(days=1)
    return prev.strftime("%Y-%m-%dT17:00")


def _cme_early(
    date: str,
    name: str,
    url: str,
    close_time: str,
    *,
    status: str,
    note: str,
) -> OfficialEvent:
    return OfficialEvent(
        exchange="CME",
        date=date,
        event_type="early_close",
        name=name,
        source_url=url,
        open_time=_cme_prev_evening(date),
        close_time=close_time,
        status=status,
        note=note,
    )


def _cme_equity_index_events() -> list[OfficialEvent]:
    """CME equity-index Globex facts from the 2026-10-14 Isaac round.

    Short equity-index sessions that halt in the morning are ``early_close``
    with previous-evening 17:00 CT open, matching other CME early closes.
    2025-01-09 stays ``other`` because Globex reopened the same calendar day.
    Official status is used only when a cmegroup.com notice/advisory fetched
    in that run states the closure or session. Mondovisione CME-release
    mirrors are ``secondary-confirmed``. Broker pages are not used to confirm.
    """
    holiday_clearing = (
        "Fetched CME Group clearing advisory 2026-10-14 (WebFetch; urllib of "
        "cmegroup.com was 403). No ITD cycle; prices carried forward from the "
        "prior session; trades submitted on the holiday clear the next "
        "business day. Treated as a full Globex holiday. "
        + CME_EQUITY_INDEX_SCOPE_NOTE
    )
    abbreviated_clock_unverified = (
        "Fetched CME Group clearing advisory 2026-10-14 (WebFetch; urllib of "
        "cmegroup.com was 403). Advisory states an abbreviated equity session "
        "and does not print the Globex close clock. close_time 08:15 CT is "
        "unverified: it is the usual Good Friday equity-index halt filled so "
        "the row can be typed early_close; no fetched tier A/B page prints "
        "this year's clock. Broker 08:15 CT reports were not used. "
        "Needs a CME tier-A check. "
        + CME_ABBREVIATED_EARLY_NOTE
    )
    mondo = (
        "Fetched Mondovisione copy of a CME release 2026-10-14. Mirror of a "
        "CME announcement is tier B, not official. "
        + CME_EQUITY_INDEX_SCOPE_NOTE
    )
    return [
        _closure(
            "CME",
            "2001-09-11",
            "September 11 attacks: CME open outcry and Globex suspended",
            MONDO_911_RESUME_URL,
            status="secondary-confirmed",
            note=(
                "Mondovisione CME release 2001-09-13: all CME markets remained "
                "closed until 2001-09-13 morning; Globex equity halted 08:30 CT "
                "on 2001-09-11; open outcry equity/ag did not trade that day. "
                + mondo
            ),
        ),
        _closure(
            "CME",
            "2001-09-12",
            "September 12 2001: coordinated close of Chicago financial markets",
            MONDO_912_CLOSE_URL,
            status="secondary-confirmed",
            note=(
                "Mondovisione CME/CBOT/CBOE/CHX joint release 2001-09-12: open "
                "outcry and electronic markets closed Wednesday 2001-09-12. "
                + mondo
            ),
        ),
        _cme_early(
            "2004-06-11",
            (
                "National Day of Mourning for Ronald Reagan: US equity-index "
                "abbreviated Globex session (ended 08:00 CT; reopen Sunday "
                "17:00 CT 2004-06-13). Floor closed. Not a full Globex closure."
            ),
            MONDO_REAGAN_URL,
            "08:00",
            status="secondary-confirmed",
            note="Mondovisione CME release 2004-06-07. " + mondo + " " + CME_ABBREVIATED_EARLY_NOTE,
        ),
        _cme_early(
            "2007-01-02",
            (
                "National Day of Mourning for Gerald R. Ford: US equity-index "
                "abbreviated Globex session (ended 08:15 CT; reopen 15:30 CT "
                "for the next trade date). Floor equities closed. IR/FX closed "
                "12:00 CT. Not a full equity-index Globex closure."
            ),
            MONDO_FORD_URL,
            "08:15",
            status="secondary-confirmed",
            note=(
                "Mondovisione CME notice 2006-12-29. CFTC filing CME 06-120 "
                f"listing fetched at {CFTC_CME_06_120_URL} confirms a certified "
                "hours change for 2007-01-02 but does not print 08:15. "
                "Associated PDF rul122906cme001 was not found. "
                + mondo
                + " "
                + CME_ABBREVIATED_EARLY_NOTE
            ),
        ),
        _cme_early(
            "2010-04-02",
            (
                "Good Friday: US equity-index abbreviated Globex session "
                "(ended 08:15 CT / 09:15 ET). Not a full closure."
            ),
            IBTIMES_2010_GOOD_FRIDAY_URL,
            "08:15",
            status="secondary-confirmed",
            note=(
                "Fetched IBTimes 2026-10-14; Reuters wire (David Gaffen) reports "
                "CME Globex abbreviated until 09:15 ET. "
                + CME_ABBREVIATED_EARLY_NOTE
            ),
        ),
        _cme_early(
            "2012-04-06",
            (
                "Good Friday: US equity-index abbreviated Globex session "
                "(ended 08:15 CT / 09:15 ET). Not a full closure."
            ),
            FOX_2012_GOOD_FRIDAY_URL,
            "08:15",
            status="secondary-confirmed",
            note=(
                "Fetched Fox Business 2026-10-14; pre-event schedule: equity "
                "index futures Thursday evening open until Friday 09:15 ET. "
                + CME_ABBREVIATED_EARLY_NOTE
            ),
        ),
        _cme_early(
            "2015-04-03",
            (
                "Good Friday: US equity-index abbreviated Globex session "
                "(ended 08:15 CT / 09:15 ET). Not a full closure."
            ),
            CNBC_2015_GOOD_FRIDAY_URL,
            "08:15",
            status="secondary-confirmed",
            note=(
                "Fetched CNBC 2026-10-14: early-morning futures session finished "
                "at 9:15 a.m. ET. Preview "
                f"{CNBC_2015_GOOD_FRIDAY_PREVIEW_URL} also says CME Globex until "
                "9:15 a.m. EDT. vcpost.com is not an established outlet and was "
                "not used. "
                f"Broker page {CANNON_2015_GOOD_FRIDAY_URL} is tier C and was "
                "not used to confirm. Direct reuters.com returned 401. "
                + CME_ABBREVIATED_EARLY_NOTE
            ),
        ),
        _cme_early(
            "2018-12-05",
            (
                "National Day of Mourning for George H.W. Bush: US equity-index "
                "abbreviated session (ended 08:30 CT after overnight trade; "
                "reopen for the 2018-12-06 trade date). Interest-rate products "
                "fully closed. Other markets regular hours."
            ),
            CME_BUSH_MOURNING_URL,
            "08:30",
            status="official",
            note=(
                "Fetched CME Clearing Advisory 18-474 on 2026-10-14 (WebFetch). "
                + CME_ABBREVIATED_EARLY_NOTE
            ),
        ),
        _cme_early(
            "2021-04-02",
            (
                "Good Friday: US equity-index abbreviated Globex session "
                "(not settled; used 2021-04-01 settlement; close_time 08:15 CT "
                "unverified). Not a full closure."
            ),
            CME_2021_GOOD_FRIDAY_URL,
            "08:15",
            status="official",
            note=abbreviated_clock_unverified,
        ),
        _cme_early(
            "2023-04-07",
            (
                "Good Friday: US equity-index abbreviated Globex session "
                "(ended 08:15 CT / 09:15 ET). Not a full closure."
            ),
            MARKETWATCH_2023_GOOD_FRIDAY_URL,
            "08:15",
            status="secondary-confirmed",
            note=(
                "Fetched 2026-10-14 Wayback copy of MarketWatch (Dow Jones) "
                "story aacef663: stock-index futures on the CME remained open "
                "until 9:15 a.m. Live marketwatch.com returned 401. "
                "ttm.financial is tier C and was not used to confirm. "
                + CME_ABBREVIATED_EARLY_NOTE
            ),
        ),
        _closure(
            "CME",
            "2024-03-29",
            "Good Friday",
            CME_2024_GOOD_FRIDAY_URL,
            status="official",
            note=holiday_clearing,
        ),
        _closure(
            "CME",
            "2025-04-18",
            "Good Friday",
            CME_2025_GOOD_FRIDAY_URL,
            status="official",
            note=holiday_clearing,
        ),
        _cme_early(
            "2026-04-03",
            (
                "Good Friday: US equity-index abbreviated Globex session "
                "(not settled; used 2026-04-02 settlement; close_time 08:15 CT "
                "unverified). IR/FX/crypto settled. Not a full equity-index "
                "closure."
            ),
            CME_2026_GOOD_FRIDAY_URL,
            "08:15",
            status="official",
            note=abbreviated_clock_unverified,
        ),
        _cme_carter_mourning(),
    ]


WIKIPEDIA_9_11_URL = "https://en.wikipedia.org/wiki/September_11_attacks"

# Reported CME facts that no fetched official or tier-B page confirms. They are
# kept as unverified rows so a reader can see them, but they never carry
# official or secondary-confirmed status. Every library calendar here treats
# 2001-09-13 as a session, so that disagreement is recorded in disagreements.csv.
#
# 2001-09-11 and 2001-09-12 were upgraded in the 2026-10-14 round from
# Mondovisione CME-release mirrors (secondary-confirmed). 2001-09-13 still has
# no fetched source that states the equity-index-only close.
def _unverified(
    exchange: str,
    date: str,
    event_type: str,
    name: str,
    url: str,
    open_time: str = "",
    close_time: str = "",
    note: str = "",
) -> OfficialEvent:
    return OfficialEvent(
        exchange=exchange,
        date=date,
        event_type=event_type,
        name=name,
        source_url=url,
        open_time=open_time,
        close_time=close_time,
        status="unverified",
        note=note,
    )


def unverified_events() -> list[OfficialEvent]:
    return [
        _unverified(
            "CME",
            "2001-09-13",
            "other",
            "September 13 2001: equity-index products closed (reported, not fetched)",
            WIKIPEDIA_9_11_URL,
            note=(
                "Still unverified after the 2026-10-14 round. Mondovisione CME "
                "release fetched for 2001-09-11/12 says non-equity floor/Globex "
                "resume 2001-09-13 and equity-index no earlier than 2001-09-14; "
                "it does not by itself type 2001-09-13 as an equity-index close. "
                + CME_EQUITY_INDEX_SCOPE_NOTE
            ),
        ),
        _unverified(
            "CME",
            "2027-06-18",
            "early_close",
            "Juneteenth National Independence Day (observed)",
            "https://github.com/rsheftel/pandas_market_calendars",
            open_time="2027-06-17T17:00",
            close_time="12:00",
        ),
        # Close clocks stay at the CMES 12:00 value. Do not take 12:15 from
        # broker (tier C) bulletins. Flagged for a CME tier-A check.
        _unverified(
            "CME",
            "2024-11-29",
            "early_close",
            "Early close",
            LIBRARY_SOURCE_URL,
            open_time="2024-11-28T17:00",
            close_time="12:00",
            note=CME_CLOCK_NOTE,
        ),
        _unverified(
            "CME",
            "2024-12-24",
            "early_close",
            "Early close",
            LIBRARY_SOURCE_URL,
            open_time="2024-12-23T17:00",
            close_time="12:00",
            note=CME_CLOCK_NOTE,
        ),
        _unverified(
            "CME",
            "2025-11-28",
            "early_close",
            "Early close",
            LIBRARY_SOURCE_URL,
            open_time="2025-11-27T17:00",
            close_time="12:00",
            note=CME_CLOCK_NOTE,
        ),
    ]


# The dataset takes CME early-close clocks from exchange_calendars CMES, which
# says 12:00 America/Chicago for these sessions. The CME Globex Equity calendar
# says 12:15. The CME Group page could not be read in this run, so the 12:15
# value is unverified and recorded as a disagreement.
UNVERIFIED_EARLY_CLOSE_CLOCKS: dict[tuple[str, str], str] = {
    ("CME", "2026-11-27"): "12:15",
    ("CME", "2026-12-24"): "12:15",
    ("CME", "2027-11-26"): "12:15",
}


# Library calendars that disagree with a fetched official fact, keyed by
# (exchange, date, library id). Each entry is a deliberate decision that the
# official notice wins. A conflict not listed here is unresolved and blocks the build.
RESOLVED_OFFICIAL_CONFLICTS: frozenset[tuple[str, str, str]] = frozenset(
    {
        # HKEX final cancellation notices for full-day sessions that these libraries traded.
        ("HKEX", "2020-10-13", "pandas_market_calendars:HKEX"),
        ("HKEX", "2021-10-13", "pandas_market_calendars:HKEX"),
        ("HKEX", "2023-09-01", "exchange_calendars:XHKG"),
        ("HKEX", "2023-09-08", "exchange_calendars:XHKG"),
        ("HKEX", "2024-09-06", "pandas_market_calendars:HKEX"),
        # HKEX 2021-04-06: XHKG closed, native HKEX open. Licensed Bloomberg
        # settlement calendar confirms a full closure (tier B).
        ("HKEX", "2021-04-06", "pandas_market_calendars:HKEX"),
        # HKEX half-days: native HKEX keeps a 16:00 close. Sourced rows are early_close 12:00.
        ("HKEX", "2024-12-31", "pandas_market_calendars:HKEX"),
        ("HKEX", "2025-01-28", "pandas_market_calendars:HKEX"),
        ("HKEX", "2025-12-24", "pandas_market_calendars:HKEX"),
        ("HKEX", "2025-12-31", "pandas_market_calendars:HKEX"),
        ("HKEX", "2026-02-16", "pandas_market_calendars:HKEX"),
        # KRX closures the XKRX library omitted. News cites the exchange notice.
        ("KRX", "2026-06-03", "exchange_calendars:XKRX"),
        ("KRX", "2026-07-17", "exchange_calendars:XKRX"),
        # CME 2025-01-09: CMES and CME_Equity treat the day as a full closure.
        # The fetched CME press release describes an abbreviated equity-index session.
        ("CME", "2025-01-09", "exchange_calendars:CMES"),
        ("CME", "2025-01-09", "pandas_market_calendars:CME_Equity"),
        # 2001-09-11/12: all three calendars treat the dates as sessions.
        ("CME", "2001-09-11", "exchange_calendars:CMES"),
        ("CME", "2001-09-11", "pandas_market_calendars:CME_Equity"),
        ("CME", "2001-09-11", "pandas_market_calendars:CME Globex Equity"),
        ("CME", "2001-09-12", "exchange_calendars:CMES"),
        ("CME", "2001-09-12", "pandas_market_calendars:CME_Equity"),
        ("CME", "2001-09-12", "pandas_market_calendars:CME Globex Equity"),
        # Abbreviated equity-index sessions: CMES (and sometimes CME_Equity)
        # still lists a full closure. Dataset follows the sourced early_close.
        ("CME", "2004-06-11", "exchange_calendars:CMES"),
        ("CME", "2004-06-11", "pandas_market_calendars:CME_Equity"),
        ("CME", "2004-06-11", "pandas_market_calendars:CME Globex Equity"),
        ("CME", "2007-01-02", "exchange_calendars:CMES"),
        ("CME", "2007-01-02", "pandas_market_calendars:CME_Equity"),
        ("CME", "2007-01-02", "pandas_market_calendars:CME Globex Equity"),
        ("CME", "2010-04-02", "exchange_calendars:CMES"),
        ("CME", "2012-04-06", "exchange_calendars:CMES"),
        ("CME", "2015-04-03", "exchange_calendars:CMES"),
        ("CME", "2018-12-05", "exchange_calendars:CMES"),
        ("CME", "2018-12-05", "pandas_market_calendars:CME_Equity"),
        ("CME", "2018-12-05", "pandas_market_calendars:CME Globex Equity"),
        ("CME", "2021-04-02", "exchange_calendars:CMES"),
        ("CME", "2023-04-07", "exchange_calendars:CMES"),
        ("CME", "2026-04-03", "exchange_calendars:CMES"),
        # Official Good Friday full closures: native calendars keep a session.
        ("CME", "2024-03-29", "pandas_market_calendars:CME_Equity"),
        ("CME", "2024-03-29", "pandas_market_calendars:CME Globex Equity"),
        ("CME", "2025-04-18", "pandas_market_calendars:CME_Equity"),
        ("CME", "2025-04-18", "pandas_market_calendars:CME Globex Equity"),
    }
)


def unverified_by_exchange() -> dict[str, list[OfficialEvent]]:
    grouped: dict[str, list[OfficialEvent]] = {}
    for event in unverified_events():
        grouped.setdefault(event.exchange, []).append(event)
    return grouped


def official_events() -> list[OfficialEvent]:
    return (
        _nyse_events()
        + _jpx_events()
        + _sse_events()
        + _hkex_events()
        + _secondary_news_events()
        + _cme_equity_index_events()
    )


def official_by_exchange() -> dict[str, list[OfficialEvent]]:
    grouped: dict[str, list[OfficialEvent]] = {}
    for event in official_events():
        grouped.setdefault(event.exchange, []).append(event)
    return grouped
