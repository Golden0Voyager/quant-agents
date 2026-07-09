import logging
import os
import time
from typing import Annotated

import pandas as pd
import yfinance as yf
from stockstats import wrap
from yfinance.exceptions import YFRateLimitError

from .config import get_config
from .symbol_utils import NoMarketDataError, normalize_symbol
from .utils import safe_ticker_component

logger = logging.getLogger(__name__)

# A vendor's latest OHLCV row this many calendar days before the requested date
# is treated as stale. Generous enough to span long holiday weekends, tight
# enough to catch the year-old frames yfinance occasionally returns (#1021).
MAX_OHLCV_STALE_DAYS = 10


def yf_retry(func, max_retries=3, base_delay=2.0):
    """Execute a yfinance call with exponential backoff on rate limits.

    yfinance raises YFRateLimitError on HTTP 429 responses but does not
    retry them internally. This wrapper adds retry logic specifically
    for rate limits. Other exceptions propagate immediately.
    """
    for attempt in range(max_retries + 1):
        try:
            return func()
        except YFRateLimitError:
            if attempt < max_retries:
                delay = base_delay * (2**attempt)
                logger.warning(
                    f"Yahoo Finance rate limited, retrying in {delay:.0f}s (attempt {attempt + 1}/{max_retries})"
                )
                time.sleep(delay)
            else:
                raise


def _ensure_date_column(data: pd.DataFrame) -> pd.DataFrame:
    """Normalize the date column to ``Date``.

    Some yfinance builds leave the index unnamed (so ``reset_index()`` yields
    ``index``) or use ``Datetime`` for intraday data. Rename the first
    date-like column so indicators don't silently drop when it isn't ``Date``.
    """
    if "Date" in data.columns:
        return data
    for candidate in ("index", "Datetime", "date"):
        if candidate in data.columns:
            return data.rename(columns={candidate: "Date"})
    return data


def _clean_dataframe(data: pd.DataFrame) -> pd.DataFrame:
    """Normalize a stock DataFrame for stockstats: parse dates, drop invalid rows, fill price gaps."""
    # .copy() defensively since yfinance 1.2.0+ returns consolidated (read-only) DataFrames
    data = data.copy()
    data = _ensure_date_column(data)
    data["Date"] = pd.to_datetime(data["Date"], errors="coerce")
    data = data.dropna(subset=["Date"])

    price_cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in data.columns]
    data[price_cols] = data[price_cols].apply(pd.to_numeric, errors="coerce")
    data = data.dropna(subset=["Close"])
    data[price_cols] = data[price_cols].ffill().bfill()

    return data


def _coerce_ohlcv_dates(data: pd.DataFrame) -> pd.Series:
    """Return parsed dates from an OHLCV frame, whether Date is a column or the index."""
    if "Date" in data.columns:
        return pd.to_datetime(data["Date"], errors="coerce").dropna()
    # yfinance keeps the dates in the index (a DatetimeIndex, sometimes unnamed).
    if isinstance(data.index, pd.DatetimeIndex):
        return pd.Series(pd.to_datetime(data.index, errors="coerce")).dropna()
    # Fallback: expose the index and look for any date-like column.
    df = data.reset_index()
    for col in ("Date", "Datetime", "date", "index"):
        if col in df.columns:
            parsed = pd.to_datetime(df[col], errors="coerce").dropna()
            if not parsed.empty:
                return parsed
    return pd.Series(dtype="datetime64[ns]")


def _assert_ohlcv_not_stale(
    data: pd.DataFrame,
    curr_date: str,
    symbol: str,
    canonical: str | None = None,
    *,
    max_stale_days: int = MAX_OHLCV_STALE_DAYS,
) -> None:
    """Reject OHLCV whose latest row is far older than curr_date.

    Raises NoMarketDataError (with a stale-specific detail) so the router treats
    it like any other "no usable data from this vendor" — try the next vendor,
    then emit one clear unavailable signal. Empty frames are left to the
    caller's existing no-data handling; this guards only the dangerous case of
    present-but-stale rows (a vendor returning a year-old frame that would
    otherwise feed wrong prices to the agent, #1021).
    """
    if data is None or data.empty:
        return
    requested = pd.to_datetime(curr_date, errors="coerce")
    if pd.isna(requested):
        return
    requested = requested.normalize()
    dates = _coerce_ohlcv_dates(data)
    if dates.empty:
        return
    latest = dates.max().normalize()
    if hasattr(latest, "tz") and latest.tz is not None:
        latest = latest.tz_localize(None)
    stale_days = (requested - latest).days
    if stale_days > max_stale_days:
        raise NoMarketDataError(
            symbol,
            canonical,
            f"latest row is {latest.date()}, {stale_days} days before the "
            f"requested {requested.date()} (stale) — refusing to use it",
        )


