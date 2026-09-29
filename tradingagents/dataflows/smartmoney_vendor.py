"""SmartMoney DB vendor — read A-share and global-asset data from local SQLite.

This vendor provides a zero-latency fallback layer for A-share tickers by
reading from the shared quant_core.db maintained by quant_hunter. It also
serves US-stock / crypto OHLCV (AAPL, NVDA, BTC-USD, …) from the
``global_assets_bars`` table via :func:`get_global_asset_data`, which is
registered under the separate ``quant_db_global`` vendor name (the router
skips the ``smartmoney_db`` name for non-A-share tickers).

Placement in the fallback chain:
    quant_core.db → akshare → yfinance

If quant_core.db has no data for a symbol, ``route_to_vendor`` automatically
falls back to the next vendor in the chain.
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
from datetime import datetime
from typing import Annotated, Any

import pandas as pd

from tradingagents.dataflows.errors import NoMarketDataError
from tradingagents.dataflows.freshness import (
    expected_report_period,
    trading_sessions_between,
)
from tradingagents.dataflows.sw_industry_map import get_sw_industry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared database path (centralised in ~/Code/quant_data/)
# ---------------------------------------------------------------------------
DEFAULT_DB_PATH = os.path.expanduser("~/Code/quant_data/quant_core.db")
_DB_PATH = os.getenv("QUANT_DB_PATH", DEFAULT_DB_PATH)

# ---------------------------------------------------------------------------
# Domain constants
# ---------------------------------------------------------------------------
# Industries known for the "low-PE trap" at cyclical peaks: very low PE
# (< 8) in these sectors often signals a profit top, NOT cheap valuation.
# Used by get_historical_valuation() to inject a guard warning.
CYCLICAL_INDUSTRIES: frozenset[str] = frozenset({
    "钢铁", "煤炭", "煤炭开采", "航运", "海运",
    "化工", "基础化工", "有色金属", "猪肉", "养殖业",
})


def _to_smartmoney_symbol(symbol: str) -> str:
    """Convert TradingAgents ticker format to quant_core.db ts_code format.

    quant_core.db stores tickers as bare numeric codes (e.g. 600519, 000001)
    without exchange suffixes.

    Examples:
        600519.SS  → 600519
        300454.SZ  → 300454
        000001.SZ  → 000001
        688981.SS  → 688981
    """
    # Strip any exchange suffix (.SS, .SZ, .BJ, .SH)
    bare = symbol.split(".")[0]
    return bare


def _get_connection():
    """Open a read-only SQLite connection to quant_core.db."""
    if not os.path.exists(_DB_PATH):
        raise FileNotFoundError(f"quant_core.db not found at {_DB_PATH}")
    return sqlite3.connect(_DB_PATH)


def _df_from_sql(query: str, params: tuple = ()) -> pd.DataFrame | None:
    """Execute SQL and return a DataFrame, or None on any error."""
    try:
        with _get_connection() as conn:
            return pd.read_sql_query(query, conn, params=params)
    except Exception as exc:
        logger.debug("quant_core.db query failed: %s", exc)
        return None


# ===========================================================================
# Core stock APIs
# ===========================================================================

def get_stock_data(
    symbol: Annotated[str, "A-share ticker e.g. 600519.SS"],
    start_date: Annotated[str, "Start date YYYY-MM-DD"],
    end_date: Annotated[str, "End date YYYY-MM-DD"],
) -> str:
    """Fetch A-share daily OHLCV from local quant_core.db (forward-adjusted)."""
    code = _to_smartmoney_symbol(symbol)

    df = _df_from_sql(
        """
        SELECT trade_date AS Date, open AS Open, high AS High,
               low AS Low, close AS Close, volume AS Volume
        FROM daily_bars
        WHERE ts_code = ? AND trade_date BETWEEN ? AND ?
        ORDER BY trade_date DESC
        """,
        (code, start_date, end_date),
    )

    if df is None or df.empty:
        # Benchmark indices (e.g. 399001.SZ) may be requested as tickers.
        # Try the index table before giving up on the local DB fallback layer.
        try:
            return get_index_daily(symbol, start_date, end_date)
        except (NoMarketDataError, RuntimeError):
            pass

        raise RuntimeError(
            f"No data in quant_core.db for {symbol} between {start_date} and {end_date}"
        )

    df = df.set_index("Date")
    for col in ("Open", "High", "Low", "Close"):
        df[col] = df[col].round(2)

    header = (
        f"# Stock data for {symbol.upper()} from {start_date} to {end_date}\n"
        f"# Total records: {len(df)}\n"
        f"# Source: quant_core.db (local SQLite, 前复权)\n"
        f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    )
    return header + df.to_csv()


# ===========================================================================
# Global assets (US stocks / crypto) — global_assets_bars table
# ===========================================================================

def get_global_asset_data(
    symbol: Annotated[str, "Global ticker e.g. AAPL, NVDA, BTC-USD"],
    start_date: Annotated[str, "Start date YYYY-MM-DD"],
    end_date: Annotated[str, "End date YYYY-MM-DD"],
) -> str:
    """Fetch US-stock / crypto daily OHLCV from local quant_core.db.

    Reads the ``global_assets_bars`` table maintained by quant_pipeline
    (schema: ts_code, trade_date, open, high, low, close, adj_close, volume).
    A-share tickers are rejected immediately — they belong to ``daily_bars``.

    Freshness guard: when the latest local row lags ``end_date`` by more
    than 5 calendar days, raises ``NoMarketDataError``
    so ``route_to_vendor`` falls back to the next vendor (online yfinance).
    """
    from tradingagents.dataflows.akshare_common import is_a_share_ticker

    if is_a_share_ticker(symbol):
        raise NoMarketDataError(
            symbol, symbol,
            "A-share tickers are stored in daily_bars, not global_assets_bars.",
        )

    # US / crypto markets: no single session calendar fits both (crypto trades
    # 365d), so this path keeps a calendar-day budget — tightened from the old
    # 10 days, which let a week of stale prices through silently.
    _GLOBAL_ASSETS_STALE_DAYS = 5

    code = symbol.upper()
    df = _df_from_sql(
        """
        SELECT trade_date AS Date, open AS Open, high AS High,
               low AS Low, close AS Close, volume AS Volume
        FROM global_assets_bars
        WHERE ts_code = ? AND trade_date BETWEEN ? AND ?
        ORDER BY trade_date DESC
        """,
        (code, start_date, end_date),
    )

    if df is None or df.empty:
        raise NoMarketDataError(
            symbol, code,
            f"No data in quant_core.db global_assets_bars for {symbol} "
            f"between {start_date} and {end_date}.",
        )

    latest = pd.to_datetime(df["Date"], errors="coerce").max()
    end = pd.to_datetime(end_date, errors="coerce")
    if pd.notna(latest) and pd.notna(end):
        stale_days = (end.normalize() - latest.normalize()).days
        if stale_days > _GLOBAL_ASSETS_STALE_DAYS:
            raise NoMarketDataError(
                symbol, code,
                f"global_assets_bars latest row is {latest.date()}, "
                f"{stale_days} days before the requested {end_date} (stale) — "
                f"refusing to use it",
            )

    df = df.set_index("Date")
    for col in ("Open", "High", "Low", "Close"):
        df[col] = df[col].round(2)

    header = (
        f"# Stock data for {code} from {start_date} to {end_date}\n"
        f"# Total records: {len(df)}\n"
        f"# Source: quant_core.db global_assets_bars (local SQLite)\n"
        f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    )
    return header + df.to_csv()


# ===========================================================================
# Technical indicators
# ===========================================================================

def get_indicators(
    symbol: Annotated[str, "A-share ticker"],
    indicator: Annotated[str, "stockstats indicator name e.g. rsi_14, macd"],
    curr_date: Annotated[str, "Current date YYYY-MM-DD"],
    look_back_days: Annotated[int, "How many trading days to report"],
) -> str:
    """Read pre-computed indicators from quant_core.db.

    Supports indicators stored in the ``indicators`` table:
        ma5, ma10, ma20, ma60, vol_ma5, vol_ma50, vol_ma60,
        boll_upper, boll_mid, boll_lower, boll_bandwidth, cyc60,
        chip_concentration, macd_dif, macd_dea, macd_hist,
        kdj_k, kdj_d, kdj_j, rsi6, rsi12, rsi24, cci

    For indicators not stored locally (e.g. rsi_14), this raises
    ``RuntimeError`` so ``route_to_vendor`` falls back to akshare.
    """
    from stockstats import wrap

    code = _to_smartmoney_symbol(symbol)

    # Map common TradingAgents indicator names to our column names
    _INDICATOR_MAP = {
        "rsi_6": "rsi6",
        "rsi_12": "rsi12",
        "rsi_24": "rsi24",
        "rsi_14": None,  # not pre-computed; will fall back
        # MACD naming follows the stockstats/verified-snapshot convention:
        # macd = DIF line, macds = DEA signal line, macdh = histogram.
        # ("macd" used to map to macd_hist, which made get_indicators disagree
        # with the snapshot and triggered spurious data-conflict warnings.)
        "macd": "macd_dif",
        "macds": "macd_dea",
        "macdh": "macd_hist",
        "macd_dif": "macd_dif",
        "macd_dea": "macd_dea",
        "macd_hist": "macd_hist",
        "kdj_k": "kdj_k",
        "kdj_d": "kdj_d",
        "kdj_j": "kdj_j",
        "cci": "cci",
        "close": "close",
        "volume": "volume",
    }

    col_name = _INDICATOR_MAP.get(indicator, indicator)

    # Pull enough history to compute missing indicators via stockstats
    end = datetime.strptime(curr_date, "%Y-%m-%d")
    start = (end - pd.Timedelta(days=look_back_days * 2 + 260)).strftime("%Y-%m-%d")

    # Pull OHLCV from daily_bars + pre-computed indicators from indicators table
    df_ohlcv = _df_from_sql(
        """
        SELECT trade_date AS Date, open AS Open, high AS High,
               low AS Low, close AS Close, volume AS Volume
        FROM daily_bars
        WHERE ts_code = ? AND trade_date BETWEEN ? AND ?
        ORDER BY trade_date
        """,
        (code, start, curr_date),
    )

    df_ind = _df_from_sql(
        """
        SELECT trade_date AS Date, close AS Close, volume AS Volume,
               ma5, ma10, ma20, ma60, vol_ma5, vol_ma50, vol_ma60,
               boll_upper, boll_mid, boll_lower, boll_bandwidth, cyc60,
               chip_concentration, macd_dif, macd_dea, macd_hist,
               kdj_k, kdj_d, kdj_j, rsi6, rsi12, rsi24, cci
        FROM indicators
        WHERE ts_code = ? AND trade_date BETWEEN ? AND ?
        ORDER BY trade_date
        """,
        (code, start, curr_date),
    )

    if df_ohlcv is None or df_ohlcv.empty:
        raise RuntimeError(f"No indicator data in quant_core.db for {symbol}")

    # Use OHLCV as base; merge pre-computed indicators if available
    df = df_ohlcv.copy()
    df["Date"] = pd.to_datetime(df["Date"])

    if df_ind is not None and not df_ind.empty:
        df_ind["Date"] = pd.to_datetime(df_ind["Date"])
        # Merge pre-computed columns (skip Date/Close/Volume duplicates)
        merge_cols = [c for c in df_ind.columns if c not in ("Date", "Close", "Volume")]
        df = df.merge(df_ind[["Date"] + merge_cols], on="Date", how="left")

    # If the requested indicator is pre-computed, return it directly
    if col_name and col_name in df.columns:
        df = df.set_index("Date")
        tail = df.tail(look_back_days)
        lines = [
            f"## {indicator} values for {symbol.upper()} "
            f"(last {look_back_days} trading days, source: quant_core.db)\n"
        ]
        for idx, row in tail.iterrows():
            v = row[col_name]
            lines.append(f"{idx.strftime('%Y-%m-%d')}: {'N/A' if pd.isna(v) else v}")
        return "\n".join(lines)

    # Otherwise try to compute via stockstats (requires full OHLCV).
    # stockstats-compute fallback: exercised only when callers request an indicator
    # that is neither pre-computed in ``quant_core.db`` nor in the internal map.
    # No unit test currently drives it; add one when wiring a full DB fixture.
    stats = wrap(df)  # pragma: no cover
    stats["Date"] = stats["Date"].dt.strftime("%Y-%m-%d")  # pragma: no cover
    try:  # pragma: no cover
        stats[indicator]  # trigger calculation  # pragma: no cover
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            f"Indicator '{indicator}' not available in quant_core.db and "
            f"stockstats could not compute it: {exc}"
        ) from exc  # pragma: no cover

    tail = stats.tail(look_back_days)  # pragma: no cover  -- stockstats-compute tail block
    lines = [  # pragma: no cover
        f"## {indicator} values for {symbol.upper()} "
        f"(last {look_back_days} trading days, source: quant_core.db + stockstats)\n"
    ]
    for _, row in tail.iterrows():  # pragma: no cover
        v = row[indicator]  # pragma: no cover
        lines.append(f"{row['Date']}: {'N/A' if pd.isna(v) else v}")  # pragma: no cover
    return "\n".join(lines)  # pragma: no cover


# ===========================================================================
# Fundamental data
# ===========================================================================

def get_fundamentals(
    symbol: Annotated[str, "A-share ticker"],
    curr_date: Annotated[str, "Current date YYYY-MM-DD"],
) -> str:
    """Read fundamentals from quant_core.db (PE, PB, ROE, etc.)."""
    code = _to_smartmoney_symbol(symbol)

    # Get company name from stock_list
    name_df = _df_from_sql(
        "SELECT name, industry FROM stock_list WHERE code = ?",
        (code.split(".")[0],),
    )
    company_name = name_df["name"].iloc[0] if name_df is not None and not name_df.empty else code
    industry = name_df["industry"].iloc[0] if name_df is not None and not name_df.empty else "N/A"

    # Get latest fundamentals on or before curr_date
    if curr_date:
        df = _df_from_sql(
            """
            SELECT * FROM fundamentals
            WHERE ts_code = ? AND trade_date <= ?
            ORDER BY trade_date DESC
            LIMIT 1
            """,
            (code, curr_date),
        )
    else:
        df = _df_from_sql(
            """
            SELECT * FROM fundamentals
            WHERE ts_code = ?
            ORDER BY trade_date DESC
            LIMIT 1
            """,
            (code,),
        )

    if df is None or df.empty:
        raise RuntimeError(f"No fundamentals in quant_core.db for {symbol}")

    row = df.iloc[0]
    lines = [
        f"# Fundamentals for {symbol.upper()} ({company_name}) as of {curr_date}",
        "# Source: quant_core.db (local SQLite)",
        "",
        f"- 股票简称: {company_name}",
        f"- 行业: {industry}",
    ]

    # Valuation metrics
    for col, label, fmt in [
        ("pe_ttm", "PE(TTM)", ".2f"),
        ("pb", "PB", ".2f"),
        ("ps_ttm", "PS(TTM)", ".2f"),
        ("dividend_yield", "股息率", ".2f%"),
        ("market_cap", "总市值", ",.0f"),
    ]:
        v = row.get(col)
        if pd.notna(v):
            if "cap" in col:
                lines.append(f"- {label}: {v/1e8:{fmt}} 亿 (≈{v/1e9:.2f} billion CNY)")
            elif "%" in fmt:
                # quant_core.db stores dividend_yield as a percent number already
                lines.append(f"- {label}: {v:.2f}%")
            else:
                lines.append(f"- {label}: {v:{fmt}}")

    # Profitability metrics
    lines.append("")
    lines.append("## 盈利能力")
    for col, label in [
        ("roe", "ROE"),
        ("roa", "ROA"),
        ("gross_margin", "毛利率"),
        ("net_margin", "净利率"),
    ]:
        v = row.get(col)
        if pd.notna(v):
            lines.append(f"- {label}: {v:.2f}%")

    # Growth metrics
    lines.append("")
    lines.append("## 成长性")
    for col, label in [
        ("revenue_growth", "营收同比增长"),
        ("profit_growth", "净利润同比增长"),
        ("eps_growth", "EPS同比增长"),
        ("peg", "PEG"),
    ]:
        v = row.get(col)
        if pd.notna(v):
            if col == "peg":
                lines.append(f"- {label}: {v:.2f}")
            else:
                lines.append(f"- {label}: {v:.2f}%")

    # Debt
    v = row.get("debt_ratio")
    if pd.notna(v):
        lines.append("")
        lines.append("## 偿债能力")
        lines.append(f"- 资产负债率: {v:.2f}%")

    return "\n".join(lines)


def get_balance_sheet(
    symbol: Annotated[str, "A-share ticker"],
    freq: str = "quarterly",
    curr_date: str | None = None,
) -> str:
    """Read key balance-sheet metrics from quant_core.db quarterly_financials.

    Covers debt ratio, book value per share, and operating cash flow.
    Falls back to akshare if not available locally.
    """
    code = _to_smartmoney_symbol(symbol)
    df = _df_from_sql(
        """
        SELECT report_period, debt_ratio, bps, operating_cashflow
        FROM quarterly_financials
        WHERE ts_code = ?
        ORDER BY report_period DESC
        LIMIT 1
        """,
        (code,),
    )
    if df is None or df.empty:
        raise RuntimeError(
            "Balance sheet data not available in quant_core.db. "
            "Route to_vendor will fall back to akshare."
        )

    row = df.iloc[0]
    period = row["report_period"]
    metrics: list[str] = []
    for col, label, fmt in [
        ("debt_ratio", "资产负债率", ".2f%"),
        ("bps", "每股净资产", ".2f"),
        ("operating_cashflow", "经营活动现金流净额", ",.0f"),
    ]:
        v = row.get(col)
        if pd.notna(v):
            if "%" in fmt:
                metrics.append(f"- {label}: {v:.2f}%")
            else:
                metrics.append(f"- {label}: {v:{fmt}}")
    if not metrics:
        # 行存在但指标列全空 — 空壳会阻断链路 fallback (hithink/akshare)，
        # 必须按无数据抛出让路由继续下探 (20260826 批次 001316/000603/688239 中招)
        raise NoMarketDataError(
            symbol, detail="local quarterly_financials row has only null metrics"
        )
    # 指标可用再查时效：报告期早于应披露期时抛给线上 vendor 补数
    _assert_quarterly_not_stale(symbol, period, curr_date)
    lines = [
        f"# Balance Sheet for {symbol.upper()} (截至 {period})",
        "# Source: quant_core.db (local SQLite, quarterly_financials)",
        "",
        *metrics,
    ]
    return "\n".join(lines)


def get_cashflow(
    symbol: Annotated[str, "A-share ticker"],
    freq: str = "quarterly",
    curr_date: str | None = None,
) -> str:
    """Read operating cash flow from quant_core.db quarterly_financials.

    Only operating_cashflow is available from quarterly_financials — this
    is not a full cash flow statement. Falls back to akshare if not available
    locally.
    """
    code = _to_smartmoney_symbol(symbol)
    df = _df_from_sql(
        """
        SELECT report_period, operating_cashflow
        FROM quarterly_financials
        WHERE ts_code = ?
        ORDER BY report_period DESC
        LIMIT 1
        """,
        (code,),
    )
    if df is None or df.empty:
        raise RuntimeError(
            "Cashflow statement not available in quant_core.db. "
            "Route to_vendor will fall back to akshare."
        )

    row = df.iloc[0]
    period = row["report_period"]
    v = row.get("operating_cashflow")
    if not pd.notna(v):
        # 同上: 空壳会阻断 fallback，必须抛出 (见 get_balance_sheet)
        raise NoMarketDataError(
            symbol, detail="local quarterly_financials row has null operating_cashflow"
        )
    # 指标可用再查时效：报告期早于应披露期时抛给线上 vendor 补数
    _assert_quarterly_not_stale(symbol, period, curr_date)
    lines = [
        f"# Operating Cash Flow for {symbol.upper()} (截至 {period})",
        "# Source: quant_core.db (local SQLite, quarterly_financials)",
        "",
        f"- 经营活动现金流净额: {v:,.0f}",
    ]
    return "\n".join(lines)


def get_income_statement(
    symbol: Annotated[str, "A-share ticker"],
    freq: str = "quarterly",
    curr_date: str | None = None,
) -> str:
    """Read income-statement metrics from quant_core.db quarterly_financials.

    Covers revenue, net profit, margins, growth rates, EPS, and ROE.
    Falls back to akshare if not available locally.
    """
    code = _to_smartmoney_symbol(symbol)
    df = _df_from_sql(
        """
        SELECT report_period, revenue, net_profit, gross_margin, net_margin,
               revenue_growth, profit_growth, eps, roe
        FROM quarterly_financials
        WHERE ts_code = ?
        ORDER BY report_period DESC
        LIMIT 1
        """,
        (code,),
    )
    if df is None or df.empty:
        raise RuntimeError(
            "Income statement not available in quant_core.db. "
            "Route to_vendor will fall back to akshare."
        )

    row = df.iloc[0]
    period = row["report_period"]
    metrics: list[str] = []
    for col, label, fmt in [
        ("revenue", "营业总收入", ",.0f"),
        ("net_profit", "净利润", ",.0f"),
        ("gross_margin", "毛利率", ".2f"),
        ("net_margin", "净利率", ".2f"),
        ("revenue_growth", "营收同比增长", ".2f%"),
        ("profit_growth", "净利润同比增长", ".2f%"),
        ("eps", "基本每股收益", ".2f"),
        ("roe", "ROE", ".2f"),
    ]:
        v = row.get(col)
        if pd.notna(v):
            if "%" in fmt:
                metrics.append(f"- {label}: {v:.2f}%")
            else:
                metrics.append(f"- {label}: {v:{fmt}}")
    if not metrics:
        # 同上: 空壳会阻断 fallback，必须抛出 (见 get_balance_sheet)
        raise NoMarketDataError(
            symbol, detail="local quarterly_financials row has only null metrics"
        )
    # 指标可用再查时效：报告期早于应披露期时抛给线上 vendor 补数
    _assert_quarterly_not_stale(symbol, period, curr_date)
    lines = [
        f"# Income Statement for {symbol.upper()} (截至 {period})",
        "# Source: quant_core.db (local SQLite, quarterly_financials)",
        "",
        *metrics,
    ]
    return "\n".join(lines)


# ===========================================================================
# Fund flow
# ===========================================================================

def get_fund_flow(
    symbol: str,
    curr_date: str | None = None,
) -> str:
    """Fetch A-share individual stock fund flow from quant_core.db."""
    code = _to_smartmoney_symbol(symbol)

    params = [code]
    date_filter = ""
    if curr_date:
        date_filter = " AND trade_date <= ?"
        params.append(curr_date)

    df = _df_from_sql(
        f"""
        SELECT trade_date AS Date, main_net_inflow AS main_net,
               main_net_inflow_pct AS main_pct,
               super_large_net_inflow AS super_large_net,
               super_large_net_inflow_pct AS super_large_pct,
               large_net_inflow AS large_net,
               large_net_inflow_pct AS large_pct,
               is_simulated
        FROM fund_flow
        WHERE ts_code = ?{date_filter}
        ORDER BY trade_date DESC
        LIMIT 5
        """,
        tuple(params),
    )

    if df is None or df.empty:
        raise RuntimeError(f"No fund flow data in quant_core.db for {symbol}")

    _assert_local_data_not_stale("fund_flow", symbol, df, "Date", curr_date)

    lines = [
        f"## {symbol.upper()} Fund Flow (source: quant_core.db / local SQLite)",
        f"Total records: {len(df)} trading days",
        "",
    ]

    if df["is_simulated"].any():
        lines.append("_Note: some data is simulated (generated when real data was unavailable)_")
        lines.append("")

    for _, row in df.iterrows():
        lines.append(f"**Date**: {row['Date']}")
        lines.append(
            f"- Main Force Net Inflow: {row['main_net']:,.0f} "
            f"({row['main_pct']:.2f}%)"
        )
        lines.append(
            f"- Super Large Order Net Inflow: {row['super_large_net']:,.0f} "
            f"({row['super_large_pct']:.2f}%)"
        )
        lines.append(
            f"- Large Order Net Inflow: {row['large_net']:,.0f} "
            f"({row['large_pct']:.2f}%)"
        )
        lines.append("")

    return "\n".join(lines)


# ===========================================================================
# Index data
# ===========================================================================

def _to_index_code(symbol: str) -> str:
    """Convert index ticker to quant_core.db index_code format.

    quant_core.db stores index codes with exchange prefix (e.g. sh000001, sz399001).

    Examples:
        000001.SS  → sh000001
        399001.SZ  → sz399001
        399006.SZ  → sz399006
        000688.SS  → sh000688
        sh000001   → sh000001  (already correct)
    """
    # Already in correct format
    if symbol.startswith(("sh", "sz")):
        return symbol
    # Strip exchange suffix and add prefix
    bare = symbol.split(".")[0]
    suffix = symbol.split(".")[-1].upper() if "." in symbol else ""
    if suffix == "SS":
        return f"sh{bare}"
    elif suffix == "SZ":
        return f"sz{bare}"
    # Fallback: guess by code prefix (000/888 → sh, 399 → sz)
    if bare.startswith(("000", "888", "688")):
        return f"sh{bare}"
    return f"sz{bare}"


def get_index_daily_df(
    index_code: str,
    start_date: str,
    end_date: str,
) -> pd.DataFrame | None:
    """Return ``index_daily`` rows as an ascending DataFrame, or None.

    Columns: Date, Open, High, Low, Close, Volume. Unlike
    :func:`get_index_daily` this never raises — callers that only need the
    rows (e.g. return attribution) treat a miss as a fallback signal.
    """
    code = _to_index_code(index_code)
    df = _df_from_sql(
        """
        SELECT trade_date AS Date, open AS Open, high AS High,
               low AS Low, close AS Close, volume AS Volume
        FROM index_daily
        WHERE index_code = ? AND trade_date BETWEEN ? AND ?
        ORDER BY trade_date ASC
        """,
        (code, start_date, end_date),
    )
    if df is None or df.empty:
        return None
    return df


def get_index_daily(
    index_code: Annotated[
        str,
        "A-share index code e.g. 000001.SS (SSE Composite), 399001.SZ (SZSE Component), "
        "399006.SZ (ChiNext), 000688.SS (STAR Market). Exchange suffix is normalised to "
        "the quant_core.db index_code format (sh000001, sz399001, etc.).",
    ],
    start_date: Annotated[str, "Start date YYYY-MM-DD"],
    end_date: Annotated[str, "End date YYYY-MM-DD"],
) -> str:
    """Fetch A-share index daily OHLCV from quant_core.db.

    Reads the ``index_daily`` table for major A-share indices. Schema:
        index_code TEXT, index_name TEXT, trade_date TEXT, open REAL, high REAL,
        low REAL, close REAL, volume REAL

    Returns a CSV-formatted OHLCV table for the requested index and date range.

    - Empty result (no rows for the requested code/date range): raises
      ``NoMarketDataError`` so ``route_to_vendor`` returns ``NO_DATA_AVAILABLE``.
    - Query failure / schema mismatch / missing table: also raises
      ``NoMarketDataError``. Because ``get_index_daily`` only has the
      ``smartmoney_db`` vendor (no fallback), a hard crash would abort the
      agent call; degrading gracefully keeps the pipeline alive.
    """
    code = _to_index_code(index_code)

    df = _df_from_sql(
        """
        SELECT trade_date AS Date, open AS Open, high AS High,
               low AS Low, close AS Close, volume AS Volume
        FROM index_daily
        WHERE index_code = ? AND trade_date BETWEEN ? AND ?
        ORDER BY trade_date DESC
        """,
        (code, start_date, end_date),
    )

    if df is None:
        raise NoMarketDataError(
            index_code, index_code,
            f"index_daily query failed for {index_code} between {start_date} and {end_date}."
        )

    if df.empty:
        raise NoMarketDataError(
            index_code, index_code,
            f"No index_daily data in quant_core.db between {start_date} and {end_date}."
        )

    required_cols = {"Date", "Open", "High", "Low", "Close", "Volume"}
    missing_cols = required_cols - set(df.columns)
    if missing_cols:
        raise NoMarketDataError(
            index_code, index_code,
            f"index_daily schema mismatch for {index_code}: missing columns {sorted(missing_cols)}."
        )

    numeric_cols = ("Open", "High", "Low", "Close", "Volume")
    for col in numeric_cols:
        if not pd.api.types.is_numeric_dtype(df[col]):
            raise NoMarketDataError(
                index_code, index_code,
                f"index_daily schema mismatch for {index_code}: column {col!r} is not numeric."
            )

    df = df.set_index("Date")
    for col in ("Open", "High", "Low", "Close"):
        df[col] = df[col].round(2)

    header = (
        f"# Index data for {index_code.upper()} from {start_date} to {end_date}\n"
        f"# Total records: {len(df)}\n"
        f"# Source: quant_core.db (local SQLite)\n\n"
    )
    return header + df.to_csv()


# ===========================================================================
# Market breadth
# ===========================================================================

def get_limit_up_down(trade_date: str) -> str:
    """Fetch market-wide limit-up/limit-down stats for a trading date.

    Reads the ``limit_up_down`` table which stores per-stock records:
        trade_date TEXT, ts_code TEXT, name TEXT, pct_change REAL,
        close_price REAL, turnover_rate REAL, limit_type TEXT (涨停/跌停),
        board_count INTEGER (连板数), industry TEXT

    Aggregates into a market sentiment summary with:
    - Total limit-up / limit-down counts
    - Board-count distribution (连板分布) for limit-up stocks
    - Top industries by limit-up count
    - Sample stock names

    Returns a markdown summary for the Market Analyst / Sentiment Analyst
    to gauge short-term market emotion.
    """
    # Aggregate counts by limit_type
    df_counts = _df_from_sql(
        """
        SELECT limit_type, COUNT(*) AS cnt
        FROM limit_up_down
        WHERE trade_date = ?
        GROUP BY limit_type
        """,
        (trade_date,),
    )

    if df_counts is None or df_counts.empty:
        raise NoMarketDataError(
            trade_date, trade_date,
            f"No limit-up/limit-down data in quant_core.db for {trade_date}."
        )

    counts = dict(zip(df_counts["limit_type"], df_counts["cnt"], strict=True))
    limit_up_count = counts.get("涨停", 0)
    limit_down_count = counts.get("跌停", 0)

    lines = [
        f"## A-Share Limit-Up / Limit-Down Stats for {trade_date} "
        f"(source: quant_core.db / local SQLite)",
        "",
        f"- **Limit-up stocks (涨停)**: {limit_up_count}",
        f"- **Limit-down stocks (跌停)**: {limit_down_count}",
        f"- **Up/Down ratio**: {limit_up_count}:{limit_down_count}",
    ]

    # Board-count distribution (连板分布) — only for limit-up
    df_boards = _df_from_sql(
        """
        SELECT board_count, COUNT(*) AS cnt, GROUP_CONCAT(name, ', ') AS stocks
        FROM limit_up_down
        WHERE trade_date = ? AND limit_type = '涨停' AND board_count IS NOT NULL
        GROUP BY board_count
        ORDER BY board_count DESC
        """,
        (trade_date,),
    )

    if df_boards is not None and not df_boards.empty:
        lines.append("")
        lines.append("**连板分布 (Board-count distribution):**")
        for _, row in df_boards.iterrows():
            bc = int(row["board_count"]) if pd.notna(row["board_count"]) else 1
            cnt = int(row["cnt"])
            label = f"{bc}连板" if bc > 1 else "首板"
            stocks = str(row.get("stocks", ""))[:80]
            lines.append(f"- {label}: {cnt} 只 ({stocks}...)")

    # Top industries by limit-up count
    df_industry = _df_from_sql(
        """
        SELECT industry, COUNT(*) AS cnt
        FROM limit_up_down
        WHERE trade_date = ? AND limit_type = '涨停' AND industry IS NOT NULL AND industry != ''
        GROUP BY industry
        ORDER BY cnt DESC
        LIMIT 5
        """,
        (trade_date,),
    )

    if df_industry is not None and not df_industry.empty:
        lines.append("")
        lines.append("**涨停行业分布 (Top industries):**")
        for _, row in df_industry.iterrows():
            lines.append(f"- {row['industry']}: {int(row['cnt'])} 只")

    # Sample limit-up stocks (top 10 by board_count)
    df_up_sample = _df_from_sql(
        """
        SELECT name, board_count, industry
        FROM limit_up_down
        WHERE trade_date = ? AND limit_type = '涨停'
        ORDER BY board_count DESC, turnover_rate ASC
        LIMIT 10
        """,
        (trade_date,),
    )

    if df_up_sample is not None and not df_up_sample.empty:
        lines.append("")
        lines.append("**Sample limit-up stocks:**")
        for _, row in df_up_sample.iterrows():
            bc = int(row["board_count"]) if pd.notna(row["board_count"]) else 1
            bc_str = f" ({bc}连板)" if bc > 1 else ""
            ind = f" [{row['industry']}]" if pd.notna(row.get("industry")) and row["industry"] else ""
            lines.append(f"- {row['name']}{bc_str}{ind}")

    # Sample limit-down stocks
    df_down_sample = _df_from_sql(
        """
        SELECT name, industry
        FROM limit_up_down
        WHERE trade_date = ? AND limit_type = '跌停'
        LIMIT 10
        """,
        (trade_date,),
    )

    if df_down_sample is not None and not df_down_sample.empty:
        lines.append("")
        lines.append("**Sample limit-down stocks:**")
        for _, row in df_down_sample.iterrows():
            ind = f" [{row['industry']}]" if pd.notna(row.get("industry")) and row["industry"] else ""
            lines.append(f"- {row['name']}{ind}")

    return "\n".join(lines)


# ===========================================================================
# News / Governance — not stored in quant_core.db
# ===========================================================================

def get_news(
    symbol: str,
    start_date: str,
    end_date: str,
) -> str:
    raise RuntimeError("News not available in quant_core.db")


def get_insider_transactions(symbol: str) -> str:
    raise RuntimeError("Insider transactions not available in quant_core.db")


def get_company_announcements(
    symbol: str,
    start_date: str,
    end_date: str,
) -> str:
    raise RuntimeError("Company announcements not available in quant_core.db")


def get_restricted_release(symbol: str) -> str:
    raise RuntimeError("Restricted release not available in quant_core.db")


def get_institutional_holdings(
    symbol: str,
    curr_date: str | None = None,
) -> str:
    """Fetch institutional-holdings (机构持股) data from quant_core.db."""
    code = _to_smartmoney_symbol(symbol)

    params = [code]
    date_filter = ""
    if curr_date:
        date_filter = " AND report_date <= ?"
        params.append(curr_date)

    df = _df_from_sql(
        f"""
        SELECT report_date AS Date, institution_count, type_counts
        FROM institutional_holdings
        WHERE ts_code = ?{date_filter}
        ORDER BY report_date DESC
        LIMIT 5
        """,
        tuple(params),
    )

    if df is None or df.empty:
        raise RuntimeError(
            f"No institutional-holdings data in quant_core.db for {symbol}"
        )

    lines = [
        f"## {symbol.upper()} Institutional Holdings (机构持股) "
        f"(source: quant_core.db / local SQLite)",
        f"Total records: {len(df)} report periods",
        "",
    ]
    for _, row in df.iterrows():
        lines.append(f"**Report Date**: {row['Date']}")
        ic = row['institution_count']
        lines.append(f"- 机构数量: {f'{ic:,.0f}' if pd.notna(ic) else 'N/A'}")
        tc = row.get("type_counts")
        if tc and str(tc).strip() and str(tc) != "nan":
            import json

            try:
                parsed = json.loads(tc) if isinstance(tc, str) else tc
                if isinstance(parsed, dict) and parsed:
                    for k, v in parsed.items():
                        lines.append(f"  - {k}: {v}")
            except (json.JSONDecodeError, TypeError):
                pass
        lines.append("")
    return "\n".join(lines)


def get_northbound_hold(
    symbol: str,
    curr_date: str | None = None,
) -> str:
    """Fetch northbound (Stock Connect) holdings for an A-share from quant_core.db.

    Reads the ``north_hold`` table — quarter-end snapshots of foreign-investor
    (northbound / HKEX Stock Connect) holdings per stock. Since 2024-08-19 the
    exchanges stopped daily per-stock disclosure and publish quarter-end
    snapshots instead, so this is the authoritative post-2024 source.

    The expected schema is:
        ts_code TEXT, security_name TEXT, trade_date DATE,
        close_price REAL, hold_shares REAL, hold_market_cap REAL,
        hold_shares_ratio REAL, free_shares_ratio REAL, total_shares_ratio REAL

    - Missing/empty result or schema failure: raises ``NoMarketDataError`` so
      ``route_to_vendor`` returns ``NO_DATA_AVAILABLE`` (or falls back to the
      next configured vendor, e.g. akshare).
    """
    code = _to_smartmoney_symbol(symbol)

    params = [code]
    date_filter = ""
    if curr_date:
        date_filter = " AND trade_date <= ?"
        params.append(curr_date)

    df = _df_from_sql(
        f"""
        SELECT trade_date AS Date, security_name, close_price,
               hold_shares, hold_market_cap, hold_shares_ratio,
               free_shares_ratio, total_shares_ratio
        FROM north_hold
        WHERE ts_code = ?{date_filter}
        ORDER BY trade_date DESC
        LIMIT 8
        """,
        tuple(params),
    )

    if df is None or df.empty:
        raise NoMarketDataError(
            symbol, symbol,
            "No northbound holding data in quant_core.db for the requested symbol."
        )

    name = ""
    first_name = df.iloc[0].get("security_name")
    if pd.notna(first_name) and str(first_name).strip():
        name = f" ({str(first_name).strip()})"

    lines = [
        f"## {symbol.upper()}{name} Northbound (Stock Connect) Holdings "
        f"(source: quant_core.db / local SQLite)",
        f"Quarter-end snapshots: {len(df)} periods (latest first)",
        "",
    ]

    # Quarter-over-quarter trend on the two most recent snapshots.
    if len(df) >= 2:
        latest = df.iloc[0].get("hold_shares")
        prev = df.iloc[1].get("hold_shares")
        if pd.notna(latest) and pd.notna(prev) and prev:
            chg = latest - prev
            pct = chg / prev * 100
            direction = "增持" if chg > 0 else ("减持" if chg < 0 else "持平")
            lines.append(
                f"**QoQ change (季度环比)**: {direction} "
                f"{chg:+,.0f} shares ({pct:+.2f}%)"
            )
            lines.append("")

    for _, row in df.iterrows():
        lines.append(f"**Report date**: {row['Date']}")
        v = row.get("hold_shares")
        if pd.notna(v):
            lines.append(f"- Hold shares: {v:,.0f}")
        v = row.get("hold_market_cap")
        if pd.notna(v):
            lines.append(f"- Hold market cap: {v / 1e8:,.2f} 亿元")
        v = row.get("free_shares_ratio")
        if pd.notna(v):
            lines.append(f"- % of free float: {v:.2f}%")
        v = row.get("total_shares_ratio")
        if pd.notna(v):
            lines.append(f"- % of total shares: {v:.2f}%")
        v = row.get("close_price")
        if pd.notna(v):
            lines.append(f"- Close price: {v:,.2f}")
        lines.append("")

    return "\n".join(lines)


def get_industry_valuation(symbol: str) -> str:
    """Read industry valuation comparison from quant_core.db.

    Uses three local tables:
      1. stock_list — resolve the stock's industry
      2. sector_industry — industry average PE/PB/PS
      3. historical_valuation — the stock's own valuation history

    Falls back to akshare if no data is available locally.
    """
    code = _to_smartmoney_symbol(symbol)

    name_df = _df_from_sql(
        "SELECT name, industry FROM stock_list WHERE code = ?",
        (code,),
    )
    if name_df is None or name_df.empty:
        raise RuntimeError(
            "Industry valuation not available in quant_core.db. "
            "Route to_vendor will fall back to akshare."
        )
    company_name = name_df["name"].iloc[0]
    industry = name_df["industry"].iloc[0]

    sector_df = _df_from_sql(
        """
        SELECT avg_pe, avg_pb, avg_ps, avg_roe, avg_revenue_growth,
               avg_profit_growth, total_market_cap
        FROM sector_industry
        WHERE industry_name = ?
        ORDER BY trade_date DESC
        LIMIT 1
        """,
        (industry,),
    )

    stock_df = _df_from_sql(
        """
        SELECT trade_date AS Date, pe_ttm, pb, ps_ttm, dividend_yield
        FROM historical_valuation
        WHERE ts_code = ?
        ORDER BY trade_date DESC
        LIMIT 1
        """,
        (code,),
    )

    if (sector_df is None or sector_df.empty) and (stock_df is None or stock_df.empty):
        raise RuntimeError(
            "Industry valuation not available in quant_core.db. "
            "Route to_vendor will fall back to akshare."
        )

    lines = [
        f"# {symbol.upper()} ({company_name}) Industry Valuation Comparison",
        "# Source: quant_core.db (local SQLite, sector_industry + historical_valuation)",
        "",
        f"## {symbol.upper()} vs {industry} Industry",
        "",
    ]

    if stock_df is not None and not stock_df.empty:
        sr = stock_df.iloc[0]
        lines.append("**Target Stock Metrics:**")
        for col, label, fmt in [
            ("pe_ttm", "PE(TTM)", ".2f"),
            ("pb", "PB", ".2f"),
            ("ps_ttm", "PS(TTM)", ".2f"),
        ]:
            v = sr.get(col)
            if pd.notna(v):
                lines.append(f"- {label}: {v:{fmt}}")
        v = sr.get("dividend_yield")
        if pd.notna(v):
            lines.append(f"- 股息率: {v:.2f}%")
        lines.append("")

    if sector_df is not None and not sector_df.empty:
        ind = sector_df.iloc[0]
        lines.append("**Industry Average Metrics:**")
        for col, label, fmt in [
            ("avg_pe", "平均 PE", ".2f"),
            ("avg_pb", "平均 PB", ".2f"),
            ("avg_ps", "平均 PS", ".2f"),
        ]:
            v = ind.get(col)
            if pd.notna(v):
                lines.append(f"- {label}: {v:{fmt}}")
        v = ind.get("avg_roe")
        if pd.notna(v):
            lines.append(f"- 平均 ROE: {v:.2f}%")
        v = ind.get("total_market_cap")
        if pd.notna(v):
            lines.append(f"- 行业总市值: {v:,.0f}")
        lines.append("")

    if (
        sector_df is not None
        and not sector_df.empty
        and stock_df is not None
        and not stock_df.empty
    ):
        ind = sector_df.iloc[0]
        sr = stock_df.iloc[0]
        pe_ttm = sr.get("pe_ttm")
        avg_pe = ind.get("avg_pe")
        if pd.notna(pe_ttm) and pd.notna(avg_pe) and avg_pe > 0:
            ratio = pe_ttm / avg_pe
            if ratio < 0.8:
                verdict = "below industry average (可能低估)"
            elif ratio > 1.2:
                verdict = "above industry average (可能高估)"
            else:
                verdict = "in line with industry average (估值合理)"
            lines.append(
                f"**Valuation Verdict**: PE(TTM) {pe_ttm:.2f} vs industry "
                f"{avg_pe:.2f} — {ratio:.2f}x, {verdict}"
            )

    return "\n".join(lines)


def get_earnings_estimates(symbol: str, curr_date: str | None = None) -> str:
    """Fetch the latest locally archived earnings forecast for *symbol*.

    ``earnings_forecast`` is the normalized local table used by the data
    pipeline.  Keep the method name for tool compatibility, while exposing
    the archived pre-announcement data instead of unconditionally reporting
    that estimates are unavailable.
    """
    code = _to_smartmoney_symbol(symbol)
    date_filter = ""
    params: list[str] = [code]
    if curr_date:
        date_filter = " AND end_date <= ?"
        params.append(curr_date)

    df = _df_from_sql(
        f"""
        SELECT name, end_date, forecast_type, net_profit_change, previous_profit
        FROM earnings_forecast
        WHERE ts_code = ?{date_filter}
        ORDER BY end_date DESC
        LIMIT 5
        """,
        tuple(params),
    )

    if df is None or df.empty:
        raise NoMarketDataError(
            symbol,
            detail=f"no earnings forecast data in quant_core.db for {symbol}",
        )

    lines = [
        f"## {symbol.upper()} Earnings Forecast (业绩预告)",
        "Source: quant_core.db / earnings_forecast",
        "",
    ]
    for _, row in df.iterrows():
        change = row.get("net_profit_change")
        change_str = f"{change:+.2f}%" if pd.notna(change) else "N/A"
        lines.append(
            f"- **报告期 {row.get('end_date', 'N/A')}**: "
            f"类型 [{row.get('forecast_type', 'N/A')}], "
            f"预计净利润同比变动 {change_str}"
        )
    return "\n".join(lines)


def get_macro_indicators(
    indicator: str = "",
    curr_date: str | None = None,
    look_back_days: int | None = None,
) -> str:
    """Read China macro indicators from quant_core.db.

    Reads from local tables:
      - macro_monthly → CPI, PMI, M2, retail sales, industrial production, LPR
      - macro_quarterly → GDP
      - money_market → SHIBOR, repo rates, PBOC policy rate
      - market_valuation → equity-bond spread (股债利差), market PE/PB median
      - us_treasury → US & China treasury yields, 10y-2y spread

    Raises RuntimeError for unsupported indicators so route_to_vendor can
    fall back to akshare or FRED.
    """
    indicator = indicator.lower().strip()
    max_rows = _resolve_lookback(look_back_days) if look_back_days else 12

    # ------------------------------------------------------------------
    # macro_monthly table — monthly China macro (CPI, PMI, M2, etc.)
    # ------------------------------------------------------------------
    monthly_dispatch: dict[str, dict] = {
        "cpi": {
            "cols": ["cpi_yoy", "cpi_mom", "cpi_core_yoy", "ppi_yoy", "ppi_mom"],
            "title": "China CPI & PPI",
        },
        "pmi": {
            "cols": ["pmi", "pmi_yoy", "pmi_caixin", "pmi_mom", "pmi_monthly_change"],
            "title": "China PMI (Manufacturing)",
        },
        "m2": {
            "cols": ["m2", "m2_yoy", "m1_yoy", "m0_yoy", "new_loans", "new_loans_yoy"],
            "title": "China Money Supply (M2)",
        },
        "retail_sales": {
            "cols": ["retail_sales_yoy", "retail_sales_ytd_yoy"],
            "title": "China Retail Sales",
        },
        "fixed_asset_investment": {
            "cols": ["fixed_asset_investment_yoy", "fixed_asset_investment_ytd_yoy"],
            "title": "China Fixed Asset Investment",
        },
        "industrial_production": {
            "cols": ["industrial_production_yoy", "industrial_production_ytd_yoy"],
            "title": "China Industrial Production",
        },
        "consumer_confidence": {
            "cols": ["consumer_confidence", "consumer_satisfaction", "consumer_expectation"],
            "title": "China Consumer Confidence",
        },
        "lpr": {
            "cols": ["lpr_1y", "lpr_5y"],
            "title": "China Loan Prime Rate (LPR)",
        },
    }

    if indicator in monthly_dispatch:
        return _macro_from_table(
            "macro_monthly", monthly_dispatch[indicator], max_rows
        )

    if indicator in ("social_finance", "社融"):
        # social_finance not directly in macro_monthly; fall through to akshare
        raise RuntimeError(
            "Social financing not available in quant_core.db as a standalone column."
        )

    # ------------------------------------------------------------------
    # macro_quarterly table — GDP
    # ------------------------------------------------------------------
    if indicator == "gdp":
        return _macro_from_table(
            "macro_quarterly",
            {
                "cols": ["gdp", "gdp_yoy", "gdp_qoq", "gdp_primary", "gdp_secondary", "gdp_tertiary"],
                "title": "China GDP",
            },
            12 if look_back_days is None else max_rows,
        )

    # ------------------------------------------------------------------
    # money_market table — SHIBOR, repo rates, PBOC policy rate
    # ------------------------------------------------------------------
    if indicator in ("shibor", "interbank"):
        return _macro_from_table(
            "money_market",
            {
                "cols": [
                    "shibor_on", "shibor_1w", "shibor_2w", "shibor_1m",
                    "shibor_3m", "shibor_6m", "shibor_1y",
                    "fr001", "fr007", "pboc_policy_rate",
                ],
                "title": "China Money Market Rates (SHIBOR & Repo)",
            },
            _resolve_lookback(look_back_days) if look_back_days else 60,
        )

    # ------------------------------------------------------------------
    # market_valuation table — equity-bond spread, market PE/PB median
    # ------------------------------------------------------------------
    if indicator in ("equity_bond_spread", "ebs"):
        return _macro_from_table(
            "market_valuation",
            {
                "cols": [
                    "pe_median", "pe_quantile", "pb_median", "pb_quantile",
                    "equity_bond_spread", "ebs_ma", "csi300_close",
                ],
                "title": "A-Share Market Valuation & Equity-Bond Spread (股债利差)",
            },
            _resolve_lookback(look_back_days) if look_back_days else 60,
        )

    # ------------------------------------------------------------------
    # us_treasury table — US & China yield curves
    # ------------------------------------------------------------------
    if indicator in ("10y_treasury", "yield_curve", "cn_10y", "treasury_yield"):
        return _macro_from_table(
            "us_treasury",
            {
                "cols": [
                    "us_2y", "us_5y", "us_10y", "us_30y",
                    "cn_2y", "cn_5y", "cn_10y", "cn_30y",
                    "spread_10y_2y",
                ],
                "title": "US & China Treasury Yields",
                "date_col": "trade_date",
            },
            _resolve_lookback(look_back_days) if look_back_days else 60,
        )

    # Unsupported — let fallback chain try akshare / FRED.
    raise RuntimeError(
        f"Indicator '{indicator}' not available in quant_core.db. "
        "route_to_vendor will fall back to akshare or FRED."
    )


def _resolve_lookback(look_back_days: int | None) -> int:
    if look_back_days is None:
        return 12
    if look_back_days >= 365 * 5:
        return 60
    if look_back_days >= 365:
        return 24
    if look_back_days >= 180:
        return 12
    return max(6, look_back_days // 30)


def _macro_from_table(table: str, spec: dict, limit: int) -> str:
    """Read *limit* rows and format as markdown. spec keys: cols, title, date_col."""
    date_col = spec.get("date_col", "date")
    col_list = ", ".join(spec["cols"])
    df = _df_from_sql(
        f"SELECT {date_col} AS date, {col_list} FROM \"{table}\" ORDER BY {date_col} DESC LIMIT ?",
        (limit,),
    )

    if df is None or df.empty:
        raise RuntimeError(
            f"No macro data in quant_core.db for table '{table}'."
        )

    lines = [
        f"## {spec['title']} (source: quant_core.db / local SQLite)",
        f"Total records: {len(df)}",
        "---",
        "",
    ]

    for _, row in df.iterrows():
        date_val = row.get("date", "")
        parts = [f"**{date_val}**"]
        for col in spec["cols"]:
            v = row.get(col)
            if pd.notna(v):
                if isinstance(v, (float, int)):
                    parts.append(f"  {col}: {v:.2f}")
                else:
                    parts.append(f"  {col}: {v}")
        lines.append("  \n".join(parts))
        lines.append("")

    return "\n".join(lines)


# ===========================================================================
# Research Reports (个股研报) — smartmoney_db fallback
# ===========================================================================


def get_research_reports(
    symbol: str,
    curr_date: str | None = None,
) -> str:
    """Fetch A-share research reports from quant_core.db.

    Reads the ``research_report`` table with schema:
        ts_code TEXT, report_date TEXT, org_name TEXT,
        rating TEXT, target_price REAL, title TEXT

    If the table does not exist, is empty for the ticker, or the query
    fails, raises ``RuntimeError`` so ``route_to_vendor`` falls through
    to the next configured vendor (akshare).
    """
    code = _to_smartmoney_symbol(symbol)

    params = [code]
    date_filter = ""
    if curr_date:
        date_filter = " AND report_date <= ?"
        params.append(curr_date)

    df = _df_from_sql(
        f"""
        SELECT report_date, org_name, rating, target_price, title
        FROM research_report
        WHERE ts_code = ?{date_filter}
        ORDER BY report_date DESC
        LIMIT 10
        """,
        tuple(params),
    )

    if df is None or df.empty:
        raise RuntimeError(
            f"No research reports in quant_core.db for {symbol}. "
            "Route to_vendor will fall back to akshare."
        )

    lines = [
        f"## {symbol.upper()} Research Reports (source: quant_core.db / local SQLite)",
        f"Total records: {len(df)}",
        "",
    ]

    for _, row in df.iterrows():
        title = row.get("title", "N/A")
        org = row.get("org_name", "N/A")
        rating = row.get("rating", "N/A")
        target_price = row.get("target_price")
        report_date = row.get("report_date", "N/A")
        lines.append(f"**{title}**")
        lines.append(f"- 机构: {org}")
        lines.append(f"- 评级: {rating}")
        if pd.notna(target_price):
            lines.append(f"- 目标价: {target_price:.2f}")
        lines.append(f"- 日期: {report_date}")
        lines.append("")

    return "\n".join(lines)


# ===========================================================================
# Margin Trading (融资融券) — v2.2
# ===========================================================================

def get_margin_trading(
    symbol: str,
    curr_date: str | None = None,
) -> str:
    """Fetch margin-trading (融资融券) data from quant_core.db."""

    def _fmt_num(value) -> str:
        return f"{value:,.0f}" if pd.notna(value) else "N/A"

    code = _to_smartmoney_symbol(symbol)

    params = [code]
    date_filter = ""
    if curr_date:
        date_filter = " AND trade_date <= ?"
        params.append(curr_date)

    df = _df_from_sql(
        f"""
        SELECT trade_date AS Date, margin_balance, margin_buy, margin_repay,
               short_balance, short_sell, short_repay, total_balance
        FROM margin_trading
        WHERE ts_code = ?{date_filter}
        ORDER BY trade_date DESC
        LIMIT 5
        """,
        tuple(params),
    )

    if df is None or df.empty:
        # No rows for this symbol is a no-data condition, not a runtime
        # failure: raising NoMarketDataError lets route_to_vendor fall back
        # to online vendors and, when none serve it, degrade to a NO_DATA
        # sentinel. A bare RuntimeError here instead escaped the router
        # (governance_risk is not an optional category) and aborted the
        # whole ticker run — observed on 605299.SS (2026-09-28 batch).
        raise NoMarketDataError(
            symbol,
            canonical=code,
            detail=f"no margin_trading rows in quant_core.db for {symbol}",
        )

    _assert_local_data_not_stale("margin_trading", symbol, df, "Date", curr_date)

    lines = [
        f"## {symbol.upper()} Margin Trading (融资融券) "
        f"(source: quant_core.db / local SQLite)",
        f"Total records: {len(df)} trading days",
        "",
    ]
    for _, row in df.iterrows():
        lines.append(f"**Date**: {row['Date']}")
        lines.append(f"- 融资余额: {_fmt_num(row['margin_balance'])}")
        lines.append(f"- 融资买入额: {_fmt_num(row['margin_buy'])}")
        lines.append(f"- 融券余量: {_fmt_num(row['short_balance'])}")
        lines.append(f"- 融资融券余额: {_fmt_num(row['total_balance'])}")
        lines.append("")
    return "\n".join(lines)


# ===========================================================================
# Dragon Tiger (龙虎榜) — v2.2
# ===========================================================================

def get_dragon_tiger(
    symbol: str,
    curr_date: str | None = None,
) -> str:
    """Fetch dragon-tiger-board (龙虎榜) data from quant_core.db.

    Freshness is judged on the WHOLE TABLE's max trade_date (pipeline
    health), not per-stock appearances: a stock that has not appeared on
    the board recently legitimately has old per-stock rows, which are
    returned as history. Only a stalled backfill (table-wide staleness)
    falls through to the online vendor.
    """
    code = _to_smartmoney_symbol(symbol)

    params = [code]
    date_filter = ""
    if curr_date:
        date_filter = " AND trade_date <= ?"
        params.append(curr_date)

    table_max = _df_from_sql(
        "SELECT MAX(trade_date) AS latest FROM dragon_tiger", ()
    )
    anchor = (curr_date or datetime.now().strftime("%Y-%m-%d"))[:10]
    if table_max is not None and not table_max.empty:
        latest = table_max.iloc[0].get("latest")
        if latest is not None and pd.notna(latest):
            lag = trading_sessions_between(str(latest), anchor)
            if lag is not None and lag >= _DRAGON_TIGER_STALE_SESSIONS:
                raise NoMarketDataError(
                    symbol, symbol,
                    f"dragon_tiger table in quant_core.db is stale: newest "
                    f"row {latest} misses {lag} trading sessions as of "
                    f"{anchor} (budget {_DRAGON_TIGER_STALE_SESSIONS}); "
                    f"falling through to the online vendor.",
                )

    df = _df_from_sql(
        f"""
        SELECT trade_date AS Date, close_price, pct_change, net_buy_amount,
               buy_amount, sell_amount, turnover_rate, market_cap, reason
        FROM dragon_tiger
        WHERE ts_code = ?{date_filter}
        ORDER BY trade_date DESC
        LIMIT 5
        """,
        tuple(params),
    )

    if df is None or df.empty:
        raise RuntimeError(f"No dragon-tiger data in quant_core.db for {symbol}")

    lines = [
        f"## {symbol.upper()} Dragon Tiger Board (龙虎榜) "
        f"(source: quant_core.db / local SQLite)",
        f"Total records: {len(df)} appearances",
        "",
    ]
    for _, row in df.iterrows():
        lines.append(f"**Date**: {row['Date']}")
        lines.append(f"- Close: {row['close_price']:.2f} ({row['pct_change']:.2f}%)")
        lines.append(f"- Net Buy: {row['net_buy_amount']:,.0f}")
        lines.append(f"- Buy/Sell: {row['buy_amount']:,.0f} / {row['sell_amount']:,.0f}")
        if row.get("reason"):
            lines.append(f"- Reason: {row['reason']}")
        lines.append("")
    return "\n".join(lines)


# ===========================================================================
# Block Trade (大宗交易) — v2.2
# ===========================================================================

def get_block_trade(
    symbol: str,
    curr_date: str | None = None,
) -> str:
    """Fetch block-trade (大宗交易) data from quant_core.db."""
    code = _to_smartmoney_symbol(symbol)

    params = [code]
    date_filter = ""
    if curr_date:
        date_filter = " AND trade_date <= ?"
        params.append(curr_date)

    df = _df_from_sql(
        f"""
        SELECT trade_date AS Date, deal_price, close_price, discount_rate,
               volume, amount, buyer_branch, seller_branch
        FROM block_trade
        WHERE ts_code = ?{date_filter}
        ORDER BY trade_date DESC
        LIMIT 5
        """,
        tuple(params),
    )

    if df is None or df.empty:
        raise RuntimeError(f"No block-trade data in quant_core.db for {symbol}")

    lines = [
        f"## {symbol.upper()} Block Trade (大宗交易) "
        f"(source: quant_core.db / local SQLite)",
        f"Total records: {len(df)} transactions",
        "",
    ]
    for _, row in df.iterrows():
        lines.append(f"**Date**: {row['Date']}")
        lines.append(f"- Deal Price: {row['deal_price']:.2f}")
        lines.append(f"- Discount Rate: {row['discount_rate']:.2f}%")
        lines.append(f"- Volume: {row['volume']:,.0f}")
        lines.append(f"- Amount: {row['amount']:,.0f}")
        lines.append(f"- Buyer: {row.get('buyer_branch', 'N/A')}")
        lines.append(f"- Seller: {row.get('seller_branch', 'N/A')}")
        lines.append("")
    return "\n".join(lines)


# ===========================================================================
# Sector Fund Flow (板块资金流向) — v2.2
# ===========================================================================

# ---------------------------------------------------------------------------
# Sector name resolution helpers
# ---------------------------------------------------------------------------

# stock_list.industry 的注册行业口径 → sector_fund_flow 板块名的保守别名表。
# 只收录语义确信的映射；有歧义的（如 "农牧饲渔"）故意不收，留给报错路径。
_INDUSTRY_SECTOR_ALIASES = {
    "航空装备": "军工装备",
    "航天航空": "军工装备",
    "输配电气": "电网设备",
    "电信运营": "通信服务",
    "通讯行业": "通信设备",
    "有色金属": "工业金属",
    "家用轻工": "家居用品",
}

_SECTOR_NAME_SUFFIXES = ("行业", "概念", "板块")

# 申万官方在层级名后带罗马数字（"白酒Ⅱ"/"白酒Ⅲ"、"贸易Ⅱ"/"贸易Ⅲ"），本地
# sector_fund_flow 用的是不带后缀的同一批板块名（"白酒"/"贸易"）。比较前统一
# 剥掉罗马数字，否则"白酒"会同时命中"白酒Ⅱ"和"白酒Ⅲ"而被误判成歧义。
_ROMAN_NUMERAL_SUFFIX = re.compile(r"[ⅠⅡⅢⅣⅤⅥ]+$")


def _strip_sector_suffix(name: str) -> str:
    """剥掉 LLM 自由文本里常见的板块名后缀（如 "白酒行业" → "白酒"）。"""
    for suffix in _SECTOR_NAME_SUFFIXES:
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)]
    return name


def _normalize_sector_name(name: str) -> str:
    return _ROMAN_NUMERAL_SUFFIX.sub("", name.strip()).strip()


def _resolve_sector_name(requested: str, names: list[str]) -> str:
    """Resolve a requested sector/industry name to a sector_fund_flow entry.

    Comparison runs on Roman-numeral-stripped names so Shenwan's "白酒Ⅱ" and the
    local "白酒" are recognised as one sector, but the value returned is always
    an original entry of ``names`` (that is what the SQL lookup needs).

    Resolution order: exact → unique bidirectional substring (on both the raw
    and suffix-stripped probe) → bare-probe tie-break → alias table. A unique
    fuzzy hit is used automatically. When several sectors match but one of them
    *is* the bare probe (贸易行业 → 贸易, rather than the 石油加工贸易/贸易
    pair), the bare name wins; genuinely one-to-many hits (军工 → 军工电子 /
    军工装备) and zero hits raise NoMarketDataError carrying the candidate and
    available names, so the router degrades to NO_DATA_AVAILABLE (a missing
    sector must never abort the ticker) and the LLM can retry a valid name.
    """
    requested_norm = _normalize_sector_name(requested)
    normalized = {n: _normalize_sector_name(n) for n in names}

    exact = [n for n in names if normalized[n] == requested_norm]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise NoMarketDataError(
            requested,
            detail=(
                f"sector name is ambiguous in quant_core.db; "
                f"matching sectors: {', '.join(sorted(exact))}. "
                "Retry with one exact sector name."
            ),
        )

    probes = {requested_norm, _normalize_sector_name(_strip_sector_suffix(requested))} - {""}
    candidates = sorted({
        n for n in names
        for probe in probes
        if probe in normalized[n] or normalized[n] in probe
    })
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        bare = sorted({normalized[c] for c in candidates} & probes)
        if len(bare) == 1:
            return next(c for c in candidates if normalized[c] == bare[0])
        raise NoMarketDataError(
            requested,
            detail=(
                f"sector name is ambiguous in quant_core.db; "
                f"matching sectors: {', '.join(candidates)}. "
                "Retry with one exact sector name."
            ),
        )
    alias = _INDUSTRY_SECTOR_ALIASES.get(requested)
    if alias and alias in names:
        return alias
    raise NoMarketDataError(
        requested,
        detail=(
            f"no sector named {requested!r} in quant_core.db. "
            f"Available sectors: {', '.join(sorted(names))}"
        ),
    )


def _registered_industry(ticker: str) -> str | None:
    """Look up the registered industry of an A-share ticker in stock_list."""
    code = _to_smartmoney_symbol(ticker)
    if not code:
        return None
    df = _df_from_sql(
        "SELECT industry FROM stock_list WHERE code = ?",
        (code,),
    )
    if df is None or df.empty:
        return None
    industry = df.iloc[0]["industry"]
    if pd.isna(industry) or not str(industry).strip():
        return None
    return str(industry).strip()


def _resolve_sector_with_fallbacks(requested: str, names: list[str], ticker: str | None) -> tuple[str, str]:
    """按 请求名 → stock_list 注册行业 → 申万权威行业 的顺序解析板块名。

    每一级都只接受"精确或唯一"命中（见 `_resolve_sector_name`），全都不中就抛
    最初的 NoMarketDataError，由路由层降级成 NO_DATA_AVAILABLE。绝不因为语义
    相近就挑一个板块——那会把一只票的资金流错报成另一个行业的。返回
    (板块名, 审计标注)，标注会写进工具输出，报告读者可核对推断来源。
    """
    resolved: str | None = None
    direct_error: NoMarketDataError | None = None
    try:
        resolved = _resolve_sector_name(requested, names)
    except NoMarketDataError as exc:
        direct_error = exc
    if resolved is not None:
        note = f"（请求 '{requested}' 自动匹配到板块 '{resolved}'）" if resolved != requested else ""
        return resolved, note

    if ticker:
        industry = _registered_industry(ticker)
        if industry:
            try:
                resolved = _resolve_sector_name(industry, names)
            except NoMarketDataError:
                pass
            else:
                return resolved, f"（按 {ticker} 注册行业 '{industry}' 匹配到板块 '{resolved}'）"

        sw = get_sw_industry(ticker)
        if sw:
            for level, authority in (("申万二级", sw[1]), ("申万三级", sw[2])):
                if not authority:
                    continue
                try:
                    resolved = _resolve_sector_name(authority, names)
                except NoMarketDataError:
                    continue
                return resolved, f"（按 {ticker} {level} '{authority}' 匹配到板块 '{resolved}'）"
    raise direct_error if direct_error is not None else NoMarketDataError(requested)


def get_sector_fund_flow(sector_name: str, ticker: str | None = None) -> str:
    """Fetch sector fund-flow (板块资金流向) from quant_core.db.

    The DB stores Shenwan-style industry names (如 "军工电子"、"元件") while LLM
    analysts ask with colloquial or concept names (如 "军工"、"新能源汽车"),
    so an exact match frequently misses even though the data exists. The
    requested name is resolved via `_resolve_sector_name`; when that fails
    and `ticker` is given, the stock's registered industry (stock_list) is
    resolved instead, turning a free-text guess into a deterministic lookup.
    """
    requested = (sector_name or "").strip()
    resolved = requested
    note = ""

    all_df = _df_from_sql("SELECT DISTINCT sector_name FROM sector_fund_flow", ())
    if all_df is not None and not all_df.empty:
        names = [str(n) for n in all_df["sector_name"].dropna()]
        resolved, note = _resolve_sector_with_fallbacks(requested, names, ticker)

    df = _df_from_sql(
        """
        SELECT trade_date AS Date, main_net_inflow, main_net_inflow_pct,
               super_large_net_inflow, large_net_inflow,
               medium_net_inflow, small_net_inflow
        FROM sector_fund_flow
        WHERE sector_name = ?
        ORDER BY trade_date DESC
        LIMIT 5
        """,
        (resolved,),
    )

    if df is None or df.empty:
        raise NoMarketDataError(
            resolved,
            detail=(
                "sector_fund_flow has no rows for the resolved sector in "
                "quant_core.db"
            ),
        )

    lines = [
        f"## {resolved} Sector Fund Flow (板块资金流向) "
        f"(source: quant_core.db / local SQLite){note}",
        f"Total records: {len(df)} trading days",
        "",
    ]

    def _fmt(value, spec: str) -> str:
        """Format a numeric cell, tolerating SQL NULLs (None/NaN)."""
        return format(value, spec) if pd.notna(value) else "N/A"

    for _, row in df.iterrows():
        lines.append(f"**Date**: {row['Date']}")
        lines.append(
            f"- Main Force: {_fmt(row['main_net_inflow'], ',.0f')} "
            f"({_fmt(row['main_net_inflow_pct'], '.2f')}%)"
        )
        lines.append(f"- Super Large: {_fmt(row['super_large_net_inflow'], ',.0f')}")
        lines.append(f"- Large: {_fmt(row['large_net_inflow'], ',.0f')}")
        lines.append(f"- Medium: {_fmt(row['medium_net_inflow'], ',.0f')}")
        lines.append(f"- Small: {_fmt(row['small_net_inflow'], ',.0f')}")
        lines.append("")
    return "\n".join(lines)


# ===========================================================================
# Shareholder Count (股东户数) — v2.2
# ===========================================================================

def get_shareholder_count(
    symbol: str,
    curr_date: str | None = None,
) -> str:
    """Fetch shareholder-count (股东户数) from quant_core.db."""
    code = _to_smartmoney_symbol(symbol)

    params = [code]
    date_filter = ""
    if curr_date:
        date_filter = " AND report_date <= ?"
        params.append(curr_date)

    df = _df_from_sql(
        f"""
        SELECT report_date AS Date, holder_count, holder_count_change_pct,
               avg_shares_per_holder
        FROM shareholder_count
        WHERE ts_code = ?{date_filter}
        ORDER BY report_date DESC
        LIMIT 4
        """,
        tuple(params),
    )

    if df is None or df.empty:
        raise RuntimeError(f"No shareholder-count data in quant_core.db for {symbol}")

    lines = [
        f"## {symbol.upper()} Shareholder Count (股东户数) "
        f"(source: quant_core.db / local SQLite)",
        f"Total records: {len(df)} report periods",
        "",
    ]
    for _, row in df.iterrows():
        lines.append(f"**Report Date**: {row['Date']}")
        lines.append(f"- 股东户数: {row['holder_count']:,.0f}")
        lines.append(f"- 环比变化: {row['holder_count_change_pct']:.2f}%")
        lines.append(f"- 人均持股: {row['avg_shares_per_holder']:,.0f}")
        lines.append("")
    return "\n".join(lines)


# ===========================================================================
# Pledge Ratio (股权质押) — v2.2
# ===========================================================================


def get_pledge_ratio(symbol: str) -> str:
    """Fetch A-share pledge-ratio data from quant_core.db.

    Reads the normalized ``stock_pledge`` table with schema:
        stock_code TEXT, trade_date TEXT, pledger TEXT,
        pledge_amount REAL, pledge_ratio REAL, pledge_org TEXT

    If the table does not exist, is empty, or the query fails, raises
    ``NoMarketDataError`` so ``route_to_vendor`` falls through to AkShare.
    """
    code = _to_smartmoney_symbol(symbol)

    df = _df_from_sql(
        """
        SELECT trade_date, pledger, pledge_amount,
               pledge_ratio, pledge_org
        FROM stock_pledge
        WHERE stock_code = ?
        ORDER BY trade_date DESC
        LIMIT 10
        """,
        (code,),
    )

    if df is None or df.empty:
        raise NoMarketDataError(
            symbol,
            detail=f"no pledge-ratio data in quant_core.db for {symbol}",
        )

    lines = [
        f"## {symbol.upper()} Pledge Ratio (source: quant_core.db / local SQLite)",
        f"Total records: {len(df)}",
        "",
    ]
    for _, row in df.iterrows():
        lines.append(f"**Pledger**: {row.get('pledger', 'N/A')}")
        lines.append(f"- 质押日期: {row.get('trade_date', 'N/A')}")
        lines.append(f"- 质押数量: {row.get('pledge_amount', 'N/A')}")
        ratio = row.get("pledge_ratio")
        ratio_str = f"{ratio:.2f}%" if pd.notna(ratio) else "N/A"
        lines.append(f"- 质押比例: {ratio_str}")
        lines.append(f"- 质押机构: {row.get('pledge_org', 'N/A')}")
        lines.append("")

    return "\n".join(lines)


def _check_stale_warning(latest_date: str | None, curr_date: str | None, max_days: int = 2) -> str:
    """Return a warning string if latest_date is more than max_days behind curr_date."""
    if not latest_date or not curr_date:
        return ""
    try:
        dt_latest = pd.to_datetime(str(latest_date).split(" ")[0])
        dt_curr = pd.to_datetime(str(curr_date).split(" ")[0])
        days_behind = (dt_curr - dt_latest).days
        if days_behind > max_days:
            return (
                f"> ⚠️ [数据时效性预警]: 数据库记录最新日期为 {dt_latest.strftime('%Y-%m-%d')}，"
                f"距当前分析日期 ({dt_curr.strftime('%Y-%m-%d')}) 已滞后 {days_behind} 天。"
                f"请将以下数据视为历史参考背景，并适当降低决策置信度。\n\n"
            )
    except Exception:
        pass
    return ""


# Trading-session freshness budgets for high-frequency local tables, judged
# against the request's own date anchor (curr_date/trade_date as rewritten by
# the data policy — never today, so backtests are not penalised). Session lag
# is the count of trading days missing from the local table; weekends and
# holidays add no sessions, so a healthy table over Spring Festival never
# trips the guard, while a mid-week pipeline stall does after ~3 sessions.
# Budgets follow each table's publication reality (the user refreshes the
# pipeline daily after market close, so same-day-published tables are stale
# as soon as the anchor day's session is missing):
#   - fund_flow: published the same evening — budget 1 (missing the anchor
#     day's session already means the pipeline skipped a day)
#   - margin_trading: officially disclosed the NEXT morning — on day T the
#     expected newest row is T-1, so budget 2 (missing T-1 trips it)
# Only tables with a working online fallback belong here: beyond the budget
# the getter raises NoMarketDataError and route_to_vendor falls through to
# the online vendor.
# Tables intentionally excluded:
#   - north_hold: quarter-end disclosure since 2024-08, no fresher source exists
#   - chip_distribution: smartmoney_db is the only configured vendor; a stale
#     row with the inline _check_stale_warning beats NO_DATA
#   - stock_comment / stock_hot_rank: snapshot semantics — callers deliberately
#     use the latest snapshot regardless of age and degrade to online fetches
#   - dragon_tiger: sparse per-stock table — judged on the WHOLE TABLE's max
#     date in get_dragon_tiger (pipeline health), not per-stock appearances
#   - quarterly_financials: quarterly disclosure cadence — judged against the
#     expected report period via _assert_quarterly_not_stale, not sessions
_LOCAL_TABLE_STALE_SESSIONS = {
    "fund_flow": 1,
    "margin_trading": 2,
}

# Dragon-tiger staleness budget, judged on the whole table's max date (see
# get_dragon_tiger): a sparse per-stock table must not trip on a stock that
# simply has not appeared on the board recently. The list is published in
# the evening of day T, so at an after-close anchor the expected max date is
# T-1 and the budget is 2 (missing T-1 means the pipeline skipped a day).
_DRAGON_TIGER_STALE_SESSIONS = 2


def _assert_local_data_not_stale(
    table: str,
    symbol: str,
    df: pd.DataFrame,
    date_col: str,
    anchor_date: str | None,
) -> None:
    """Raise NoMarketDataError when the newest local row exceeds the budget.

    Budgets are trading sessions (see _LOCAL_TABLE_STALE_SESSIONS).
    ``anchor_date=None`` means "as of today". Malformed dates and calendar
    failures are ignored — a freshness guard must never be the reason data
    becomes unavailable.
    """
    budget = _LOCAL_TABLE_STALE_SESSIONS.get(table)
    if not budget or df is None or df.empty or date_col not in df.columns:
        return
    latest = df[date_col].max()
    if latest is None or pd.isna(latest):
        return
    anchor = (anchor_date or datetime.now().strftime("%Y-%m-%d"))[:10]
    lag = trading_sessions_between(str(latest), anchor)
    if lag is None:
        return
    if lag >= budget:
        raise NoMarketDataError(
            symbol, symbol,
            f"{table} data in quant_core.db is stale: newest row {latest} "
            f"misses {lag} trading sessions as of {anchor} "
            f"(budget {budget}); falling through to the online vendor.",
        )


def _assert_quarterly_not_stale(
    symbol: str,
    report_period: object,
    anchor_date: str | None,
) -> None:
    """Raise NoMarketDataError when the local quarterly report predates the expected period.

    Quarterly disclosure has its own cadence: a report counts as "expected"
    once its filing deadline plus the grace window has passed (see
    freshness.expected_report_period). Without this guard the local getter
    would silently serve a years-old report as "latest" after the pipeline
    fell behind. Judged against the request's own date anchor — backtests
    requesting old dates expect old reports and are never penalised.
    Malformed periods or anchors never block.
    """
    expected = expected_report_period(anchor_date)
    if expected is None:
        return
    latest = pd.to_datetime(str(report_period)[:10], errors="coerce")
    if pd.isna(latest):
        return
    if str(latest.date()) < expected:
        anchor = (anchor_date or datetime.now().strftime("%Y-%m-%d"))[:10]
        raise NoMarketDataError(
            symbol, symbol,
            f"quarterly_financials newest report_period {latest.date()} predates "
            f"the expected {expected} as of {anchor}; falling through to the "
            f"online vendor.",
        )


# ===========================================================================
# Chip Distribution & Cost Bias (筹码分布与成本偏离度) — High Alpha
# ===========================================================================

def get_chip_distribution(symbol: str, curr_date: str | None = None) -> str:
    """Fetch A-share chip distribution and cost bias from quant_core.db.

    Reads ``chip_distribution_em`` / ``chip_distribution`` and ``daily_bars``
    to compute profit ratio, average cost, 90%/70% concentration, and price-to-cost bias.
    """
    code = _to_smartmoney_symbol(symbol)
    params = [code]
    date_filter = ""
    if curr_date:
        date_filter = " AND trade_date <= ?"
        params.append(curr_date)

    # Try chip_distribution_em first, then chip_distribution
    # avg_cost > 0 过滤采集端历史上写入的全零伪数据（2026-07-21 起换手率缺失
    # 导致筹码计算静默输出全 0 行），自动回退到最近一批有效数据
    df = _df_from_sql(
        f"""
        SELECT trade_date, profit_ratio, avg_cost, cost_90_low, cost_90_high,
               concentration_90, cost_70_low, cost_70_high, concentration_70, chip_concentration
        FROM chip_distribution_em
        WHERE ts_code = ?{date_filter} AND profit_ratio IS NOT NULL AND avg_cost > 0
        ORDER BY trade_date DESC
        LIMIT 5
        """,
        tuple(params),
    )

    if df is None or df.empty:
        df = _df_from_sql(
            f"""
            SELECT trade_date, profit_ratio, avg_cost, cost_90_low, cost_90_high,
                   concentration_90, cost_70_low, cost_70_high, concentration_70, chip_concentration
            FROM chip_distribution
            WHERE ts_code = ?{date_filter} AND profit_ratio IS NOT NULL AND avg_cost > 0
            ORDER BY trade_date DESC
            LIMIT 5
            """,
            tuple(params),
        )

    if df is None or df.empty:
        raise RuntimeError(f"No chip distribution data in quant_core.db for {symbol}")

    latest = df.iloc[0]
    stale_warn = _check_stale_warning(latest["trade_date"], curr_date)

    # Fetch latest close price to calculate cost bias
    bar_df = _df_from_sql(
        f"SELECT close FROM daily_bars WHERE ts_code = ?{date_filter} ORDER BY trade_date DESC LIMIT 1",
        tuple(params),
    )
    latest_close = bar_df.iloc[0]["close"] if bar_df is not None and not bar_df.empty else None

    p_ratio = latest["profit_ratio"]
    if p_ratio is not None and p_ratio <= 1.0:
        p_ratio_pct = p_ratio * 100.0
    elif p_ratio is not None:
        p_ratio_pct = float(p_ratio)
    else:
        p_ratio_pct = 0.0

    avg_cost = latest["avg_cost"]
    c_90 = latest["concentration_90"]
    c_90_pct = (c_90 * 100.0) if (c_90 is not None and c_90 <= 1.0) else (c_90 or 0.0)

    # Cost bias
    bias_str = "N/A"
    synthesis = "筹码分布中性"
    if latest_close is not None and avg_cost is not None and avg_cost > 0:
        cost_bias = ((latest_close - avg_cost) / avg_cost) * 100.0
        bias_str = f"{cost_bias:+.2f}%"
        if p_ratio_pct > 80.0 and cost_bias < 5.0:
            synthesis = "🔥 获利盘高且处于成本密集区上方 (突破主升浪前兆/高获利沉淀)"
        elif p_ratio_pct > 85.0:
            synthesis = "⚠️ 获利盘极高 (>85%)，需防范上方短线获利回吐压力"
        elif p_ratio_pct < 15.0:
            synthesis = "🛡️ 获利盘极低 (<15%)，深幅超跌/筹码沉淀筑底区"

    lines = [
        stale_warn + f"## {symbol.upper()} Chip Distribution (筹码分布与成本偏离度)",
        f"Source: quant_core.db (Date: {latest['trade_date']})",
        f"- 获利盘比例: {p_ratio_pct:.2f}%",
        f"- 筹码平均成本: {avg_cost if avg_cost else 'N/A'} 元" + (f" (最新股价: {latest_close:.2f}元)" if latest_close else ""),
        f"- 股价相对于平均成本偏离度: {bias_str}",
        f"- 90%筹码集中度: {c_90_pct:.2f}%",
        f"- 90%筹码价格区间: {latest['cost_90_low']} ~ {latest['cost_90_high']} 元",
        f"- 量化因子综合判定: {synthesis}",
        "",
        "### 近5日筹码动态:",
    ]
    for _, row in df.iterrows():
        pr = (row['profit_ratio'] * 100.0) if row['profit_ratio'] and row['profit_ratio'] <= 1.0 else (row['profit_ratio'] or 0)
        lines.append(f"- **{row['trade_date']}**: 获利盘 {pr:.1f}%, 平均成本 {row['avg_cost']}元, 集中度 {row['concentration_90']}")

    return "\n".join(lines)


# ===========================================================================
# Historical Valuation Percentile (历史估值分位数) — High Alpha
# ===========================================================================

def get_historical_valuation(symbol: str, curr_date: str | None = None) -> str:
    """Fetch A-share 3-year historical valuation percentile rank and ROE from quant_core.db."""
    code = _to_smartmoney_symbol(symbol)
    params = [code]
    date_filter = ""
    if curr_date:
        date_filter = " AND trade_date <= ?"
        params.append(curr_date)

    df = _df_from_sql(
        f"""
        SELECT trade_date, pe_ttm, pb, ps_ttm, dividend_yield
        FROM historical_valuation
        WHERE ts_code = ?{date_filter} AND pe_ttm IS NOT NULL
        ORDER BY trade_date DESC
        LIMIT 720
        """,
        tuple(params),
    )

    if df is None or df.empty:
        # Fallback to fundamentals table
        df = _df_from_sql(
            f"""
            SELECT trade_date, pe_ttm, pb, ps_ttm, dividend_yield
            FROM fundamentals
            WHERE ts_code = ?{date_filter} AND pe_ttm IS NOT NULL
            ORDER BY trade_date DESC
            LIMIT 720
            """,
            tuple(params),
        )

    if df is None or df.empty:
        raise RuntimeError(f"No historical valuation data in quant_core.db for {symbol}")

    latest = df.iloc[0]
    stale_warn = _check_stale_warning(latest["trade_date"], curr_date)

    curr_pe = latest["pe_ttm"]
    curr_pb = latest["pb"]

    # Sample Size Guard
    sample_notice = ""
    if len(df) < 120:
        sample_notice = (
            f"\n> ⚠️ [次新股/小样本警示]: 本标的历史交易日仅 {len(df)} 条 (不足6个月)，"
            f"估值分位数仅反映上市以来的短期分位数，不代表长期历史高低位，请谨慎参考。\n"
        )

    # Calculate percentile
    valid_pes = df["pe_ttm"].dropna()
    valid_pbs = df["pb"].dropna()

    pe_pct = ((valid_pes <= curr_pe).sum() / len(valid_pes)) * 100.0 if len(valid_pes) > 0 else 50.0
    pb_pct = ((valid_pbs <= curr_pb).sum() / len(valid_pbs)) * 100.0 if len(valid_pbs) > 0 else 50.0

    # Fetch ROE from fundamentals for value trap verification
    f_df = _df_from_sql(
        f"SELECT roe, gross_margin, net_margin FROM fundamentals WHERE ts_code = ?{date_filter} AND roe IS NOT NULL ORDER BY trade_date DESC LIMIT 1",
        tuple(params),
    )
    latest_roe = f_df.iloc[0]["roe"] if f_df is not None and not f_df.empty else None

    # Sector Relative & Cyclical Stock Guard
    sector_info = "N/A"
    cyclical_warning = ""
    ind_df = _df_from_sql("SELECT industry FROM stock_list WHERE code = ?", (code,))
    if ind_df is not None and not ind_df.empty:
        ind_name = ind_df.iloc[0]["industry"]
        if ind_name:
            sec_df = _df_from_sql(
                "SELECT avg_pe FROM sector_industry WHERE industry_name = ? ORDER BY trade_date DESC LIMIT 1",
                (ind_name,),
            )
            if sec_df is not None and not sec_df.empty and pd.notna(sec_df.iloc[0]["avg_pe"]):
                avg_pe = sec_df.iloc[0]["avg_pe"]
                diff_pct = ((curr_pe - avg_pe) / avg_pe) * 100.0 if avg_pe > 0 else 0.0
                sector_info = f"{ind_name} (行业均值PE: {avg_pe:.2f}, 相对行业折溢价: {diff_pct:+.1f}%)"

            if ind_name in CYCLICAL_INDUSTRIES and curr_pe < 8.0:
                cyclical_warning = (
                    "\n> ⚠️ [周期股景气顶点预警]: 周期性行业 (如钢铁/煤炭/航运/化工) 极低PE (<8) 常出现在盈利顶点 (周期顶部)，"
                    "极低PE并不等于便宜，必须结合产品大宗价格与ROE变动趋势进行防爆判定。\n"
                )

    # Quant valuation synthesis
    synthesis = "估值处于合理区间"
    if pe_pct < 20.0:
        if latest_roe is not None and latest_roe > 10.0:
            synthesis = "🟢 深度价值区 (Deep Value): PE处于近3年底部的20%以内，且ROE>10%维持高盈利，具有强安全边际"
        elif latest_roe is not None and latest_roe < 5.0:
            synthesis = "⚠️ 警惕价值陷阱 (Value Trap Alert): 低PE但ROE持续走低(<5%)，基本面承压"
        else:
            synthesis = "🟢 低估值区: PE处于近3年底部20%分位"
    elif pe_pct > 80.0:
        synthesis = "🔴 极高估值区: PE处于近3年顶部的80%以上分位，溢价过高"

    # 股息率可能在最新行缺失（采集端雪球 token 失效时整日为 NULL），
    # 向前回退取最近非空值并标注数据日期
    div_yield_str = "N/A"
    dy_series = df["dividend_yield"].dropna() if "dividend_yield" in df.columns else None
    if dy_series is not None and not dy_series.empty:
        dy_idx = dy_series.index[0]
        dy_val = dy_series.iloc[0]
        dy_date = df.loc[dy_idx, "trade_date"]
        div_yield_str = f"{dy_val}%" if dy_date == latest["trade_date"] else f"{dy_val}% (截至 {dy_date})"

    lines = [
        stale_warn + sample_notice + cyclical_warning + f"## {symbol.upper()} Historical Valuation Percentile (近3年估值分位数)",
        f"Source: quant_core.db (Latest Date: {latest['trade_date']}, Total Bars: {len(df)})",
        f"- PE (TTM): {curr_pe:.2f} (处于近3年 {pe_pct:.1f}% 分位数)",
        f"- PB: {curr_pb:.2f} (处于近3年 {pb_pct:.1f}% 分位数)",
        f"- 所属行业对比: {sector_info}",
        f"- 股息率 (Dividend Yield): {div_yield_str}",
        f"- 最新 ROE: {f'{latest_roe:.2f}%' if latest_roe is not None else 'N/A'}",
        f"- 估值因子综合判定: {synthesis}",
    ]
    return "\n".join(lines)


# ===========================================================================
# Institutional Intelligence (机构综合情报 - 调研与持仓合并) — High Alpha
# ===========================================================================

def get_institutional_intelligence(symbol: str, curr_date: str | None = None) -> str:
    """Fetch merged institutional survey & holdings intelligence from quant_core.db."""
    code = _to_smartmoney_symbol(symbol)

    df_survey = _df_from_sql(
        """
        SELECT trade_date, survey_org, survey_type, survey_count
        FROM institution_survey
        WHERE stock_code = ? OR stock_code = ?
        ORDER BY trade_date DESC
        LIMIT 10
        """,
        (code, symbol),
    )

    df_holdings = _df_from_sql(
        """
        SELECT report_date, institution_count, top10_holder_ratio, type_counts
        FROM institutional_holdings
        WHERE ts_code = ? OR ts_code = ?
        ORDER BY report_date DESC
        LIMIT 4
        """,
        (code, symbol),
    )

    if (df_survey is None or df_survey.empty) and (df_holdings is None or df_holdings.empty):
        raise RuntimeError(f"No institutional intelligence (survey/holdings) in quant_core.db for {symbol}")

    latest_dt = None
    if df_survey is not None and not df_survey.empty:
        latest_dt = df_survey.iloc[0]["trade_date"]
    elif df_holdings is not None and not df_holdings.empty:
        latest_dt = df_holdings.iloc[0]["report_date"]

    stale_warn = _check_stale_warning(latest_dt, curr_date)

    lines = [
        stale_warn + f"## {symbol.upper()} Institutional Intelligence (机构综合情报)",
        "Source: quant_core.db",
    ]

    if df_survey is not None and not df_survey.empty:
        lines.append(f"### 近期机构调研动向 ({len(df_survey)} 项记录):")
        for _, row in df_survey.iterrows():
            lines.append(f"- **{row.get('trade_date', 'N/A')}**: 机构 {row.get('survey_org', 'N/A')}, 调研类型 {row.get('survey_type', 'N/A')}, 频次 {row.get('survey_count', 1)}")
        lines.append("")

    if df_holdings is not None and not df_holdings.empty:
        lines.append("### 机构持仓与筹码结构:")
        for _, row in df_holdings.iterrows():
            lines.append(f"- **报告期 {row['report_date']}**: 持仓机构总数 {row['institution_count']} 家, 前十名持仓集中度 {row['top10_holder_ratio'] if row['top10_holder_ratio'] else 'N/A'}%, 机构分类: {row['type_counts']}")

    return "\n".join(lines)


# Backward compatibility alias
get_institution_survey = get_institutional_intelligence




# ===========================================================================
# Earnings Forecast (业绩预告) — High Alpha
# ===========================================================================

def get_earnings_forecast(symbol: str) -> str:
    """Fetch earnings pre-announcement & forecast from quant_core.db."""
    code = _to_smartmoney_symbol(symbol)
    df = _df_from_sql(
        """
        SELECT name, end_date, forecast_type, net_profit_change, previous_profit
        FROM earnings_forecast
        WHERE ts_code = ?
        ORDER BY end_date DESC
        LIMIT 5
        """,
        (code,),
    )

    if df is None or df.empty:
        raise RuntimeError(f"No earnings forecast data in quant_core.db for {symbol}")

    lines = [
        f"## {symbol.upper()} Earnings Forecast (业绩预告)",
        "Source: quant_core.db",
        "",
    ]
    for _, row in df.iterrows():
        change_str = f"{row['net_profit_change']:+.2f}%" if pd.notna(row['net_profit_change']) else "N/A"
        lines.append(f"- **报告期 {row['end_date']}**: 类型 [{row['forecast_type']}], 预计净利润同比变动 {change_str}")

    return "\n".join(lines)


# ===========================================================================
# Concept Board (概念板块归属) — High Alpha
# ===========================================================================

def get_concept_board(symbol: str) -> str:
    """Fetch belonging concept boards from quant_core.db."""
    code = _to_smartmoney_symbol(symbol)
    df = _df_from_sql(
        """
        SELECT concept_code, concept_name
        FROM concept_member
        WHERE ts_code = ?
        """,
        (code,),
    )

    if df is None or df.empty:
        raise RuntimeError(f"No concept board data in quant_core.db for {symbol}")

    concepts = [f"{row['concept_name']} ({row['concept_code']})" for _, row in df.iterrows()]
    lines = [
        f"## {symbol.upper()} Belonging Concept Boards (归属概念题材)",
        f"Source: quant_core.db (共 {len(concepts)} 个概念板块)",
        "- 概念标签: " + ", ".join(concepts[:15]),
    ]
    return "\n".join(lines)


# ===========================================================================
# Commodity data (商品现货/期货) — lithium spot & commodity futures
# ===========================================================================

def get_lithium_spot(periods: int = 60) -> str:
    """Fetch lithium carbonate (碳酸锂) spot & futures basis from quant_core.db.

    Reads the market-level ``lithium_spot_daily`` table (no code dimension):
    生意社 spot quote plus near/dominant GFEX contract prices and basis.
    Rows are returned oldest-first so trend reading is natural.
    """
    df = _df_from_sql(
        """
        SELECT spot_date AS Date, spot_price, near_contract, near_contract_price,
               dom_contract, dom_contract_price, dom_basis, dom_basis_rate
        FROM lithium_spot_daily
        ORDER BY spot_date DESC
        LIMIT ?
        """,
        (max(int(periods), 1),),
    )

    if df is None:
        raise NoMarketDataError(
            "lithium_spot",
            detail="lithium_spot_daily query failed in quant_core.db (table missing or schema mismatch).",
        )

    if df.empty:
        raise NoMarketDataError(
            "lithium_spot",
            detail="no lithium spot data in quant_core.db.",
        )

    df = df.iloc[::-1].reset_index(drop=True)

    def _fmt(value, digits: int = 2) -> str:
        return f"{value:,.{digits}f}" if pd.notna(value) else "N/A"

    lines = [
        "## Lithium Carbonate Spot (碳酸锂现货与基差) "
        "(source: quant_core.db / local SQLite)",
        f"Total records: {len(df)} days",
        "",
    ]
    for _, row in df.iterrows():
        lines.append(f"**Date**: {row['Date']}")
        lines.append(f"- 现货价: {_fmt(row['spot_price'])}")
        lines.append(
            f"- 近月合约 {row['near_contract']}: {_fmt(row['near_contract_price'])}"
        )
        lines.append(
            f"- 主力合约 {row['dom_contract']}: {_fmt(row['dom_contract_price'])}"
        )
        basis_rate = row["dom_basis_rate"]
        basis_rate_str = f"{basis_rate * 100:.2f}%" if pd.notna(basis_rate) else "N/A"
        lines.append(f"- 主力基差: {_fmt(row['dom_basis'])} ({basis_rate_str})")
        lines.append("")
    return "\n".join(lines)


def get_commodity_futures(variety: str, periods: int = 60) -> str:
    """Fetch Chinese commodity futures daily bars from quant_core.db.

    Reads the ``futures_daily`` table for one variety code, e.g.
    ``AG`` (白银), ``LC`` (碳酸锂), ``CU`` (铜). Rows are returned
    oldest-first so trend reading is natural.
    """
    code = variety.strip().upper()

    df = _df_from_sql(
        """
        SELECT trade_date AS Date, name, open AS Open, high AS High,
               low AS Low, close AS Close, volume AS Volume,
               hold AS Hold, change_pct AS ChangePct
        FROM futures_daily
        WHERE symbol = ?
        ORDER BY trade_date DESC
        LIMIT ?
        """,
        (code, max(int(periods), 1)),
    )

    if df is None:
        raise NoMarketDataError(
            variety, code,
            "futures_daily query failed in quant_core.db (table missing or schema mismatch).",
        )

    if df.empty:
        available = _df_from_sql(
            "SELECT DISTINCT symbol FROM futures_daily ORDER BY symbol",
        )
        known = ", ".join(available["symbol"].tolist()) if available is not None and not available.empty else "N/A"
        raise NoMarketDataError(
            variety, code,
            f"no futures_daily data in quant_core.db for variety {code!r}. "
            f"Available varieties: {known}.",
        )

    df = df.iloc[::-1].reset_index(drop=True)
    name = df["name"].dropna().iloc[-1] if df["name"].notna().any() else code

    def _fmt_num(value) -> str:
        return f"{value:,.0f}" if pd.notna(value) else "N/A"

    def _fmt_px(value) -> str:
        return f"{value:,.2f}" if pd.notna(value) else "N/A"

    lines = [
        f"## {code} Futures Daily ({name}) "
        "(source: quant_core.db / local SQLite)",
        f"Total records: {len(df)} trading days",
        "",
    ]
    for _, row in df.iterrows():
        change = row["ChangePct"]
        change_str = f"{change:+.2f}%" if pd.notna(change) else "N/A"
        lines.append(f"**Date**: {row['Date']}")
        lines.append(f"- Close: {_fmt_px(row['Close'])} ({change_str})")
        lines.append(
            f"- Open/High/Low: {_fmt_px(row['Open'])} / {_fmt_px(row['High'])} / {_fmt_px(row['Low'])}"
        )
        lines.append(f"- Volume: {_fmt_num(row['Volume'])} | Open Interest: {_fmt_num(row['Hold'])}")
        lines.append("")
    return "\n".join(lines)


# ===========================================================================
# Macro data (宏观数据) — US daily macro, CFTC COT, EIA petroleum
# ===========================================================================

# Column legend for us_macro_daily, injected into the get_us_macro header so
# the LLM can interpret raw FRED-style column names.
_US_MACRO_COLUMN_LEGEND = (
    "Column legend: effr=有效联邦基金利率, dgs3mo/dgs2/dgs10=美债3月/2年/10年收益率, "
    "t10yie/t5yie=10年/5年盈亏平衡通胀预期, spread_10y_3m=10Y-3M利差, "
    "real_rate_10y=10年实际利率, icsa=初请失业金人数, "
    "hy_oas/ig_oas=高收益/投资级债信用利差(OAS), stlfi=STLFSI金融压力指数"
)


def get_us_macro(periods: int = 120) -> str:
    """Fetch US daily macro indicators from quant_core.db.

    Reads the ``us_macro_daily`` table (FRED-sourced, T+1): policy rate,
    Treasury yields, term spread, real rates, inflation expectations,
    initial claims, credit spreads (HY/IG OAS) and the STLFSI financial
    stress index. Returns a CSV-formatted table, oldest-first.
    """
    df = _df_from_sql(
        """
        SELECT trade_date AS Date, effr, dgs3mo, dgs2, dgs10,
               t10yie, t5yie, spread_10y_3m, real_rate_10y,
               icsa, hy_oas, ig_oas, stlfi
        FROM us_macro_daily
        ORDER BY trade_date DESC
        LIMIT ?
        """,
        (max(int(periods), 1),),
    )

    if df is None:
        raise NoMarketDataError(
            "us_macro",
            detail="us_macro_daily query failed in quant_core.db (table missing or schema mismatch).",
        )

    if df.empty:
        raise NoMarketDataError(
            "us_macro",
            detail="no US macro daily data in quant_core.db.",
        )

    df = df.iloc[::-1].set_index("Date")
    for col in df.columns:
        df[col] = pd.to_numeric(df[col], errors="coerce").round(4)

    header = (
        "## US Macro Daily (美国宏观日频指标)\n"
        f"# Total records: {len(df)} days\n"
        "# Source: quant_core.db / us_macro_daily (FRED, T+1)\n"
        f"# {_US_MACRO_COLUMN_LEGEND}\n\n"
    )
    return header + df.to_csv()


def _cot_available_instruments() -> str:
    """Comma-separated instrument list for CFTC COT error messages."""
    available = _df_from_sql(
        """
        SELECT DISTINCT instrument FROM cftc_cot_weekly
        WHERE market = 'goods' ORDER BY instrument
        """,
    )
    if available is None or available.empty:
        return "N/A"
    return ", ".join(available["instrument"].tolist())


def get_cftc_cot(instrument: str | None = None, periods: int = 52) -> str:
    """Fetch CFTC Commitments of Traders (持仓报告) from quant_core.db.

    With *instrument* (Chinese name, e.g. 白银/黄金/纽约原油) returns that
    instrument's weekly long/short/net series. Without *instrument* returns
    the whole ``goods`` complex (12 commodities) as a wide net-position table
    for the latest *periods* weeks.
    """
    limit = max(int(periods), 1)

    if instrument:
        name = instrument.strip()
        df = _df_from_sql(
            """
            SELECT trade_date AS Date, long_positions AS Long,
                   short_positions AS Short, net_positions AS Net
            FROM cftc_cot_weekly
            WHERE instrument = ?
            ORDER BY trade_date DESC
            LIMIT ?
            """,
            (name, limit),
        )

        if df is None:
            raise NoMarketDataError(
                instrument, name,
                "cftc_cot_weekly query failed in quant_core.db (table missing or schema mismatch).",
            )

        if df.empty:
            raise NoMarketDataError(
                instrument, name,
                f"no CFTC COT data in quant_core.db for instrument {name!r}. "
                f"Available goods instruments: {_cot_available_instruments()}.",
            )

        df = df.iloc[::-1].reset_index(drop=True)
        lines = [
            f"## CFTC COT — {name} (每周持仓)",
            "(source: quant_core.db / local SQLite)",
            f"Total records: {len(df)} weeks",
            "",
            "| Date | Long | Short | Net |",
            "| --- | ---: | ---: | ---: |",
        ]
        for _, row in df.iterrows():
            lines.append(
                f"| {row['Date']} | {_fmt_cot(row['Long'])} | {_fmt_cot(row['Short'])} | {_fmt_cot(row['Net'])} |"
            )
        return "\n".join(lines)

    df = _df_from_sql(
        """
        SELECT trade_date AS Date, instrument, net_positions AS Net
        FROM cftc_cot_weekly
        WHERE market = 'goods'
        ORDER BY trade_date DESC
        LIMIT ?
        """,
        (limit * 12,),
    )

    if df is None:
        raise NoMarketDataError(
            "cftc_cot",
            detail="cftc_cot_weekly query failed in quant_core.db (table missing or schema mismatch).",
        )

    if df.empty:
        raise NoMarketDataError(
            "cftc_cot",
            detail="no CFTC COT goods data in quant_core.db.",
        )

    # Wide table: one row per week, one column per goods instrument.
    pivot = df.pivot_table(index="Date", columns="instrument", values="Net", aggfunc="last")
    pivot = pivot.sort_index(ascending=False).head(limit).iloc[::-1]

    lines = [
        "## CFTC COT — Goods complex net positions (商品板块净持仓)",
        "(source: quant_core.db / local SQLite, weekly)",
        f"Total records: {len(pivot)} weeks x {len(pivot.columns)} instruments",
        "Net = long - short positions; call get_cftc_cot with an instrument for the full long/short breakdown.",
        "",
        "| Date | " + " | ".join(pivot.columns) + " |",
        "| --- |" + " ---: |" * len(pivot.columns),
    ]
    for date, row in pivot.iterrows():
        cells = " | ".join(_fmt_cot(row[col]) for col in pivot.columns)
        lines.append(f"| {date} | {cells} |")
    return "\n".join(lines)


def _fmt_cot(value) -> str:
    return f"{value:,.0f}" if pd.notna(value) else "N/A"


def get_eia_petroleum(series_id: str | None = None, periods: int = 156) -> str:
    """Fetch EIA weekly petroleum statistics from quant_core.db.

    With *series_id* (exact, e.g. PET.WCESTUS1.W) returns that series;
    without it returns all five archived series (crude/gasoline/SPR
    inventories, US production, refinery utilization) as a wide table.
    """
    limit = max(int(periods), 1)

    if series_id:
        sid = series_id.strip()
        df = _df_from_sql(
            """
            SELECT week_date AS Date, value AS Value
            FROM eia_petroleum_weekly
            WHERE series_id = ?
            ORDER BY week_date DESC
            LIMIT ?
            """,
            (sid, limit),
        )

        if df is None:
            raise NoMarketDataError(
                series_id, sid,
                "eia_petroleum_weekly query failed in quant_core.db (table missing or schema mismatch).",
            )

        if df.empty:
            available = _df_from_sql(
                "SELECT DISTINCT series_id FROM eia_petroleum_weekly ORDER BY series_id"
            )
            known = ", ".join(available["series_id"].tolist()) if available is not None and not available.empty else "N/A"
            raise NoMarketDataError(
                series_id, sid,
                f"no EIA petroleum data in quant_core.db for series {sid!r}. "
                f"Available series: {known}.",
            )

        df = df.iloc[::-1].reset_index(drop=True)
        lines = [
            f"## EIA Petroleum Weekly — {sid}",
            "(source: quant_core.db / local SQLite)",
            f"Total records: {len(df)} weeks",
            "",
            "| Date | Value |",
            "| --- | ---: |",
        ]
        for _, row in df.iterrows():
            value = f"{row['Value']:,.2f}" if pd.notna(row["Value"]) else "N/A"
            lines.append(f"| {row['Date']} | {value} |")
        return "\n".join(lines)

    df = _df_from_sql(
        """
        SELECT week_date AS Date, series_id, series_name, value AS Value
        FROM eia_petroleum_weekly
        ORDER BY week_date DESC
        LIMIT ?
        """,
        (limit * 5,),
    )

    if df is None:
        raise NoMarketDataError(
            "eia_petroleum",
            detail="eia_petroleum_weekly query failed in quant_core.db (table missing or schema mismatch).",
        )

    if df.empty:
        raise NoMarketDataError(
            "eia_petroleum",
            detail="no EIA petroleum data in quant_core.db.",
        )

    # Wide table: one row per week, one column per series.
    df["Label"] = df["series_id"] + " " + df["series_name"].fillna("")
    pivot = df.pivot_table(index="Date", columns="Label", values="Value", aggfunc="last")
    pivot = pivot.sort_index(ascending=False).head(limit).iloc[::-1]

    lines = [
        "## EIA Petroleum Weekly (EIA 周度石油数据)",
        "(source: quant_core.db / local SQLite)",
        f"Total records: {len(pivot)} weeks x {len(pivot.columns)} series",
        "",
        "| Date | " + " | ".join(pivot.columns) + " |",
        "| --- |" + " ---: |" * len(pivot.columns),
    ]
    for date, row in pivot.iterrows():
        cells = " | ".join(
            f"{row[col]:,.2f}" if pd.notna(row[col]) else "N/A" for col in pivot.columns
        )
        lines.append(f"| {date} | {cells} |")
    return "\n".join(lines)


# ===========================================================================
# Full-table coverage (全表接入) — events, cross-market, breadth snapshots
# ===========================================================================

def _available_column_values(table: str, column: str, where: str = "") -> str:
    """Comma-separated distinct values of *column* for error messages."""
    available = _df_from_sql(
        f"SELECT DISTINCT {column} AS v FROM {table} {where} ORDER BY {column}"
    )
    if available is None or available.empty:
        return "N/A"
    return ", ".join(str(v) for v in available["v"].tolist())


def get_placement_announcements(symbol: str, periods: int = 10) -> str:
    """Fetch private-placement (定增) announcements from quant_core.db."""
    code = _to_smartmoney_symbol(symbol)

    df = _df_from_sql(
        """
        SELECT issue_date AS Date, name, symbol, issue_method
        FROM placement_announcements
        WHERE ts_code = ?
        ORDER BY issue_date DESC
        LIMIT ?
        """,
        (code, max(int(periods), 1)),
    )

    if df is None:
        raise NoMarketDataError(
            symbol, code,
            "placement_announcements query failed in quant_core.db (table missing or schema mismatch).",
        )

    if df.empty:
        raise NoMarketDataError(
            symbol, code,
            f"no placement announcements in quant_core.db for {symbol}.",
        )

    lines = [
        f"## {symbol.upper()} Placement Announcements (定增公告) "
        "(source: quant_core.db / local SQLite)",
        f"Total records: {len(df)} announcements",
        "",
        "| Date | Name | Symbol | Method |",
        "| --- | --- | --- | --- |",
    ]
    for _, row in df.iterrows():
        lines.append(
            f"| {row['Date']} | {row['name']} | {row['symbol']} | {row['issue_method']} |"
        )
    return "\n".join(lines)


def get_stock_repurchase(symbol: str, periods: int = 10) -> str:
    """Fetch share-repurchase (回购) announcements from quant_core.db."""
    code = _to_smartmoney_symbol(symbol)

    df = _df_from_sql(
        """
        SELECT trade_date AS Date, stock_name, repurchase_amount,
               repurchase_price, repurchase_price_lower, repurchase_price_upper,
               repurchase_quantity, progress_status
        FROM stock_repurchase
        WHERE stock_code = ?
        ORDER BY trade_date DESC
        LIMIT ?
        """,
        (code, max(int(periods), 1)),
    )

    if df is None:
        raise NoMarketDataError(
            symbol, code,
            "stock_repurchase query failed in quant_core.db (table missing or schema mismatch).",
        )

    if df.empty:
        raise NoMarketDataError(
            symbol, code,
            f"no repurchase announcements in quant_core.db for {symbol}.",
        )

    def _fmt(value) -> str:
        return f"{value:,.2f}" if pd.notna(value) else "N/A"

    lines = [
        f"## {symbol.upper()} Share Repurchase (回购公告) "
        "(source: quant_core.db / local SQLite)",
        f"Total records: {len(df)} announcements",
        "",
    ]
    for _, row in df.iterrows():
        lines.append(f"**Date**: {row['Date']} [{row['progress_status']}]")
        lines.append(f"- 回购金额: {_fmt(row['repurchase_amount'])}")
        lines.append(
            f"- 回购价格: {_fmt(row['repurchase_price'])} "
            f"(区间 {_fmt(row['repurchase_price_lower'])} ~ {_fmt(row['repurchase_price_upper'])})"
        )
        lines.append(f"- 回购数量: {_fmt(row['repurchase_quantity'])}")
        lines.append("")
    return "\n".join(lines)


def get_dividend_summary(symbol: str) -> str:
    """Fetch the dividend & fundraising overview (分红募资总览) for one stock."""
    code = _to_smartmoney_symbol(symbol)

    df = _df_from_sql(
        """
        SELECT name, list_date, cumulative_dividend, avg_annual_dividend,
               dividend_count, total_raise_amount, raise_count
        FROM dividend_summary
        WHERE ts_code = ?
        """,
        (code,),
    )

    if df is None:
        raise NoMarketDataError(
            symbol, code,
            "dividend_summary query failed in quant_core.db (table missing or schema mismatch).",
        )

    if df.empty:
        raise NoMarketDataError(
            symbol, code,
            f"no dividend summary in quant_core.db for {symbol}.",
        )

    row = df.iloc[0]

    def _fmt(value) -> str:
        return f"{value:,.2f}" if pd.notna(value) else "N/A"

    return "\n".join([
        f"## {symbol.upper()} Dividend & Fundraising Summary (分红募资总览) "
        "(source: quant_core.db / local SQLite)",
        f"- 名称: {row['name']}",
        f"- 上市日期: {row['list_date']}",
        f"- 累计分红: {_fmt(row['cumulative_dividend'])} (年均 {_fmt(row['avg_annual_dividend'])}, 共 {row['dividend_count']} 次)",
        f"- 累计募资: {_fmt(row['total_raise_amount'])} (共 {row['raise_count']} 次)",
    ])


def get_ah_premium(symbol: str, periods: int = 60) -> str:
    """Fetch the A/H premium series (AH溢价) for one stock."""
    code = _to_smartmoney_symbol(symbol)

    df = _df_from_sql(
        """
        SELECT trade_date AS Date, h_code, a_price, h_price, premium
        FROM ah_premium
        WHERE ts_code = ?
        ORDER BY trade_date DESC
        LIMIT ?
        """,
        (code, max(int(periods), 1)),
    )

    if df is None:
        raise NoMarketDataError(
            symbol, code,
            "ah_premium query failed in quant_core.db (table missing or schema mismatch).",
        )

    if df.empty:
        raise NoMarketDataError(
            symbol, code,
            f"no A/H premium data in quant_core.db for {symbol} (A-share only).",
        )

    df = df.iloc[::-1].reset_index(drop=True)

    def _fmt(value) -> str:
        return f"{value:,.2f}" if pd.notna(value) else "N/A"

    lines = [
        f"## {symbol.upper()} A/H Premium (AH溢价) "
        "(source: quant_core.db / local SQLite)",
        f"Total records: {len(df)} trading days",
        "",
        "| Date | H Code | A Price | H Price | Premium |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    for _, row in df.iterrows():
        lines.append(
            f"| {row['Date']} | {row['h_code']} | {_fmt(row['a_price'])} "
            f"| {_fmt(row['h_price'])} | {_fmt(row['premium'])} |"
        )
    return "\n".join(lines)


def get_gold_price(periods: int = 60) -> str:
    """Fetch SGE gold prices (SGE金价) from quant_core.db."""
    df = _df_from_sql(
        """
        SELECT trade_date AS Date, trading_time, evening_price, morning_price
        FROM gold_price
        ORDER BY trade_date DESC, trading_time DESC
        LIMIT ?
        """,
        (max(int(periods), 1),),
    )

    if df is None:
        raise NoMarketDataError(
            "gold_price",
            detail="gold_price query failed in quant_core.db (table missing or schema mismatch).",
        )

    if df.empty:
        raise NoMarketDataError(
            "gold_price",
            detail="no gold price data in quant_core.db.",
        )

    df = df.iloc[::-1].reset_index(drop=True)

    def _fmt(value) -> str:
        return f"{value:,.2f}" if pd.notna(value) else "N/A"

    lines = [
        "## SGE Gold Price (SGE 金价) (source: quant_core.db / local SQLite)",
        f"Total records: {len(df)} quotes",
        "",
        "| Date | Evening | Morning |",
        "| --- | ---: | ---: |",
    ]
    for _, row in df.iterrows():
        lines.append(
            f"| {row['Date']} | {_fmt(row['evening_price'])} | {_fmt(row['morning_price'])} |"
        )
    return "\n".join(lines)


def get_hk_tech_index(periods: int = 120) -> str:
    """Fetch the Hang Seng Tech Index (恒生科技指数) daily bars."""
    df = _df_from_sql(
        """
        SELECT trade_date AS Date, open AS Open, high AS High, low AS Low,
               close AS Close, change_pct AS ChangePct, volume AS Volume,
               amount AS Amount
        FROM hk_tech_index_daily
        ORDER BY trade_date DESC
        LIMIT ?
        """,
        (max(int(periods), 1),),
    )

    if df is None:
        raise NoMarketDataError(
            "hk_tech_index",
            detail="hk_tech_index_daily query failed in quant_core.db (table missing or schema mismatch).",
        )

    if df.empty:
        raise NoMarketDataError(
            "hk_tech_index",
            detail="no Hang Seng Tech index data in quant_core.db.",
        )

    df = df.iloc[::-1].set_index("Date")
    for col in ("Open", "High", "Low", "Close", "ChangePct", "Volume", "Amount"):
        df[col] = pd.to_numeric(df[col], errors="coerce").round(4)

    header = (
        "## Hang Seng Tech Index (恒生科技指数日线)\n"
        f"# Total records: {len(df)} trading days\n"
        "# Source: quant_core.db / hk_tech_index_daily (local SQLite)\n\n"
    )
    return header + df.to_csv()


def get_fx_rate(currency: str = "美元", periods: int = 60) -> str:
    """Fetch onshore CNY central-parity quotes (在岸人民币牌价) from quant_core.db."""
    name = currency.strip()

    df = _df_from_sql(
        """
        SELECT trade_date AS Date, currency, central_parity_rate,
               bank_buy_price, cash_buy_price, cash_sell_price, boc_convert_price
        FROM fx_rate
        WHERE currency = ?
        ORDER BY trade_date DESC
        LIMIT ?
        """,
        (name, max(int(periods), 1)),
    )

    if df is None:
        raise NoMarketDataError(
            currency, name,
            "fx_rate query failed in quant_core.db (table missing or schema mismatch).",
        )

    if df.empty:
        raise NoMarketDataError(
            currency, name,
            f"no fx rate data in quant_core.db for currency {name!r}. "
            f"Available currencies: {_available_column_values('fx_rate', 'currency')}.",
        )

    df = df.iloc[::-1].reset_index(drop=True)

    def _fmt(value) -> str:
        return f"{value:,.4f}" if pd.notna(value) else "N/A"

    lines = [
        f"## CNY FX Rate — {name} (在岸人民币牌价) "
        "(source: quant_core.db / local SQLite, 中行牌价)",
        f"Total records: {len(df)} days",
        "",
        "| Date | Central Parity | Bank Buy | Cash Buy | Cash Sell | BOC Convert |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for _, row in df.iterrows():
        lines.append(
            f"| {row['Date']} | {_fmt(row['central_parity_rate'])} | {_fmt(row['bank_buy_price'])} "
            f"| {_fmt(row['cash_buy_price'])} | {_fmt(row['cash_sell_price'])} | {_fmt(row['boc_convert_price'])} |"
        )
    return "\n".join(lines)


def get_cb_quotation() -> str:
    """Fetch the convertible-bond snapshot (可转债行情), top 50 by double-low.

    Double-low (双低值 = price + premium) ascending is the classic cheap-CB
    ranking; distressed bonds with nonsensical values sort to the front, so
    bonds priced below 50 are excluded as delisted/junk artefacts.
    """
    df = _df_from_sql(
        """
        SELECT ts_code, bond_name, price, premium, double_low, expire_date
        FROM cb_quotation
        WHERE double_low IS NOT NULL AND price >= 50
        ORDER BY double_low ASC
        LIMIT 50
        """,
    )

    if df is None:
        raise NoMarketDataError(
            "cb_quotation",
            detail="cb_quotation query failed in quant_core.db (table missing or schema mismatch).",
        )

    if df.empty:
        raise NoMarketDataError(
            "cb_quotation",
            detail="no convertible-bond quotation data in quant_core.db.",
        )

    def _fmt(value) -> str:
        return f"{value:,.2f}" if pd.notna(value) else "N/A"

    lines = [
        "## Convertible Bond Quotation (可转债行情, 双低前50) "
        "(source: quant_core.db / local SQLite)",
        f"Total records: {len(df)} bonds (double-low ascending, price >= 50)",
        "",
        "| Code | Name | Price | Premium | Double Low | Expire |",
        "| --- | --- | ---: | ---: | ---: | --- |",
    ]
    for _, row in df.iterrows():
        lines.append(
            f"| {row['ts_code']} | {row['bond_name']} | {_fmt(row['price'])} "
            f"| {_fmt(row['premium'])} | {_fmt(row['double_low'])} | {row['expire_date']} |"
        )
    return "\n".join(lines)


def get_cb_redeem() -> str:
    """Fetch convertible bonds with an active redemption flag (可转债强赎状态)."""
    df = _df_from_sql(
        """
        SELECT ts_code, bond_name, redeem_flag, redeem_price, redeem_date
        FROM cb_redeem
        WHERE redeem_flag IS NOT NULL AND redeem_flag != ''
        ORDER BY ts_code
        """,
    )

    if df is None:
        raise NoMarketDataError(
            "cb_redeem",
            detail="cb_redeem query failed in quant_core.db (table missing or schema mismatch).",
        )

    if df.empty:
        raise NoMarketDataError(
            "cb_redeem",
            detail="no convertible bonds with an active redemption flag in quant_core.db.",
        )

    def _fmt(value) -> str:
        return f"{value:,.2f}" if pd.notna(value) else "N/A"

    lines = [
        "## Convertible Bond Redemption Flags (可转债强赎/不强赎公告) "
        "(source: quant_core.db / local SQLite)",
        f"Total records: {len(df)} bonds",
        "",
        "| Code | Name | Flag | Redeem Price | Redeem Date |",
        "| --- | --- | --- | ---: | --- |",
    ]
    for _, row in df.iterrows():
        lines.append(
            f"| {row['ts_code']} | {row['bond_name']} | {row['redeem_flag']} "
            f"| {_fmt(row['redeem_price'])} | {row['redeem_date']} |"
        )
    return "\n".join(lines)


def get_cb_index(index_code: str | None = None, periods: int = 120) -> str:
    """Fetch convertible-bond index (转债指数) daily bars from quant_core.db."""
    limit = max(int(periods), 1)

    if index_code:
        code = index_code.strip()
        df = _df_from_sql(
            """
            SELECT trade_date AS Date, index_name, open AS Open, high AS High,
                   low AS Low, close AS Close, volume AS Volume
            FROM cb_index
            WHERE index_code = ? AND index_code != ''
            ORDER BY trade_date DESC
            LIMIT ?
            """,
            (code, limit),
        )

        if df is None:
            raise NoMarketDataError(
                index_code, code,
                "cb_index query failed in quant_core.db (table missing or schema mismatch).",
            )

        if df.empty:
            known = _available_column_values("cb_index", "index_code", "WHERE index_code != ''")
            raise NoMarketDataError(
                index_code, code,
                f"no cb_index data in quant_core.db for index {code!r}. "
                f"Available index codes: {known}.",
            )

        df = df.iloc[::-1].set_index("Date")
        name = df["index_name"].dropna().iloc[-1] if df["index_name"].notna().any() else code
        df = df.drop(columns=["index_name"])
        for col in ("Open", "High", "Low", "Close", "Volume"):
            df[col] = pd.to_numeric(df[col], errors="coerce").round(4)

        header = (
            f"## CB Index {code} ({name}) (source: quant_core.db / local SQLite)\n"
            f"# Total records: {len(df)} trading days\n\n"
        )
        return header + df.to_csv()

    df = _df_from_sql(
        """
        SELECT trade_date AS Date, index_code, index_name, close AS Close
        FROM cb_index
        WHERE index_code != ''
        ORDER BY trade_date DESC
        LIMIT ?
        """,
        (limit * 5,),
    )

    if df is None:
        raise NoMarketDataError(
            "cb_index",
            detail="cb_index query failed in quant_core.db (table missing or schema mismatch).",
        )

    if df.empty:
        raise NoMarketDataError(
            "cb_index",
            detail="no cb_index data in quant_core.db.",
        )

    df["Label"] = df["index_code"] + " " + df["index_name"].fillna("")
    pivot = df.pivot_table(index="Date", columns="Label", values="Close", aggfunc="last")
    pivot = pivot.sort_index(ascending=False).head(limit).iloc[::-1]

    lines = [
        "## CB Index Daily (转债指数) (source: quant_core.db / local SQLite)",
        f"Total records: {len(pivot)} days x {len(pivot.columns)} indices",
        "",
        "| Date | " + " | ".join(pivot.columns) + " |",
        "| --- |" + " ---: |" * len(pivot.columns),
    ]
    for date, row in pivot.iterrows():
        cells = " | ".join(f"{row[col]:,.2f}" if pd.notna(row[col]) else "N/A" for col in pivot.columns)
        lines.append(f"| {date} | {cells} |")
    return "\n".join(lines)


def get_etf_daily(ts_code: str, periods: int = 120) -> str:
    """Fetch ETF daily bars (ETF日线) from quant_core.db."""
    code = _to_smartmoney_symbol(ts_code)

    df = _df_from_sql(
        """
        SELECT trade_date AS Date, name, open AS Open, high AS High, low AS Low,
               close AS Close, volume AS Volume, amount AS Amount, adj_factor
        FROM etf_daily
        WHERE ts_code = ?
        ORDER BY trade_date DESC
        LIMIT ?
        """,
        (code, max(int(periods), 1)),
    )

    if df is None:
        raise NoMarketDataError(
            ts_code, code,
            "etf_daily query failed in quant_core.db (table missing or schema mismatch).",
        )

    if df.empty:
        raise NoMarketDataError(
            ts_code, code,
            f"no ETF daily data in quant_core.db for {code!r}. "
            f"Available ETF codes: {_available_column_values('etf_daily', 'ts_code')}.",
        )

    df = df.iloc[::-1].set_index("Date")
    name = df["name"].dropna().iloc[-1] if df["name"].notna().any() else code
    df = df.drop(columns=["name"])
    for col in ("Open", "High", "Low", "Close", "Volume", "Amount", "adj_factor"):
        df[col] = pd.to_numeric(df[col], errors="coerce").round(4)

    header = (
        f"## ETF {code} ({name}) Daily (source: quant_core.db / local SQLite)\n"
        f"# Total records: {len(df)} trading days\n\n"
    )
    return header + df.to_csv()


def get_option_sentiment(periods: int = 60) -> str:
    """Fetch 50ETF option sentiment (期权情绪: QVIX, PCR, volumes, OI)."""
    df = _df_from_sql(
        """
        SELECT trade_date AS Date, qvix, pcr, put_volume, call_volume,
               put_oi, call_oi, implied_vol_avg
        FROM option_sentiment
        ORDER BY trade_date DESC
        LIMIT ?
        """,
        (max(int(periods), 1),),
    )

    if df is None:
        raise NoMarketDataError(
            "option_sentiment",
            detail="option_sentiment query failed in quant_core.db (table missing or schema mismatch).",
        )

    if df.empty:
        raise NoMarketDataError(
            "option_sentiment",
            detail="no option sentiment data in quant_core.db.",
        )

    df = df.iloc[::-1].reset_index(drop=True)

    def _fmt(value, digits: int = 2) -> str:
        return f"{value:,.{digits}f}" if pd.notna(value) else "N/A"

    def _fmt_int(value) -> str:
        return f"{value:,.0f}" if pd.notna(value) else "N/A"

    lines = [
        "## Option Sentiment — 50ETF options (期权情绪) "
        "(source: quant_core.db / local SQLite)",
        f"Total records: {len(df)} trading days",
        "",
        "| Date | QVIX | PCR | Put Vol | Call Vol | Put OI | Call OI | IV Avg |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for _, row in df.iterrows():
        lines.append(
            f"| {row['Date']} | {_fmt(row['qvix'])} | {_fmt(row['pcr'])} "
            f"| {_fmt_int(row['put_volume'])} | {_fmt_int(row['call_volume'])} "
            f"| {_fmt_int(row['put_oi'])} | {_fmt_int(row['call_oi'])} | {_fmt(row['implied_vol_avg'])} |"
        )
    return "\n".join(lines)


def get_south_flow(market: str | None = None, periods: int = 60) -> str:
    """Fetch southbound (南向资金) daily flow from quant_core.db."""
    limit = max(int(periods), 1)

    if market:
        name = market.strip()
        df = _df_from_sql(
            """
            SELECT trade_date AS Date, net_buy_amount, buy_amount, sell_amount, cumulative_net_buy
            FROM south_flow
            WHERE market = ? AND market != ''
            ORDER BY trade_date DESC
            LIMIT ?
            """,
            (name, limit),
        )

        if df is None:
            raise NoMarketDataError(
                market, name,
                "south_flow query failed in quant_core.db (table missing or schema mismatch).",
            )

        if df.empty:
            known = _available_column_values("south_flow", "market", "WHERE market != ''")
            raise NoMarketDataError(
                market, name,
                f"no south-flow data in quant_core.db for market {name!r}. "
                f"Available markets: {known}.",
            )

        df = df.iloc[::-1].reset_index(drop=True)

        def _fmt(value) -> str:
            return f"{value:,.2f}" if pd.notna(value) else "N/A"

        lines = [
            f"## Southbound Flow — {name} (南向资金) "
            "(source: quant_core.db / local SQLite)",
            f"Total records: {len(df)} trading days",
            "",
            "| Date | Net Buy | Buy | Sell | Cumulative Net Buy |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
        for _, row in df.iterrows():
            lines.append(
                f"| {row['Date']} | {_fmt(row['net_buy_amount'])} | {_fmt(row['buy_amount'])} "
                f"| {_fmt(row['sell_amount'])} | {_fmt(row['cumulative_net_buy'])} |"
            )
        return "\n".join(lines)

    df = _df_from_sql(
        """
        SELECT trade_date AS Date, market, net_buy_amount
        FROM south_flow
        WHERE market != ''
        ORDER BY trade_date DESC
        LIMIT ?
        """,
        (limit * 5,),
    )

    if df is None:
        raise NoMarketDataError(
            "south_flow",
            detail="south_flow query failed in quant_core.db (table missing or schema mismatch).",
        )

    if df.empty:
        raise NoMarketDataError(
            "south_flow",
            detail="no south-flow data in quant_core.db.",
        )

    pivot = df.pivot_table(index="Date", columns="market", values="net_buy_amount", aggfunc="last")
    pivot = pivot.sort_index(ascending=False).head(limit).iloc[::-1]

    lines = [
        "## Southbound Flow (南向资金) (source: quant_core.db / local SQLite)",
        f"Total records: {len(pivot)} days x {len(pivot.columns)} markets",
        "",
        "| Date | " + " | ".join(pivot.columns) + " |",
        "| --- |" + " ---: |" * len(pivot.columns),
    ]
    for date, row in pivot.iterrows():
        cells = " | ".join(f"{row[col]:,.2f}" if pd.notna(row[col]) else "N/A" for col in pivot.columns)
        lines.append(f"| {date} | {cells} |")
    return "\n".join(lines)


def get_index_futures_basis(futures_code: str | None = None, periods: int = 60) -> str:
    """Fetch index-futures basis (期指基差: IF/IC/IM/IH) from quant_core.db."""
    limit = max(int(periods), 1)

    if futures_code:
        code = futures_code.strip().upper()
        df = _df_from_sql(
            """
            SELECT trade_date AS Date, futures_price, index_price, basis, basis_pct
            FROM index_futures_basis
            WHERE futures_code = ?
            ORDER BY trade_date DESC
            LIMIT ?
            """,
            (code, limit),
        )

        if df is None:
            raise NoMarketDataError(
                futures_code, code,
                "index_futures_basis query failed in quant_core.db (table missing or schema mismatch).",
            )

        if df.empty:
            raise NoMarketDataError(
                futures_code, code,
                f"no index-futures basis data in quant_core.db for {code!r}. "
                f"Available futures codes: {_available_column_values('index_futures_basis', 'futures_code')}.",
            )

        df = df.iloc[::-1].reset_index(drop=True)

        def _fmt(value, digits: int = 2) -> str:
            return f"{value:,.{digits}f}" if pd.notna(value) else "N/A"

        lines = [
            f"## Index Futures Basis — {code} (期指基差) "
            "(source: quant_core.db / local SQLite)",
            f"Total records: {len(df)} trading days",
            "",
            "| Date | Futures | Index | Basis | Basis % |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
        for _, row in df.iterrows():
            lines.append(
                f"| {row['Date']} | {_fmt(row['futures_price'])} | {_fmt(row['index_price'])} "
                f"| {_fmt(row['basis'])} | {_fmt(row['basis_pct'])} |"
            )
        return "\n".join(lines)

    df = _df_from_sql(
        """
        SELECT trade_date AS Date, futures_code, basis_pct
        FROM index_futures_basis
        ORDER BY trade_date DESC
        LIMIT ?
        """,
        (limit * 10,),
    )

    if df is None:
        raise NoMarketDataError(
            "index_futures_basis",
            detail="index_futures_basis query failed in quant_core.db (table missing or schema mismatch).",
        )

    if df.empty:
        raise NoMarketDataError(
            "index_futures_basis",
            detail="no index-futures basis data in quant_core.db.",
        )

    pivot = df.pivot_table(index="Date", columns="futures_code", values="basis_pct", aggfunc="last")
    pivot = pivot.sort_index(ascending=False).head(limit).iloc[::-1]

    lines = [
        "## Index Futures Basis % (期指基差) (source: quant_core.db / local SQLite)",
        f"Total records: {len(pivot)} days x {len(pivot.columns)} contracts",
        "Negative = futures discount (bearish sentiment); call with a futures_code for absolute basis.",
        "",
        "| Date | " + " | ".join(pivot.columns) + " |",
        "| --- |" + " ---: |" * len(pivot.columns),
    ]
    for date, row in pivot.iterrows():
        cells = " | ".join(f"{row[col]:,.2f}" if pd.notna(row[col]) else "N/A" for col in pivot.columns)
        lines.append(f"| {date} | {cells} |")
    return "\n".join(lines)


def get_sector_daily(sector_name: str, periods: int = 120) -> str:
    """Fetch sector daily bars (板块日线) from quant_core.db."""
    name = sector_name.strip()

    df = _df_from_sql(
        """
        SELECT trade_date AS Date, open AS Open, high AS High, low AS Low,
               close AS Close, volume AS Volume, amount AS Amount, pct_change AS ChangePct
        FROM sector_daily
        WHERE sector_name = ?
        ORDER BY trade_date DESC
        LIMIT ?
        """,
        (name, max(int(periods), 1)),
    )

    if df is None:
        raise NoMarketDataError(
            sector_name, name,
            "sector_daily query failed in quant_core.db (table missing or schema mismatch).",
        )

    if df.empty:
        raise NoMarketDataError(
            sector_name, name,
            f"no sector_daily data in quant_core.db for sector {name!r}. "
            f"Available sectors include: {_available_column_values('sector_daily', 'sector_name')[:300]}.",
        )

    df = df.iloc[::-1].set_index("Date")
    for col in ("Open", "High", "Low", "Close", "Volume", "Amount", "ChangePct"):
        df[col] = pd.to_numeric(df[col], errors="coerce").round(4)

    header = (
        f"## Sector {name} Daily (板块日线) (source: quant_core.db / local SQLite)\n"
        f"# Total records: {len(df)} trading days\n\n"
    )
    return header + df.to_csv()


def get_sector_valuation(sector_name: str, periods: int = 120) -> str:
    """Fetch sector valuation (板块估值: PE/PB/总市值) from quant_core.db."""
    name = sector_name.strip()

    df = _df_from_sql(
        """
        SELECT trade_date AS Date, pe, pb, total_mv
        FROM sector_valuation
        WHERE sector_name = ?
        ORDER BY trade_date DESC
        LIMIT ?
        """,
        (name, max(int(periods), 1)),
    )

    if df is None:
        raise NoMarketDataError(
            sector_name, name,
            "sector_valuation query failed in quant_core.db (table missing or schema mismatch).",
        )

    if df.empty:
        raise NoMarketDataError(
            sector_name, name,
            f"no sector_valuation data in quant_core.db for sector {name!r}. "
            f"Available sectors include: {_available_column_values('sector_valuation', 'sector_name')[:300]}.",
        )

    df = df.iloc[::-1].reset_index(drop=True)

    def _fmt(value) -> str:
        return f"{value:,.2f}" if pd.notna(value) else "N/A"

    lines = [
        f"## Sector {name} Valuation (板块估值) (source: quant_core.db / local SQLite)",
        f"Total records: {len(df)} days",
        "",
        "| Date | PE | PB | Total MV |",
        "| --- | ---: | ---: | ---: |",
    ]
    for _, row in df.iterrows():
        lines.append(f"| {row['Date']} | {_fmt(row['pe'])} | {_fmt(row['pb'])} | {_fmt(row['total_mv'])} |")
    return "\n".join(lines)


_CBB_COLUMN_LEGEND = (
    "Column legend: total_assets=总资产, reserve_money=储备货币, currency_issue=货币发行, "
    "claims_on_other_deposit=对其他存款性公司债权, claims_on_gov=对政府债权, "
    "gov_deposits=政府存款, foreign_assets=国外资产, fx_reserve=外汇储备(亿美元)"
)


def get_central_bank_balance(periods: int = 36) -> str:
    """Fetch the PBOC balance sheet (央行资产负债表, 月频) from quant_core.db."""
    df = _df_from_sql(
        """
        SELECT date AS Date, total_assets, reserve_money, currency_issue,
               claims_on_other_deposit, claims_on_gov, gov_deposits,
               foreign_assets, fx_reserve
        FROM central_bank_balance
        ORDER BY date DESC
        LIMIT ?
        """,
        (max(int(periods), 1),),
    )

    if df is None:
        raise NoMarketDataError(
            "central_bank_balance",
            detail="central_bank_balance query failed in quant_core.db (table missing or schema mismatch).",
        )

    if df.empty:
        raise NoMarketDataError(
            "central_bank_balance",
            detail="no central bank balance sheet data in quant_core.db.",
        )

    df = df.iloc[::-1].set_index("Date")
    for col in df.columns:
        df[col] = pd.to_numeric(df[col], errors="coerce").round(2)

    header = (
        "## PBOC Balance Sheet (央行资产负债表, 月频)\n"
        f"# Total records: {len(df)} months\n"
        "# Source: quant_core.db / central_bank_balance (local SQLite)\n"
        f"# {_CBB_COLUMN_LEGEND}\n\n"
    )
    return header + df.to_csv()


# ===========================================================================
# Eastmoney 千股千评 snapshot (stock_comment)
# ===========================================================================


def get_stock_comment(symbol: Annotated[str, "A-share ticker e.g. 002241.SZ"]) -> dict[str, Any]:
    """Fetch the latest 千股千评 (composite stock comment) snapshot from quant_core.db.

    The ``stock_comment`` table is a full-market daily snapshot maintained by
    quant_pipeline (migration 024, task ``update_stock_comment``), sparing
    every TradingAgents run the slow ``ak.stock_comment_em()`` whole-table
    fetch. Returns the most recent row for *symbol* keyed by the snapshot's
    own ``trade_date``.

    Raises:
        NoMarketDataError: table missing, query failed, or no row for symbol.
    """
    code = _to_smartmoney_symbol(symbol)

    df = _df_from_sql(
        """
        SELECT trade_date, name, close_price, change_pct, turnover,
               pe_dynamic, prime_cost, org_participation, composite_score,
               rank_up, rank, focus_index
        FROM stock_comment
        WHERE code = ?
        ORDER BY trade_date DESC
        LIMIT 1
        """,
        (code,),
    )

    if df is None:
        raise NoMarketDataError(
            "stock_comment",
            detail="stock_comment query failed in quant_core.db (table missing — run quant_pipeline migration 024 / task update_stock_comment).",
        )

    if df.empty:
        raise NoMarketDataError(
            "stock_comment",
            detail=f"no 千股千评 snapshot in quant_core.db for {symbol}.",
        )

    row = df.iloc[0].to_dict()
    logger.debug(
        "stock_comment local hit for %s: trade_date=%s score=%s",
        symbol, row.get("trade_date"), row.get("composite_score"),
    )
    return row


# ===========================================================================
# Eastmoney 人气榜 snapshot (stock_hot_rank)
# ===========================================================================


def get_stock_hot_rank(symbol: Annotated[str, "A-share ticker e.g. 002241.SZ"]) -> dict[str, Any]:
    """Fetch the latest Eastmoney hot-rank snapshot entry for *symbol* from quant_core.db.

    The ``stock_hot_rank`` table is a Top-100 popularity-rank snapshot
    maintained by quant_pipeline (migration 025, task ``update_hot_rank``).
    Returns the most recent row for *symbol* keyed by snapshot date.

    Raises:
        NoMarketDataError: table missing, query failed, or no row for symbol
            (which includes "not in the latest top-100" — callers that need
            to distinguish should use ``get_stock_hot_rank_snapshot_date``).
    """
    code = _to_smartmoney_symbol(symbol)

    df = _df_from_sql(
        """
        SELECT trade_date, code, name, rank, rank_change, prev_rank,
               close_price, change_pct
        FROM stock_hot_rank
        WHERE code = ?
        ORDER BY trade_date DESC, rank ASC
        LIMIT 1
        """,
        (code,),
    )

    if df is None:
        raise NoMarketDataError(
            "stock_hot_rank",
            detail="stock_hot_rank query failed in quant_core.db (table missing — run quant_pipeline migration 025 / task update_hot_rank).",
        )

    if df.empty:
        raise NoMarketDataError(
            "stock_hot_rank",
            detail=f"{symbol} not in the latest Eastmoney top-100 hot-rank snapshot.",
        )

    row = df.iloc[0].to_dict()
    logger.debug(
        "stock_hot_rank local hit for %s: trade_date=%s rank=%s",
        symbol, row.get("trade_date"), row.get("rank"),
    )
    return row


def get_stock_hot_rank_snapshot_date() -> str | None:
    """Return the snapshot date of the latest stock_hot_rank row, or None.

    Lets callers distinguish "snapshot missing entirely" (fall back to the
    online fetch) from "snapshot exists but symbol not in top-100" (a real
    negative signal, not data degradation).
    """
    df = _df_from_sql(
        "SELECT MAX(trade_date) AS d FROM stock_hot_rank",
    )
    if df is None or df.empty or df.iloc[0]["d"] is None:
        return None
    return str(df.iloc[0]["d"])
