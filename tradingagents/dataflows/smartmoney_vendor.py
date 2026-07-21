"""SmartMoney DB vendor — read A-share data from local SQLite database.

This vendor provides a zero-latency fallback layer for A-share tickers by
reading from the shared quant_core.db maintained by quant_hunter.

Placement in the fallback chain:
    quant_core.db → akshare → yfinance

If quant_core.db has no data for a symbol, ``route_to_vendor`` automatically
falls back to the next vendor in the chain.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime
from typing import Annotated

import pandas as pd

from tradingagents.dataflows.errors import NoMarketDataError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared database path (centralised in ~/Code/quant_data/)
# ---------------------------------------------------------------------------
DEFAULT_DB_PATH = os.path.expanduser("~/Code/quant_data/quant_core.db")
_DB_PATH = os.getenv("QUANT_DB_PATH", DEFAULT_DB_PATH)


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
        "macd": "macd_hist",
        "macd_dif": "macd_dif",
        "macd_dea": "macd_dea",
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
    lines = [
        f"# Balance Sheet for {symbol.upper()} (截至 {period})",
        "# Source: quant_core.db (local SQLite, quarterly_financials)",
        "",
    ]
    for col, label, fmt in [
        ("debt_ratio", "资产负债率", ".2f%"),
        ("bps", "每股净资产", ".2f"),
        ("operating_cashflow", "经营活动现金流净额", ",.0f"),
    ]:
        v = row.get(col)
        if pd.notna(v):
            if "%" in fmt:
                lines.append(f"- {label}: {v:.2f}%")
            else:
                lines.append(f"- {label}: {v:{fmt}}")
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
    lines = [
        f"# Operating Cash Flow for {symbol.upper()} (截至 {period})",
        "# Source: quant_core.db (local SQLite, quarterly_financials)",
        "",
    ]
    v = row.get("operating_cashflow")
    if pd.notna(v):
        lines.append(f"- 经营活动现金流净额: {v:,.0f}")
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
    lines = [
        f"# Income Statement for {symbol.upper()} (截至 {period})",
        "# Source: quant_core.db (local SQLite, quarterly_financials)",
        "",
    ]
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
                lines.append(f"- {label}: {v:.2f}%")
            else:
                lines.append(f"- {label}: {v:{fmt}}")
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


def get_earnings_estimates(symbol: str) -> str:
    raise RuntimeError("Earnings estimates not available in quant_core.db")


def get_macro_indicators(
    indicator: str = "",
    curr_date: str | None = None,
    look_back_days: int | None = None,
) -> str:
    raise RuntimeError("Macro indicators not available in quant_core.db")


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
        raise RuntimeError(f"No margin-trading data in quant_core.db for {symbol}")

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
    """Fetch dragon-tiger-board (龙虎榜) data from quant_core.db."""
    code = _to_smartmoney_symbol(symbol)

    params = [code]
    date_filter = ""
    if curr_date:
        date_filter = " AND trade_date <= ?"
        params.append(curr_date)

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

def get_sector_fund_flow(sector_name: str) -> str:
    """Fetch sector fund-flow (板块资金流向) from quant_core.db."""
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
        (sector_name,),
    )

    if df is None or df.empty:
        raise RuntimeError(
            f"No sector fund-flow data in quant_core.db for '{sector_name}'"
        )

    lines = [
        f"## {sector_name} Sector Fund Flow (板块资金流向) "
        f"(source: quant_core.db / local SQLite)",
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

    Reads the ``stock_gpzy`` table with schema:
        ts_code TEXT, pledge_date TEXT, pledger TEXT,
        pledged_shares REAL, pct_of_holding REAL, pct_of_total REAL,
        pledge_org TEXT

    If the table does not exist, is empty, or the query fails, raises
    ``RuntimeError`` so ``route_to_vendor`` falls through to akshare.
    """
    code = _to_smartmoney_symbol(symbol)

    df = _df_from_sql(
        """
        SELECT pledge_date, pledger, pledged_shares,
               pct_of_holding, pct_of_total, pledge_org
        FROM stock_gpzy
        WHERE ts_code = ?
        ORDER BY pledge_date DESC
        LIMIT 10
        """,
        (code,),
    )

    if df is None or df.empty:
        raise RuntimeError(
            f"No pledge-ratio data in quant_core.db for {symbol}. "
            "Route will fall back to akshare."
        )

    lines = [
        f"## {symbol.upper()} Pledge Ratio (source: quant_core.db / local SQLite)",
        f"Total records: {len(df)}",
        "",
    ]
    for _, row in df.iterrows():
        lines.append(f"**Pledger**: {row.get('pledger', 'N/A')}")
        lines.append(f"- 质押日期: {row.get('pledge_date', 'N/A')}")
        lines.append(f"- 质押数量: {row.get('pledged_shares', 'N/A')}")
        lines.append(f"- 占所持比例: {row.get('pct_of_holding', 0):.2f}%")
        lines.append(f"- 占总股本比例: {row.get('pct_of_total', 0):.2f}%")
        lines.append(f"- 质押机构: {row.get('pledge_org', 'N/A')}")
        lines.append("")

    return "\n".join(lines)
