import contextlib
import io
import logging
import os
import time
from functools import partial
from typing import Annotated

import pandas as pd
import yfinance as yf
from stockstats import wrap
from yfinance.exceptions import YFRateLimitError

from tradingagents.dataflows.freshness import trading_sessions_between

from .config import get_config
from .symbol_utils import NoMarketDataError, normalize_symbol
from .utils import safe_ticker_component

logger = logging.getLogger(__name__)

# A vendor's latest OHLCV row missing this many *trading sessions* before the
# requested date is treated as stale. Session lag stops growing across
# weekends and holidays (no sessions, no growth), so the budget is both
# tighter than the old 10-calendar-day rule in the stall case (a mid-week
# pipeline outage trips it within ~3 sessions) and immune to the Spring
# Festival / National Day false positives that a calendar budget suffers.
MAX_OHLCV_STALE_SESSIONS = 3


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


def _silent_yf_download(*args, **kwargs):
    """Call yf.download with stdout suppressed to hide 'Failed download' messages.

    yfinance uses print() internally for failure reporting; redirect_stdout
    silences these without affecting Rich's Live rendering (which runs in the
    main thread and writes to the layout data structure, not sys.stdout directly).
    """
    with contextlib.redirect_stdout(io.StringIO()):
        return yf.download(*args, **kwargs)


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

    # Forward-fill missing OHLC prices; never back-fill with future prices.
    ohlc_cols = [c for c in ["Open", "High", "Low", "Close"] if c in data.columns]
    if ohlc_cols:
        data[ohlc_cols] = data[ohlc_cols].ffill()
    if "Volume" in data.columns:
        data["Volume"] = data["Volume"].fillna(0)

    # Drop rows that still lack a close (e.g. leading NaNs before any price).
    data = data.dropna(subset=["Close"])

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
    max_stale_sessions: int = MAX_OHLCV_STALE_SESSIONS,
) -> None:
    """Reject OHLCV whose latest row misses too many trading sessions.

    Raises NoMarketDataError (with a stale-specific detail) so the router treats
    it like any other "no usable data from this vendor" — try the next vendor,
    then emit one clear unavailable signal. Empty frames are left to the
    caller's existing no-data handling; this guards only the dangerous case of
    present-but-stale rows (a vendor returning a year-old frame that would
    otherwise feed wrong prices to the agent, #1021). Unparseable dates or a
    broken session calendar never block — the guard degrades to serving data.
    """
    if data is None or data.empty:
        return
    requested = pd.to_datetime(curr_date, errors="coerce")
    if pd.isna(requested):
        return
    dates = _coerce_ohlcv_dates(data)
    if dates.empty:
        return
    latest = dates.max().normalize()
    if hasattr(latest, "tz") and latest.tz is not None:
        latest = latest.tz_localize(None)
    missing = trading_sessions_between(str(latest.date()), str(requested.date())[:10])
    if missing is None:
        return
    if missing >= max_stale_sessions:
        raise NoMarketDataError(
            symbol,
            canonical,
            f"latest row is {latest.date()}, missing {missing} trading "
            f"sessions before the requested {requested.date()} (stale) — "
            f"refusing to use it",
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


def _load_ohlcv_from_global_db(
    symbol: str,
    start_date: str,
    end_date: str,
) -> pd.DataFrame | None:
    """Fetch US-stock / crypto OHLCV from local quant_core.db (zero network).

    Reads the ``global_assets_bars`` table (e.g. AAPL, NVDA, BTC-USD).
    Returns None when the symbol is not archived locally so the caller
    falls back to the online vendor.
    """
    from tradingagents.dataflows.akshare_common import is_a_share_ticker
    from tradingagents.dataflows.smartmoney_vendor import _df_from_sql

    if is_a_share_ticker(symbol):
        return None

    df = _df_from_sql(
        """SELECT trade_date AS Date, open AS Open, high AS High,
                  low AS Low, close AS Close, volume AS Volume
           FROM global_assets_bars
           WHERE ts_code = ? AND trade_date BETWEEN ? AND ?
           ORDER BY trade_date""",
        (symbol.upper(), start_date, end_date),
    )
    if df is None or df.empty:
        return None
    return df


def _load_ohlcv_from_akshare(
    symbol: str,
    start_date: str,
    end_date: str,
) -> pd.DataFrame | None:
    """Fetch OHLCV data via akshare for A-share.

    Tries three sources in order:
      1. Eastmoney (``ak.stock_zh_a_hist``) — primary
      2. Tencent (``ak.stock_zh_a_hist_tx``) — bypasses Eastmoney's
         TLS fingerprinting / anti-bot blocks
      3. Sina (``ak.stock_zh_a_daily``) — additional fallback

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

    ak_start = start_date.replace("-", "")
    ak_end = end_date.replace("-", "")

    # source config: (label, symbol_style, fetch_callable, column_map)
    sources = (
        (
            "Eastmoney",
            "bare",
            lambda code: ak.stock_zh_a_hist(
                symbol=code,
                period="daily",
                start_date=ak_start,
                end_date=ak_end,
                adjust="qfq",
            ),
            {"日期": "Date", "开盘": "Open", "收盘": "Close",
             "最高": "High", "最低": "Low", "成交量": "Volume"},
        ),
        (
            "Tencent",
            "lower_prefix",
            lambda code: ak.stock_zh_a_hist_tx(
                symbol=code,
                start_date=ak_start,
                end_date=ak_end,
                adjust="qfq",
            ),
            {"date": "Date", "open": "Open", "close": "Close",
             "high": "High", "low": "Low", "amount": "Volume"},
        ),
        (
            "Sina",
            "lower_prefix",
            lambda code: ak.stock_zh_a_daily(
                symbol=code,
                start_date=ak_start,
                end_date=ak_end,
                adjust="qfq",
            ),
            {"date": "Date", "open": "Open", "close": "Close",
             "high": "High", "low": "Low", "volume": "Volume"},
        ),
    )

    for idx, (name, style, fetch, col_map) in enumerate(sources):
        try:
            code = to_akshare_symbol(symbol, style=style)

            with no_proxy():
                df = _akshare_retry(
                    partial(fetch, code),
                    max_retries=2,
                    base_delay=1.0,
                )

            if df is not None and not df.empty:
                df = df.rename(
                    columns={k: v for k, v in col_map.items() if k in df.columns}
                )
                return df

            logger.info("akshare %s returned empty for %s", name, symbol)
        except Exception:
            if idx < len(sources) - 1:
                logger.info(
                    "akshare %s failed for %s, trying next source", name, symbol
                )
            else:
                logger.warning(
                    "akshare all sources failed for %s", symbol, exc_info=True
                )

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
    dates = pd.to_datetime(cached["Date"], errors="coerce", format="%Y-%m-%d").dropna()
    if dates.empty:
        return False
    cached_latest = dates.max().normalize()
    requested = pd.to_datetime(curr_date, errors="coerce")
    if pd.isna(requested):
        return True
    requested = requested.normalize()
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

    For non-A-share tickers (US stocks, crypto), tries the local quant_core.db
    ``global_assets_bars`` table first and falls back to online yfinance when
    the local archive is missing or stale (latest row lags ``curr_date`` by
    more than 5 calendar days; A-share staleness is measured in trading
    sessions via ``MAX_OHLCV_STALE_SESSIONS``).

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
            if downloaded is not None:
                try:
                    _assert_ohlcv_not_stale(downloaded, curr_date, symbol, canonical)
                except NoMarketDataError:
                    logger.info(
                        "smartmoney_db OHLCV for %s is stale relative to %s; "
                        "falling back to akshare",
                        symbol,
                        curr_date,
                    )
                    downloaded = None
            if downloaded is None:
                logger.info(
                    "smartmoney_db returned no usable data for A-share %s, trying akshare",
                    symbol,
                )
                downloaded = _load_ohlcv_from_akshare(canonical, start_str, end_str)
            if downloaded is None:
                logger.info(
                    "akshare returned no data for A-share %s, no yfinance fallback "
                    "(yfinance A-share coverage is unreliable and rate-limits aggressively)",
                    symbol,
                )
        else:
            # Non-A-share: local quant_core.db global_assets_bars first
            # (US stocks / crypto archived by quant_pipeline), yfinance as
            # online fallback when local data is missing or stale.
            downloaded = _load_ohlcv_from_global_db(canonical, start_str, end_str)
            if downloaded is not None:
                try:
                    _assert_ohlcv_not_stale(downloaded, curr_date, symbol, canonical)
                except NoMarketDataError:
                    logger.info(
                        "quant_core.db global OHLCV for %s is stale relative to %s; "
                        "falling back to yfinance",
                        symbol,
                        curr_date,
                    )
                    downloaded = None
            if downloaded is None:
                try:
                    downloaded = yf_retry(
                        lambda: _silent_yf_download(
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

        # Validate freshness before writing to cache; a stale DB frame must not
        # poison the on-disk cache (P1-4).
        _assert_ohlcv_not_stale(downloaded, curr_date, symbol, canonical)
        downloaded.to_csv(data_file, index=False, encoding="utf-8")
        data = downloaded

    data = _clean_dataframe(data)

    # Filter to curr_date to prevent look-ahead bias in backtesting
    data = data[data["Date"] <= curr_date_dt]

    # Reject a stale frame (latest row far older than curr_date) rather than
    # feeding year-old prices into indicators (#1021).
    _assert_ohlcv_not_stale(data, curr_date, symbol, canonical)

    return data


def load_index_ohlcv(
    symbol: str,
    start_date: str,
    end_date: str,
) -> pd.DataFrame | None:
    """Fetch A-share index daily OHLCV for return attribution benchmarks.

    Tries the local quant_core.db ``index_daily`` table first, then akshare
    (``ak.index_zh_a_hist``) as an online fallback. yfinance is deliberately
    not consulted: it rate-limits aggressively on A-share index history, which
    used to fail every ``_fetch_returns`` benchmark lookup on batch runs.

    Args:
        symbol: Index code in yfinance style (``000001.SS``), quant_core.db
            style (``sh000001``), or bare (``000001``).
        start_date: Window start ``YYYY-MM-DD`` (inclusive).
        end_date: Window end ``YYYY-MM-DD`` (inclusive).

    Returns:
        Ascending DataFrame with Date/Open/High/Low/Close/Volume columns, or
        None when neither source has data for the window.
    """
    from tradingagents.dataflows.smartmoney_vendor import get_index_daily_df

    df = get_index_daily_df(symbol, start_date, end_date)
    if df is not None:
        return df

    try:
        import akshare as ak

        from tradingagents.dataflows.akshare_common import no_proxy
    except ImportError:
        return None

    bare = symbol.split(".")[0]
    ak_start = start_date.replace("-", "")
    ak_end = end_date.replace("-", "")
    try:
        with no_proxy():
            raw = ak.index_zh_a_hist(
                symbol=bare,
                period="daily",
                start_date=ak_start,
                end_date=ak_end,
            )
    except Exception as exc:
        logger.info("akshare index fetch failed for %s: %s", symbol, exc)
        return None

    if raw is None or raw.empty:
        return None

    column_map = {
        "日期": "Date", "开盘": "Open", "收盘": "Close",
        "最高": "High", "最低": "Low", "成交量": "Volume",
    }
    missing = [col for col in ("日期", "收盘") if col not in raw.columns]
    if missing:
        logger.info(
            "akshare index response for %s missing columns %s", symbol, missing
        )
        return None

    out = raw.rename(columns=column_map)[
        [c for c in ("Date", "Open", "High", "Low", "Close", "Volume") if c in raw.rename(columns=column_map).columns]
    ].copy()
    out["Date"] = pd.to_datetime(out["Date"], errors="coerce").dt.strftime("%Y-%m-%d")
    return out.sort_values("Date").reset_index(drop=True)


def filter_financials_by_date(data: pd.DataFrame, curr_date: str | None) -> pd.DataFrame:
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
