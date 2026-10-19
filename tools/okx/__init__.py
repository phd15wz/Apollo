"""Stage-1 OKX public market-data client. Keyless. Cannot trade.

Use ``PublicClient`` for REST market data, ``OkxPublicStream`` for the
public WebSocket, ``download_daily_trades`` for the official historical
zip archive, and ``handle_ties`` before Hawkes fits. There is no signing
path in this package.
"""

from tools.okx.archive import (
    DATA_DIR_ENV,
    DEFAULT_BUDGET_BYTES,
    DownloadResult,
    cache_usage_bytes,
    default_budget_bytes,
    default_data_dir,
    download_daily_trades,
    list_daily_trade_files,
    load_cached_trades,
    parse_trade_zip,
)
from tools.okx.client import PublicClient, RateLimiter
from tools.okx.errors import (
    CacheBudgetExceeded,
    HistoryWindowTooOld,
    LowDiskError,
    OkxError,
    OkxHttpError,
    PaginationBudgetExceeded,
    PrivateChannelError,
    UnsafeArchiveError,
    UnsafeEndpointError,
)
from tools.okx.hygiene import (
    TieReport,
    aggregate_by_timestamp_side,
    handle_ties,
    report_ties,
    stable_within_ms,
)
from tools.okx.market import candles_range, funding_rate_history_range, history_trades_range
from tools.okx.record import StreamRecorder
from tools.okx.stream import GapInfo, OkxPublicStream, StreamMessage, book_crc32, run_public_stream
from tools.okx.status import announcement_types, announcements, upcoming_maintenance
from tools.okx.types import (
    CANDLE_DTYPE,
    TRADE_DTYPE,
    Announcement,
    ArchiveFile,
    Candle,
    FundingRate,
    IndexTicker,
    Instrument,
    MaintenanceWindow,
    MarkPrice,
    OpenInterest,
    OrderBook,
    Ticker,
    Trade,
    candles_to_array,
    trades_to_array,
)

__all__ = [
    "CANDLE_DTYPE",
    "DATA_DIR_ENV",
    "DEFAULT_BUDGET_BYTES",
    "TRADE_DTYPE",
    "Announcement",
    "ArchiveFile",
    "CacheBudgetExceeded",
    "Candle",
    "GapInfo",
    "HistoryWindowTooOld",
    "LowDiskError",
    "DownloadResult",
    "FundingRate",
    "IndexTicker",
    "Instrument",
    "MaintenanceWindow",
    "MarkPrice",
    "OkxError",
    "OkxHttpError",
    "OkxPublicStream",
    "OpenInterest",
    "OrderBook",
    "PaginationBudgetExceeded",
    "PrivateChannelError",
    "PublicClient",
    "RateLimiter",
    "StreamMessage",
    "StreamRecorder",
    "Ticker",
    "TieReport",
    "Trade",
    "UnsafeArchiveError",
    "UnsafeEndpointError",
    "aggregate_by_timestamp_side",
    "announcement_types",
    "announcements",
    "book_crc32",
    "cache_usage_bytes",
    "candles_range",
    "candles_to_array",
    "default_budget_bytes",
    "default_data_dir",
    "download_daily_trades",
    "funding_rate_history_range",
    "handle_ties",
    "history_trades_range",
    "list_daily_trade_files",
    "load_cached_trades",
    "parse_trade_zip",
    "report_ties",
    "run_public_stream",
    "stable_within_ms",
    "trades_to_array",
    "upcoming_maintenance",
]
