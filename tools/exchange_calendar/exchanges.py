"""Exchange codes used in the dataset and the library calendars behind them."""

from dataclasses import dataclass


LIBRARY_SOURCE_URL = "https://github.com/gerrymanoim/exchange_calendars/tree/4.13.2"
PANDAS_MARKET_CALENDARS_URL = "https://github.com/rsheftel/pandas_market_calendars"

# History starts in 2001 so the 2001-09-11 US closure is inside the dataset.
# That is longer than the required 10 years. SSE and KRX stop at 2026 because
# the following year's schedule is announced around December and is not copied
# from library projections.
HISTORY_START = "2001-01-01"


@dataclass(frozen=True)
class Exchange:
    """One row-level exchange and the calendars that were actually compared."""

    code: str
    ec_code: str
    # Native pandas_market_calendars codes. Empty when the only mcal calendar
    # is a mirror of exchange_calendars and is not an independent source.
    mcal_codes: tuple[str, ...]
    independent_mcal: bool
    start: str
    end: str
    tz: str
    detect_late_opens: bool = False
    note: str = ""


EXCHANGES: dict[str, Exchange] = {
    "NYSE": Exchange(
        code="NYSE",
        ec_code="XNYS",
        mcal_codes=("NYSE",),
        independent_mcal=True,
        start=HISTORY_START,
        end="2028-12-31",
        tz="America/New_York",
        note="NYSE publishes 2026-2028 on its holiday page. The dataset includes those years.",
    ),
    "Nasdaq": Exchange(
        code="Nasdaq",
        ec_code="XNAS",
        mcal_codes=("NASDAQ",),
        # NASDAQ in pandas_market_calendars is NYSEExchangeCalendar, so it is not a second source.
        independent_mcal=False,
        start=HISTORY_START,
        end="2027-12-31",
        tz="America/New_York",
        note=(
            "exchange_calendars XNAS and pandas_market_calendars NASDAQ are aliases of "
            "the NYSE calendar classes. There is no distinct Nasdaq implementation."
        ),
    ),
    "CME": Exchange(
        code="CME",
        ec_code="CMES",
        mcal_codes=("CME_Equity", "CME Globex Equity"),
        independent_mcal=True,
        start=HISTORY_START,
        end="2027-12-31",
        tz="America/Chicago",
        note=(
            "Dataset base is exchange_calendars CMES (alias CME). "
            "pandas_market_calendars has no single CME calendar. "
            "CME_Equity and CME Globex Equity are the native calendars used for comparison."
        ),
    ),
    "HKEX": Exchange(
        code="HKEX",
        ec_code="XHKG",
        mcal_codes=("HKEX",),
        independent_mcal=True,
        start=HISTORY_START,
        end="2027-12-31",
        tz="Asia/Hong_Kong",
        note="Severe-weather rows are typed as typhoon_* only when an HKEX notice was fetched.",
    ),
    "SSE": Exchange(
        code="SSE",
        ec_code="XSHG",
        mcal_codes=("SSE",),
        independent_mcal=True,
        start=HISTORY_START,
        end="2026-12-31",
        tz="Asia/Shanghai",
        note="exchange_calendars records XSHG holidays only through 2026. 2027 is omitted.",
    ),
    "JPX": Exchange(
        code="JPX",
        ec_code="XTKS",
        mcal_codes=("JPX",),
        independent_mcal=True,
        start=HISTORY_START,
        end="2027-12-31",
        tz="Asia/Tokyo",
        note="Tokyo Stock Exchange cash market (TSE), calendar code XTKS / JPX.",
    ),
    "KRX": Exchange(
        code="KRX",
        ec_code="XKRX",
        mcal_codes=(),
        independent_mcal=False,
        start=HISTORY_START,
        end="2026-12-31",
        tz="Asia/Seoul",
        detect_late_opens=True,
        note=(
            "pandas_market_calendars XKRX is a mirror of exchange_calendars, "
            "so agreement is not a second source. 2027 projections are omitted."
        ),
    ),
}

EXCHANGE_CODES = tuple(EXCHANGES)