def _load_ohlcv_from_smartmoney_db(
    symbol: str,
    start_date: str,
    end_date: str,
) -> pd.DataFrame | None:
    """Fetch OHLCV from local quant_core.db (zero network)."""
    from tradingagents.dataflows.akshare_common import is_a_share_ticker
    from tradingagents.dataflows.smartmoney_vendor import (
        _df_from_sql,
        _to_smartmoney_symbol,
    )

    if not is_a_share_ticker(symbol):
        return None

    # A-share local-DB fallback; not exercised by unit tests (no SQLite fixture yet).
    code = _to_smartmoney_symbol(symbol)  # pragma: no cover
    df = _df_from_sql(  # pragma: no cover  -- spans multi-line call
        """SELECT trade_date AS Date, open AS Open, high AS High,
                  low AS Low, close AS Close, volume AS Volume
           FROM daily_bars
           WHERE ts_code = ? AND trade_date BETWEEN ? AND ?
           ORDER BY trade_date""",
        (code, start_date, end_date),
    )
    return df  # _df_from_sql returns None on any error  # pragma: no cover


def _load_ohlcv_from_akshare(
    symbol: str,
    start_date: str,
    end_date: str,
) -> pd.DataFrame | None:
    """Fetch OHLCV data via akshare (Eastmoney network API) for A-share.

    Returns a DataFrame with Date/Open/High/Low/Close/Volume columns,
    or None on failure. Uses lazy imports to avoid circular dependencies.
    """
    try:
        import akshare as ak

        from tradingagents.dataflows.akshare_common import (
            _akshare_retry,
            is_a_share_ticker,
            no_proxy,
            to_akshare_symbol,
        )
    except ImportError:
        return None

    if not is_a_share_ticker(symbol):
        return None

    try:
        code = to_akshare_symbol(symbol, style="bare")
        ak_start = start_date.replace("-", "")
        ak_end = end_date.replace("-", "")

        with no_proxy():
            df = _akshare_retry(
                lambda: ak.stock_zh_a_hist(
                    symbol=code,
                    period="daily",
                    start_date=ak_start,
                    end_date=ak_end,
                    adjust="qfq",
                ),
                max_retries=3,
                base_delay=2.0,
            )

        if df is None or df.empty:
            return None

        col_map = {
            "日期": "Date",
            "开盘": "Open",
            "收盘": "Close",
            "最高": "High",
            "最低": "Low",
            "成交量": "Volume",
        }
        df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
        return df
    except Exception:
        logger.warning("akshare network fallback failed for %s", symbol, exc_info=True)
        return None


def _cache_covers_requested_date(cached: pd.DataFrame, curr_date: str) -> bool:
    """Return True if the cached frame already covers the requested date.

    A cache is considered adequate when its latest row is on or after the
    requested analysis date. For weekday ``curr_date`` values we try to refresh
    when the cache lags, because the market may have published a newer bar since
    the cache was written. For weekends we accept the last trading day's data
    without repeatedly hitting the vendor.
    """
    if cached is None or cached.empty or "Date" not in cached.columns:
        return False
    dates = pd.to_datetime(cached["Date"], errors="coerce").dropna()
    if dates.empty:
        return False
    cached_latest = dates.max().normalize()
    requested = pd.to_datetime(curr_date, errors="coerce").normalize()
    if pd.isna(requested):
        return True
    if cached_latest >= requested:
        return True
    # Cannot refresh future dates.
    today = pd.Timestamp.today().normalize()
    if requested > today:
        return True
    # Weekday: the requested bar may now be available, so refresh.
    # Weekend: accept the last available trading day.
    return requested.weekday() >= 5


