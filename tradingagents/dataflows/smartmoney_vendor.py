"""SmartMoney DB vendor — read A-share data from local SQLite database.

This vendor provides a zero-latency fallback layer for A-share tickers by
reading from the shared quant_core.db maintained by smartmoney_hunter.

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

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared database path (centralised in ~/Code/data/quant_data/)
# ---------------------------------------------------------------------------
DEFAULT_DB_PATH = os.path.expanduser("~/Code/data/quant_data/quant_core.db")
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

    # Otherwise try to compute via stockstats (requires full OHLCV)
    stats = wrap(df)
    stats["Date"] = stats["Date"].dt.strftime("%Y-%m-%d")
    try:
        stats[indicator]  # trigger calculation
    except Exception as exc:
        raise RuntimeError(
            f"Indicator '{indicator}' not available in quant_core.db and "
            f"stockstats could not compute it: {exc}"
        ) from exc

    tail = stats.tail(look_back_days)
    lines = [
        f"## {indicator} values for {symbol.upper()} "
        f"(last {look_back_days} trading days, source: quant_core.db + stockstats)\n"
    ]
    for _, row in tail.iterrows():
        v = row[indicator]
        lines.append(f"{row['Date']}: {'N/A' if pd.isna(v) else v}")
    return "\n".join(lines)


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

    # Get latest fundamentals
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
                lines.append(f"- {label}: {v*100:.2f}%")
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

def get_fund_flow(symbol: str) -> str:
    """Fetch A-share individual stock fund flow from quant_core.db."""
    code = _to_smartmoney_symbol(symbol)

    df = _df_from_sql(
        """
        SELECT trade_date AS Date, main_net_inflow AS main_net,
               main_net_inflow_pct AS main_pct,
               super_large_net_inflow AS super_large_net,
               super_large_net_inflow_pct AS super_large_pct,
               large_net_inflow AS large_net,
               large_net_inflow_pct AS large_pct,
               is_simulated
        FROM fund_flow
        WHERE ts_code = ?
        ORDER BY trade_date DESC
        LIMIT 5
        """,
        (code,),
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


def get_institutional_holdings(symbol: str) -> str:
    raise RuntimeError("Institutional holdings not available in quant_core.db")


def get_northbound_hold(symbol: str) -> str:
    raise RuntimeError("Northbound holdings not available in quant_core.db")


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


def get_macro_indicators() -> str:
    raise RuntimeError("Macro indicators not available in quant_core.db")


# ===========================================================================
# Margin Trading (融资融券) — v2.2
# ===========================================================================

def get_margin_trading(symbol: str) -> str:
    """Fetch margin-trading (融资融券) data from quant_core.db."""
    code = _to_smartmoney_symbol(symbol)

    df = _df_from_sql(
        """
        SELECT trade_date AS Date, margin_balance, margin_buy, margin_repay,
               short_balance, short_sell, short_repay, total_balance
        FROM margin_trading
        WHERE ts_code = ?
        ORDER BY trade_date DESC
        LIMIT 5
        """,
        (code,),
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
        lines.append(f"- 融资余额: {row['margin_balance']:,.0f}")
        lines.append(f"- 融资买入额: {row['margin_buy']:,.0f}")
        lines.append(f"- 融券余量: {row['short_balance']:,.0f}")
        lines.append(f"- 融资融券余额: {row['total_balance']:,.0f}")
        lines.append("")
    return "\n".join(lines)


# ===========================================================================
# Dragon Tiger (龙虎榜) — v2.2
# ===========================================================================

def get_dragon_tiger(symbol: str) -> str:
    """Fetch dragon-tiger-board (龙虎榜) data from quant_core.db."""
    code = _to_smartmoney_symbol(symbol)

    df = _df_from_sql(
        """
        SELECT trade_date AS Date, close_price, pct_change, net_buy_amount,
               buy_amount, sell_amount, turnover_rate, market_cap, reason
        FROM dragon_tiger
        WHERE ts_code = ?
        ORDER BY trade_date DESC
        LIMIT 5
        """,
        (code,),
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

def get_block_trade(symbol: str) -> str:
    """Fetch block-trade (大宗交易) data from quant_core.db."""
    code = _to_smartmoney_symbol(symbol)

    df = _df_from_sql(
        """
        SELECT trade_date AS Date, deal_price, close_price, discount_rate,
               volume, amount, buyer_branch, seller_branch
        FROM block_trade
        WHERE ts_code = ?
        ORDER BY trade_date DESC
        LIMIT 5
        """,
        (code,),
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
    for _, row in df.iterrows():
        lines.append(f"**Date**: {row['Date']}")
        lines.append(
            f"- Main Force: {row['main_net_inflow']:,.0f} ({row['main_net_inflow_pct']:.2f}%)"
        )
        lines.append(f"- Super Large: {row['super_large_net_inflow']:,.0f}")
        lines.append(f"- Large: {row['large_net_inflow']:,.0f}")
        lines.append(f"- Medium: {row['medium_net_inflow']:,.0f}")
        lines.append(f"- Small: {row['small_net_inflow']:,.0f}")
        lines.append("")
    return "\n".join(lines)


# ===========================================================================
# Shareholder Count (股东户数) — v2.2
# ===========================================================================

def get_shareholder_count(symbol: str) -> str:
    """Fetch shareholder-count (股东户数) from quant_core.db."""
    code = _to_smartmoney_symbol(symbol)

    df = _df_from_sql(
        """
        SELECT report_date AS Date, holder_count, holder_count_change_pct,
               avg_shares_per_holder
        FROM shareholder_count
        WHERE ts_code = ?
        ORDER BY report_date DESC
        LIMIT 4
        """,
        (code,),
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