def load_ohlcv(
    symbol: str,
    curr_date: str,
    lookback_years: int = 5,
    refresh: bool = False,
) -> pd.DataFrame:
    """Fetch OHLCV data with caching, filtered to prevent look-ahead bias.

    Downloads ``lookback_years`` of data up to today and caches per symbol.
    On subsequent calls the cache is reused. Rows after curr_date are
    filtered out so backtests never see future prices.

    For A-share tickers (``.SS`` / ``.SZ`` / ``.BJ``) with ``is_a_share_ticker``,
    tries local smartmoney DB first, then akshare (Eastmoney), then yfinance as
    last resort — yfinance rate-limits aggressively on batch runs and its A-share
    coverage is unreliable. Set ``DISABLE_YFINANCE_FALLBACK=1`` to skip yfinance
    entirely for A-share tickers.

    Args:
        symbol: Ticker symbol.
        curr_date: Analysis date used to filter look-ahead rows and to judge
            whether the on-disk cache is fresh enough.
        lookback_years: How many years of history to download.
        refresh: If True, bypass the on-disk cache and force a fresh download.
    """
    from tradingagents.dataflows.akshare_common import is_a_share_ticker

    canonical = normalize_symbol(symbol)
    safe_symbol = safe_ticker_component(canonical)

    config = get_config()
    curr_date_dt = pd.to_datetime(curr_date)

    # Cache uses a fixed window (5y to today) so one file per symbol.
    today_date = pd.Timestamp.today()
    start_date = today_date - pd.DateOffset(years=lookback_years)
    start_str = start_date.strftime("%Y-%m-%d")
    # yfinance ``end`` is EXCLUSIVE; request tomorrow so today's row is included
    # when curr_date is the current day (#986). Look-ahead is still prevented by
    # the curr_date filter below.
    end_str = (today_date + pd.Timedelta(days=1)).strftime("%Y-%m-%d")

    os.makedirs(config["data_cache_dir"], exist_ok=True)
    data_file = os.path.join(
        config["data_cache_dir"],
        f"{safe_symbol}-YFin-data-{start_str}-{end_str}.csv",
    )

    # A cached file may be empty if a prior fetch failed (unknown symbol,
    # transient rate limit). Treat an empty/columnless cache as a miss and
    # re-fetch rather than serving the poisoned file forever. Also re-fetch
    # when the caller forces a refresh or when the cache lags the requested
    # date on a weekday.
    data = None
    if not refresh and os.path.exists(data_file):
        cached = pd.read_csv(data_file, on_bad_lines="skip", encoding="utf-8")
        if not cached.empty and "Close" in cached.columns:
            if _cache_covers_requested_date(cached, curr_date):
                data = cached
            else:
                logger.info(
                    "Cache for %s lags requested date %s; refreshing",
                    symbol,
                    curr_date,
                )

    if data is None:
        downloaded = None
        is_a_share = is_a_share_ticker(canonical)

        if is_a_share:
            # A-share: local smartmoney DB → akshare → yfinance (last resort)
            downloaded = _load_ohlcv_from_smartmoney_db(canonical, start_str, end_str)
            if downloaded is None:
                logger.info(
                    "smartmoney_db returned no data for A-share %s, trying akshare",
                    symbol,
                )
                downloaded = _load_ohlcv_from_akshare(canonical, start_str, end_str)
            if downloaded is None and os.getenv("DISABLE_YFINANCE_FALLBACK") != "1":
                logger.info(
                    "akshare returned no data for A-share %s, falling back to yfinance",
                    symbol,
                )
                try:
                    downloaded = yf_retry(
                        lambda: yf.download(
                            canonical,
                            start=start_str,
                            end=end_str,
                            multi_level_index=False,
                            progress=False,
                            auto_adjust=True,
                        )
                    )
                    downloaded = _ensure_date_column(downloaded.reset_index())
                    if downloaded.empty or "Close" not in downloaded.columns:
                        downloaded = None
                except Exception:
                    logger.warning(
                        "yfinance fallback failed for A-share %s",
                        symbol,
                        exc_info=True,
                    )
                    downloaded = None
        else:
            # Non-A-share: yfinance first (existing behavior)
            try:
                downloaded = yf_retry(
                    lambda: yf.download(
                        canonical,
                        start=start_str,
                        end=end_str,
                        multi_level_index=False,
                        progress=False,
                        auto_adjust=True,
                    )
                )
                downloaded = _ensure_date_column(downloaded.reset_index())
                if downloaded.empty or "Close" not in downloaded.columns:
                    downloaded = None
            except Exception:
                logger.warning(
                    "yfinance failed for %s",
                    symbol,
                    exc_info=True,
                )
                downloaded = None

        if downloaded is None or downloaded.empty or "Close" not in downloaded.columns:
            raise NoMarketDataError(symbol, canonical, "No data returned from any vendor")
        downloaded.to_csv(data_file, index=False, encoding="utf-8")
        data = downloaded

    data = _clean_dataframe(data)

    # Filter to curr_date to prevent look-ahead bias in backtesting
    data = data[data["Date"] <= curr_date_dt]

    # Reject a stale frame (latest row far older than curr_date) rather than
    # feeding year-old prices into indicators (#1021).
    _assert_ohlcv_not_stale(data, curr_date, symbol, canonical)

    return data


def filter_financials_by_date(data: pd.DataFrame, curr_date: str) -> pd.DataFrame:
    """Drop financial statement columns (fiscal period timestamps) after curr_date.

    yfinance financial statements use fiscal period end dates as columns.
    Columns after curr_date represent future data and are removed to
    prevent look-ahead bias.
    """
    if not curr_date or data.empty:
        return data
    cutoff = pd.Timestamp(curr_date)
    mask = pd.to_datetime(data.columns, errors="coerce") <= cutoff
    return data.loc[:, mask]


class StockstatsUtils:
    @staticmethod
    def get_stock_stats(
        symbol: Annotated[str, "ticker symbol for the company"],
        indicator: Annotated[str, "quantitative indicators based off of the stock data for the company"],
        curr_date: Annotated[str, "curr date for retrieving stock price data, YYYY-mm-dd"],
    ):
        data = load_ohlcv(symbol, curr_date)
        df = wrap(data).copy()
        df["Date"] = df["Date"].dt.strftime("%Y-%m-%d")
        curr_date_str = pd.to_datetime(curr_date).strftime("%Y-%m-%d")

        df[indicator]  # trigger stockstats to calculate the indicator
        matching_rows = df[df["Date"].str.startswith(curr_date_str)]

        if not matching_rows.empty:
            indicator_value = matching_rows[indicator].values[0]
            return indicator_value
        else:
            return "N/A: Not a trading day (weekend or holiday)"
