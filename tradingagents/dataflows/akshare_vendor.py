"""Akshare vendor — A-share financial data via Eastmoney/Xueqiu using akshare.

Each function mirrors the signature of its yfinance counterpart in y_finance.py
and returns a plain string suitable for direct LLM consumption. All A-share
monetary values pass through akshare_common.format_money_cn() so the unit
(亿/万) is always explicit in the output.
"""
from __future__ import annotations

import logging
import math
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Annotated, Any

import akshare as ak
import pandas as pd

from .akshare_common import (
    _akshare_retry,
    format_money_cn,
    no_proxy,
    safe_float,
    to_akshare_symbol,
)
from .errors import NoMarketDataError
from .stockstats_utils import _clean_dataframe

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Patch akshare's tqdm so every progress bar carries context and looks tidy.
# ---------------------------------------------------------------------------
_current_akshare_task: str | None = None
_original_get_tqdm = ak.utils.tqdm.get_tqdm


def _patched_get_tqdm(enable: bool = True):
    # Any: the real base class is resolved at runtime by akshare's get_tqdm.
    tqdm_cls: Any = _original_get_tqdm(enable)
    if not enable:
        return tqdm_cls

    class _AkshareTqdm(tqdm_cls):
        def __init__(self, iterable=None, desc=None, *args, **kwargs):
            if desc is None and _current_akshare_task:
                desc = _current_akshare_task
            kwargs.setdefault(
                "bar_format",
                "{desc} {percentage:3.0f}%|{bar:20}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]",
            )
            kwargs.setdefault("leave", False)
            kwargs.setdefault("ncols", 100)
            super().__init__(iterable, *args, desc=desc, **kwargs)

    return _AkshareTqdm


ak.utils.tqdm.get_tqdm = _patched_get_tqdm


def _to_yyyymmdd(date_str: str) -> str:
    """Convert YYYY-MM-DD to YYYYMMDD (akshare's hist API format)."""
    return date_str.replace("-", "")


@contextmanager
def _akshare_task_context(task_desc: str):
    """Set the global tqdm description for the duration of an akshare call."""
    global _current_akshare_task
    _current_akshare_task = task_desc
    try:
        yield
    finally:
        _current_akshare_task = None


def _filter_df_by_date(
    df: pd.DataFrame | None,
    date_col: str,
    curr_date: str | None,
) -> pd.DataFrame | None:
    """Keep rows whose ``date_col`` is on or before ``curr_date``.

    No-op when *df* is None/empty or ``curr_date`` is not provided.
    The date column is coerced with ``pd.to_datetime`` before comparison.
    """
    if df is None or df.empty or not curr_date:
        return df
    if date_col not in df.columns:
        return df
    parsed = pd.to_datetime(df[date_col], errors="coerce")
    cutoff = pd.to_datetime(curr_date, errors="coerce")
    if pd.isna(cutoff):
        return df
    return df[parsed <= cutoff].copy()


def get_stock_data(
    symbol: Annotated[str, "A-share ticker e.g. 600519.SS"],
    start_date: Annotated[str, "Start date YYYY-MM-DD"],
    end_date: Annotated[str, "End date YYYY-MM-DD"],
) -> str:
    """Fetch A-share daily OHLCV from Eastmoney via akshare (forward-adjusted).

    Uses exponential-backoff retry (3 attempts) to tolerate AkShare
    rate-limiting before falling back to the next vendor in the chain.
    """
    code = to_akshare_symbol(symbol, "bare")

    with _akshare_task_context(f"📊 {symbol} 历史行情"), no_proxy():
        df = _akshare_retry(
            lambda: ak.stock_zh_a_hist(
                symbol=code,
                period="daily",
                start_date=_to_yyyymmdd(start_date),
                end_date=_to_yyyymmdd(end_date),
                adjust="qfq",
            ),
            max_retries=3,
            base_delay=2.0,
        )

    if df is None or df.empty:
        raise NoMarketDataError(
            symbol,
            detail=f"no rows between {start_date} and {end_date} via akshare",
        )

    rename_map = {
        "日期": "Date",
        "开盘": "Open",
        "收盘": "Close",
        "最高": "High",
        "最低": "Low",
        "成交量": "Volume",
    }
    df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
    if "Date" in df.columns:
        df = df.set_index("Date")

    for col in ("Open", "High", "Low", "Close"):
        if col in df.columns:
            df[col] = df[col].round(2)

    header = (
        f"# Stock data for {symbol.upper()} from {start_date} to {end_date}\n"
        f"# Total records: {len(df)}\n"
        f"# Source: akshare (Eastmoney, 前复权)\n"
        f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    )
    return header + df.to_csv()


def _safe_call(func, *args, **kwargs):
    """Invoke an akshare function with network-error retry.

    Transient network/parse errors are retried; once retries are exhausted the
    exception propagates so ``route_to_vendor`` can fall back to the next vendor
    instead of treating the failure as empty data.
    """
    return _akshare_retry(lambda: func(*args, **kwargs))


def _yjbb_report_date_for(curr_date: str | None) -> str:
    """Pick the most recently available yjbb report date as YYYYMMDD.

    Q1 results land in late April, Q2 in late August, Q3 in late October,
    so we lag by ~one month relative to the period-end.
    """
    if curr_date:
        y_str, m_str, _ = curr_date.split("-")
        y, m = int(y_str), int(m_str)
    else:
        now = datetime.now()
        y, m = now.year, now.month
    if m >= 11:
        return f"{y}0930"
    if m >= 8:
        return f"{y}0630"
    if m >= 5:
        return f"{y}0331"
    return f"{y - 1}0930"


def get_fundamentals(
    symbol: Annotated[str, "A-share ticker"],
    curr_date: Annotated[str, "Current date YYYY-MM-DD"],
) -> str:
    """Combine company-info + latest performance report into a fundamentals brief."""
    bare = to_akshare_symbol(symbol, "bare")

    with _akshare_task_context(f"📊 {symbol} 基本面"), no_proxy():
        info_df = _safe_call(ak.stock_individual_info_em, symbol=bare)
        report_date = _yjbb_report_date_for(curr_date)
        yjbb_df = _safe_call(ak.stock_yjbb_em, date=report_date)

    lines = [
        f"# Fundamentals for {symbol.upper()} as of {curr_date}",
        "# Source: akshare (Eastmoney)",
        "",
    ]

    if info_df is not None and not info_df.empty:
        info = dict(zip(info_df["item"], info_df["value"], strict=False))
        for label in ("股票简称", "行业", "上市时间", "总股本", "流通股", "总市值", "流通市值"):
            if label in info and info[label] not in (None, ""):
                v = info[label]
                if label in ("总市值", "流通市值"):
                    v = format_money_cn(safe_float(v))
                elif label in ("总股本", "流通股"):
                    nv = safe_float(v)
                    v = f"{nv:,.0f}" if nv is not None else v
                lines.append(f"- {label}: {v}")
        lines.append("")

    if yjbb_df is not None and not yjbb_df.empty:
        row = yjbb_df[yjbb_df["股票代码"] == bare]
        if not row.empty:
            r = row.iloc[0]
            period = f"{report_date[:4]}-{report_date[4:6]}-{report_date[6:]}"
            lines.append(f"## 业绩报表 (报告期 {period})")
            for col, label in [
                ("营业总收入-同比增长", "营收同比增长(YoY)"),
                ("净利润-同比增长", "净利润同比增长(YoY)"),
                ("销售毛利率", "毛利率"),
                ("净资产收益率", "ROE"),
                ("每股收益", "EPS"),
                ("每股经营现金流量", "每股经营现金流"),
            ]:
                v = safe_float(r.get(col))
                if v is None:
                    continue
                unit = "%" if any(k in label for k in ("增长", "毛利率", "ROE")) else ""
                lines.append(f"- {label}: {v:.2f}{unit}")

    if len(lines) <= 3:
        raise NoMarketDataError(
            symbol, detail=f"no fundamentals available for {symbol} via akshare"
        )
    return "\n".join(lines)


_BALANCE_FIELDS = [
    ("TOTAL_ASSETS", "总资产"),
    ("TOTAL_CURRENT_ASSETS", "流动资产"),
    ("MONETARYFUNDS", "货币资金"),
    ("ACCOUNTS_RECE", "应收账款"),
    ("INVENTORY", "存货"),
    ("FIXED_ASSET", "固定资产"),
    ("TOTAL_LIABILITIES", "总负债"),
    ("TOTAL_CURRENT_LIAB", "流动负债"),
    ("TOTAL_EQUITY", "股东权益合计"),
]


def get_balance_sheet(
    symbol: Annotated[str, "A-share ticker"],
    freq: str = "quarterly",
    curr_date: str | None = None,
) -> str:
    """Fetch A-share balance sheet (latest report period) via akshare."""
    code = to_akshare_symbol(symbol, "upper_prefix")
    with _akshare_task_context(f"📊 {symbol} 资产负债表"), no_proxy():
        df = _akshare_retry(
            lambda: ak.stock_balance_sheet_by_report_em(symbol=code)
        )

    if df is None or df.empty:
        raise NoMarketDataError(
            symbol, detail=f"no balance sheet available for {symbol} via akshare"
        )

    latest = df.iloc[0].to_dict()
    period = latest.get("REPORT_DATE", "N/A")
    header = (
        f"# Balance Sheet for {symbol.upper()} ({period})\n"
        f"# Source: akshare (Eastmoney 资产负债表)\n"
        f"# Currency: CNY (元)\n\n"
    )
    return header + _format_row_section(latest, _BALANCE_FIELDS)


def get_cashflow(
    symbol: Annotated[str, "A-share ticker"],
    freq: str = "quarterly",
    curr_date: str | None = None,
) -> str:
    """Fetch A-share cash flow statement (latest report period) via akshare."""
    code = to_akshare_symbol(symbol, "upper_prefix")
    with _akshare_task_context(f"📊 {symbol} 现金流量表"), no_proxy():
        df = _akshare_retry(
            lambda: ak.stock_cash_flow_sheet_by_report_em(symbol=code)
        )

    if df is None or df.empty:
        raise NoMarketDataError(
            symbol, detail=f"no cash flow statement available for {symbol} via akshare"
        )

    latest = df.iloc[0].to_dict()
    period = latest.get("REPORT_DATE", "N/A")
    header = (
        f"# Cash Flow Statement for {symbol.upper()} ({period})\n"
        f"# Source: akshare (Eastmoney 现金流量表)\n"
        f"# Currency: CNY (元)\n\n"
    )
    return header + _format_row_section(latest, _CASHFLOW_FIELDS)


_CASHFLOW_FIELDS = [
    ("NETCASH_OPERATE", "经营活动现金流净额"),
    ("NETCASH_INVEST", "投资活动现金流净额"),
    ("NETCASH_FINANCE", "筹资活动现金流净额"),
    ("CCE_ADD", "现金及等价物净增加额"),
    ("END_CCE", "期末现金及等价物余额"),
]


_INCOME_FIELDS = [
    ("TOTAL_OPERATE_INCOME", "营业总收入"),
    ("OPERATE_INCOME", "营业收入"),
    ("OPERATE_COST", "营业成本"),
    ("OPERATE_PROFIT", "营业利润"),
    ("TOTAL_PROFIT", "利润总额"),
    ("PARENT_NETPROFIT", "归母净利润"),
    ("DEDUCT_PARENT_NETPROFIT", "扣非归母净利润"),
    ("BASIC_EPS", "基本每股收益"),
    ("DILUTED_EPS", "稀释每股收益"),
]


def _format_row_section(row: dict, fields) -> str:
    """Format a sequence of (akshare_key, label) pairs from *row* into lines.

    Monetary scale is auto-detected: |v| >= 1000 uses format_money_cn (亿/万);
    smaller values (EPS, ratios) keep raw float with 4 decimal places.
    """
    lines = []
    for key, label in fields:
        v = safe_float(row.get(key))
        if v is None:
            continue
        if abs(v) >= 1000:
            lines.append(f"- {label}: {format_money_cn(v)}")
        else:
            lines.append(f"- {label}: {v:.4f}")
    return "\n".join(lines) if lines else "- (no fields available)"


def get_income_statement(
    symbol: Annotated[str, "A-share ticker"],
    freq: Annotated[str, "annual/quarterly (currently informational)"] = "quarterly",
    curr_date: str | None = None,
) -> str:
    """Fetch A-share income statement (latest report period) via akshare."""
    code = to_akshare_symbol(symbol, "upper_prefix")
    with _akshare_task_context(f"📊 {symbol} 利润表"), no_proxy():
        df = _akshare_retry(
            lambda: ak.stock_profit_sheet_by_report_em(symbol=code)
        )

    if df is None or df.empty:
        raise NoMarketDataError(
            symbol, detail=f"no income statement available for {symbol} via akshare"
        )

    latest = df.iloc[0].to_dict()
    period = latest.get("REPORT_DATE", "N/A")

    header = (
        f"# Income Statement for {symbol.upper()} ({period})\n"
        f"# Source: akshare (Eastmoney 利润表)\n"
        f"# Currency: CNY (元)\n\n"
    )
    return header + _format_row_section(latest, _INCOME_FIELDS)


def get_indicators(
    symbol: Annotated[str, "A-share ticker"],
    indicator: Annotated[str, "stockstats indicator name e.g. rsi_14, macd"],
    curr_date: Annotated[str, "Current date YYYY-MM-DD"],
    look_back_days: Annotated[int, "How many trading days to report"],
) -> str:
    """Compute a stockstats indicator window on akshare-sourced A-share K-line.

    Raises on network/connection errors so ``route_to_vendor`` can fall back to
    yfinance. Returns a plain-string message only for soft data issues (empty
    DataFrame, unknown indicator, etc.).
    """
    from stockstats import wrap

    bare = to_akshare_symbol(symbol, "bare")
    end = datetime.strptime(curr_date, "%Y-%m-%d")
    # Pull enough history to warm up long indicators (e.g. 200 SMA).
    start = end - pd.Timedelta(days=look_back_days * 2 + 260)

    with _akshare_task_context(f"📊 {symbol} 技术指标({indicator})"), no_proxy():
        df = _akshare_retry(
            lambda: ak.stock_zh_a_hist(
                symbol=bare,
                period="daily",
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
                adjust="qfq",
            )
        )

    if df is None or df.empty:
        raise NoMarketDataError(
            symbol, detail=f"no K-line data for {symbol} to compute {indicator} via akshare"
        )

    df = df.rename(columns={
        "日期": "Date", "开盘": "Open", "收盘": "Close",
        "最高": "High", "最低": "Low", "成交量": "Volume",
    })
    df = _clean_dataframe(df)
    stats = wrap(df)
    stats["Date"] = stats["Date"].dt.strftime("%Y-%m-%d")
    stats[indicator]  # trigger calculation

    tail = stats.tail(look_back_days)
    lines = [
        f"## {indicator} values for {symbol.upper()} "
        f"(last {look_back_days} trading days, source: akshare)\n"
    ]
    for _, row in tail.iterrows():
        v = row[indicator]
        lines.append(f"{row['Date']}: {'N/A' if pd.isna(v) else v}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# News, announcements & governance data
# ---------------------------------------------------------------------------


def get_news(
    symbol: Annotated[str, "A-share ticker e.g. 300454.SZ"],
    start_date: Annotated[str, "Start date YYYY-MM-DD"],
    end_date: Annotated[str, "End date YYYY-MM-DD"],
) -> str:
    """Fetch A-share company-specific news from Eastmoney via akshare."""
    code = to_akshare_symbol(symbol, "bare")
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")

    with _akshare_task_context(f"📰 {symbol} 个股新闻"), no_proxy():
        df = _safe_call(ak.stock_news_em, symbol=code)

    if df is None or df.empty:
        raise NoMarketDataError(
            symbol,
            detail=f"no news found for {symbol} between {start_date} and {end_date} via akshare",
        )

    # Filter by date
    df["发布时间"] = pd.to_datetime(df["发布时间"], errors="coerce")
    mask = (df["发布时间"] >= start_dt) & (df["发布时间"] <= end_dt + pd.Timedelta(days=1))
    df = df[mask]

    if df.empty:
        raise NoMarketDataError(
            symbol,
            detail=f"no news found for {symbol} between {start_date} and {end_date} via akshare",
        )

    lines = [
        f"## {symbol.upper()} News from {start_date} to {end_date} (source: akshare / Eastmoney)\n",
        f"Total articles: {len(df)}\n",
    ]
    for _, row in df.iterrows():
        lines.append(f"### {row.get('新闻标题', 'N/A')} (source: {row.get('文章来源', 'N/A')})")
        if row.get("新闻内容"):
            content = str(row["新闻内容"]).strip()
            if content:
                lines.append(content)
        if row.get("发布时间"):
            lines.append(f"Published: {row['发布时间']}")
        if row.get("新闻链接"):
            lines.append(f"Link: {row['新闻链接']}")
        lines.append("")

    return "\n".join(lines)


def get_global_news(
    curr_date: str,
    look_back_days: int | None = None,
    limit: int | None = None,
) -> str:
    """Fetch broad market / global news from Eastmoney via akshare (fallback).

    Uses a representative index ticker (SH000001, Shanghai Composite) through
    ``ak.stock_news_em`` to get market-wide Eastmoney news articles.

    Args:
        curr_date: Current date in yyyy-mm-dd format (used to cap lookback).
        look_back_days: Number of days to look back (default 7).
        limit: Maximum number of articles (default 20).

    Returns:
        Formatted string containing recent news articles.
    """
    if look_back_days is None:
        look_back_days = 7
    if limit is None:
        limit = 20

    start_dt = datetime.strptime(curr_date, "%Y-%m-%d") - timedelta(days=look_back_days)
    end_dt = datetime.strptime(curr_date, "%Y-%m-%d") + timedelta(days=1)

    # Use Shanghai Composite index as a proxy for broad market news
    with _akshare_task_context("📰 市场综合新闻"), no_proxy():
        df = _safe_call(ak.stock_news_em, symbol="000001")

    if df is None or df.empty:
        raise NoMarketDataError(
            curr_date, detail="no global/market news available via akshare fallback"
        )

    df["发布时间"] = pd.to_datetime(df["发布时间"], errors="coerce")
    mask = (df["发布时间"] >= start_dt) & (df["发布时间"] <= end_dt)
    df = df[mask].head(limit)

    if df.empty:
        raise NoMarketDataError(
            curr_date, detail="no global/market news available via akshare fallback"
        )

    lines = [
        "## Global / Market News (source: akshare / Eastmoney fallback)",
        f"Period: {start_dt.date()} to {curr_date}",
        f"Total articles: {len(df)}",
        "",
    ]
    for _, row in df.iterrows():
        lines.append(f"### {row.get('新闻标题', 'N/A')}")
        if row.get("文章来源"):
            lines.append(f"Source: {row.get('文章来源', 'N/A')}")
        if row.get("新闻内容"):
            content = str(row["新闻内容"]).strip()
            if content:
                lines.append(content)
        if row.get("发布时间"):
            lines.append(f"Published: {row['发布时间']}")
        lines.append("")

    return "\n".join(lines)


def get_insider_transactions(
    symbol: Annotated[str, "A-share ticker e.g. 300454.SZ"],
) -> str:
    """Fetch A-share shareholder change data from 10jqka via akshare."""
    code = to_akshare_symbol(symbol, "bare")

    with _akshare_task_context(f"🏛 {symbol} 股东变动"), no_proxy():
        df = _safe_call(ak.stock_shareholder_change_ths, symbol=code)

    if df is None or df.empty:
        raise NoMarketDataError(
            symbol,
            detail=f"no shareholder change data found for {symbol} via akshare",
        )

    lines = [
        f"## {symbol.upper()} Shareholder Changes (source: akshare / 同花顺)\n",
        f"Total records: {len(df)}\n",
    ]
    for _, row in df.iterrows():
        lines.append(f"- **公告日期**: {row.get('公告日期', 'N/A')}")
        lines.append(f"  **变动股东**: {row.get('变动股东', 'N/A')}")
        lines.append(f"  **变动数量**: {row.get('变动数量', 'N/A')}")
        lines.append(f"  **交易均价**: {row.get('交易均价', 'N/A')}")
        lines.append(f"  **剩余股份**: {row.get('剩余股份总数', 'N/A')}")
        lines.append(f"  **变动期间**: {row.get('变动期间', 'N/A')}")
        lines.append(f"  **变动途径**: {row.get('变动途径', 'N/A')}")
        lines.append("")

    return "\n".join(lines)


def get_company_announcements(
    symbol: Annotated[str, "A-share ticker e.g. 300454.SZ"],
    start_date: Annotated[str, "Start date YYYY-MM-DD"],
    end_date: Annotated[str, "End date YYYY-MM-DD"],
) -> str:
    """Fetch A-share company announcements/notices from Eastmoney via akshare."""
    code = to_akshare_symbol(symbol, "bare")

    with _akshare_task_context(f"📋 {symbol} 公司公告"), no_proxy():
        df = _safe_call(
            ak.stock_individual_notice_report,
            security=code,
            begin_date=start_date,
            end_date=end_date,
        )

    if df is None or df.empty:
        raise NoMarketDataError(
            symbol,
            detail=f"no company announcements found for {symbol} between {start_date} and {end_date} via akshare",
        )

    lines = [
        f"## {symbol.upper()} Company Announcements from {start_date} to {end_date} "
        f"(source: akshare / Eastmoney)\n",
        f"Total notices: {len(df)}\n",
    ]
    for _, row in df.iterrows():
        lines.append(f"### {row.get('公告标题', 'N/A')}")
        if row.get("公告类型"):
            lines.append(f"**Type**: {row['公告类型']}")
        if row.get("公告日期"):
            lines.append(f"**Date**: {row['公告日期']}")
        if row.get("网址"):
            lines.append(f"**Link**: {row['网址']}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Fund flow & northbound data
# ---------------------------------------------------------------------------


def get_fund_flow(
    symbol: str,
    curr_date: str | None = None,
) -> str:
    """Fetch A-share individual stock fund flow (主力/超大单/大单/中单/小单)."""
    code = to_akshare_symbol(symbol, "bare")
    prefix = to_akshare_symbol(symbol, "lower_prefix")[:2]  # "sh" or "sz"

    with _akshare_task_context(f"💰 {symbol} 资金流向"), no_proxy():
        df = _safe_call(ak.stock_individual_fund_flow, stock=code, market=prefix)

    df = _filter_df_by_date(df, "日期", curr_date)

    if df is None or df.empty:
        raise NoMarketDataError(
            symbol, detail=f"no fund flow data found for {symbol} via akshare"
        )

    # Keep last 5 trading days
    df = df.head(5)
    lines = [
        f"## {symbol.upper()} Fund Flow (source: akshare / Eastmoney)",
        f"Total records: {len(df)} trading days",
        "",
    ]
    for _, row in df.iterrows():
        lines.append(f"**Date**: {row.get('日期', 'N/A')}")
        lines.append(f"- Close: {row.get('收盘价', 'N/A')}")
        lines.append(f"- Change: {row.get('涨跌幅', 'N/A')}%")
        lines.append(f"- Main Force Net Inflow: {row.get('主力净流入-净额', 'N/A')} ({row.get('主力净流入-净占比', 'N/A')}%)")
        lines.append(f"- Super Large Order Net Inflow: {row.get('超大单净流入-净额', 'N/A')} ({row.get('超大单净流入-净占比', 'N/A')}%)")
        lines.append(f"- Large Order Net Inflow: {row.get('大单净流入-净额', 'N/A')} ({row.get('大单净流入-净占比', 'N/A')}%)")
        lines.append(f"- Medium Order Net Inflow: {row.get('中单净流入-净额', 'N/A')} ({row.get('中单净流入-净占比', 'N/A')}%)")
        lines.append(f"- Small Order Net Inflow: {row.get('小单净流入-净额', 'N/A')} ({row.get('小单净流入-净占比', 'N/A')}%)")
        lines.append("")

    return "\n".join(lines)


def get_northbound_hold(
    symbol: str,
    curr_date: str | None = None,
) -> str:
    """Fetch A-share northbound (Stock Connect) holding data."""
    code = to_akshare_symbol(symbol, "bare")
    with _akshare_task_context(f"🌏 {symbol} 北向资金"), no_proxy():
        df = _safe_call(ak.stock_hsgt_individual_em, symbol=code)

    df = _filter_df_by_date(df, "持股日期", curr_date)

    if df is None or df.empty:
        raise NoMarketDataError(
            symbol,
            detail=f"no northbound holding data found for {symbol} via akshare",
        )

    df = df.head(5)
    lines = [
        f"## {symbol.upper()} Northbound (Stock Connect) Holdings (source: akshare / Eastmoney)",
        f"Total records: {len(df)} trading days",
        "",
    ]
    for _, row in df.iterrows():
        lines.append(f"**Date**: {row.get('持股日期', 'N/A')}")
        lines.append(f"- Holding Shares: {row.get('持股数量', 'N/A')}")
        lines.append(f"- Holding Market Value: {row.get('持股市值', 'N/A')}")
        lines.append(f"- % of Tradable Shares: {row.get('持股数量占A股百分比', 'N/A')}%")
        lines.append(f"- Daily Net Buy Shares: {row.get('今日增持股数', 'N/A')}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Restricted share release
# ---------------------------------------------------------------------------


def get_restricted_release(
    symbol: Annotated[str, "A-share ticker"],
    start_date: Annotated[str, "Start date YYYY-MM-DD"],
    end_date: Annotated[str, "End date YYYY-MM-DD"],
) -> str:
    """Fetch A-share restricted share release (限售解禁) details."""
    code = to_akshare_symbol(symbol, "bare")

    with _akshare_task_context(f"🔓 {symbol} 限售解禁"), no_proxy():
        df = _safe_call(
            ak.stock_restricted_release_detail_em,
            start_date=start_date,
            end_date=end_date,
        )

    if df is None or df.empty:
        raise NoMarketDataError(
            symbol,
            detail=f"no restricted share release data found for {symbol} between {start_date} and {end_date} via akshare",
        )

    # Client-side filter by stock code
    if "股票代码" in df.columns:
        df = df[df["股票代码"].astype(str).str.strip() == code]

    if df.empty:
        raise NoMarketDataError(
            symbol,
            detail=f"no restricted share release events for {symbol} between {start_date} and {end_date}",
        )

    lines = [
        f"## {symbol.upper()} Restricted Share Release (source: akshare / Eastmoney)",
        f"Total events: {len(df)}",
        "",
    ]
    for _, row in df.iterrows():
        lines.append(f"**Release Date**: {row.get('解禁时间', 'N/A')}")
        lines.append(f"- Type: {row.get('限售股类型', 'N/A')}")
        lines.append(f"- Release Quantity: {row.get('解禁数量', 'N/A')}")
        lines.append(f"- Actual Release Market Value: {row.get('实际解禁市值', 'N/A')}")
        lines.append(f"- % of Pre-release Float Cap: {row.get('占解禁前流通市值比例', 'N/A')}")
        lines.append(f"- Pre-release Close Price: {row.get('解禁前一交易日收盘价', 'N/A')}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Industry valuation comparison
# ---------------------------------------------------------------------------


def get_industry_valuation(symbol: str) -> str:
    """Fetch A-share industry valuation comparison (PE/PB) for the given stock."""
    code = to_akshare_symbol(symbol, "bare")

    with _akshare_task_context(f"🏭 {symbol} 行业估值"), no_proxy():
        info_df = _safe_call(ak.stock_individual_info_em, symbol=code)
        spot_df = _safe_call(ak.stock_zh_a_spot_em)
        value_df = _safe_call(ak.stock_value_em, symbol=code)

    if info_df is None or info_df.empty:
        raise NoMarketDataError(
            symbol,
            detail=f"no individual info data available for {symbol} via akshare",
        )

    info = dict(zip(info_df["item"], info_df["value"], strict=False))
    industry = info.get("行业", "")

    lines = [
        f"## {symbol.upper()} Industry Valuation Comparison (source: akshare / Eastmoney)",
        "",
    ]

    # Target stock metrics
    lines.append(f"### {symbol.upper()} Current Valuation")
    lines.append(f"- Industry: {industry or 'N/A'}")

    target_pe_dyn = None
    target_pb = None
    if spot_df is not None and not spot_df.empty:
        target_rows = spot_df[spot_df["代码"].astype(str).str.strip() == code]
        if not target_rows.empty:
            target = target_rows.iloc[0]
            lines.append(f"- Latest Price: {target.get('最新价', 'N/A')}")
            lines.append(f"- PE (Dynamic): {target.get('市盈率-动态', 'N/A')}")
            lines.append(f"- PB: {target.get('市净率', 'N/A')}")
            target_pe_dyn = safe_float(target.get("市盈率-动态"))
            target_pb = safe_float(target.get("市净率"))

    if value_df is not None and not value_df.empty:
        latest = value_df.iloc[-1]
        lines.append(f"- PE (TTM): {latest.get('PE(TTM)', 'N/A')}")
        lines.append(f"- PE (Static): {latest.get('PE(静)', 'N/A')}")
        lines.append(f"- PEG: {latest.get('PEG值', 'N/A')}")

    lines.append("")

    # Market-wide comparison
    if spot_df is not None and not spot_df.empty:
        pe_dyn = pd.to_numeric(spot_df["市盈率-动态"], errors="coerce").dropna()
        pb = pd.to_numeric(spot_df["市净率"], errors="coerce").dropna()

        lines.append("### Market-Wide Comparison (All A-Shares)")
        lines.append(f"- Market Sample Size: {len(spot_df)} stocks")

        if not pe_dyn.empty:
            lines.append(f"- PE (Dynamic) Market Median: {pe_dyn.median():.2f}")
            lines.append(f"- PE (Dynamic) Market Mean: {pe_dyn.mean():.2f}")
            if target_pe_dyn is not None:
                pct = (pe_dyn < target_pe_dyn).mean() * 100
                lines.append(f"- {symbol.upper()} PE Rank: {pct:.1f}% of all A-shares have lower PE")

        if not pb.empty:
            lines.append(f"- PB Market Median: {pb.median():.2f}")
            if target_pb is not None:
                pct = (pb < target_pb).mean() * 100
                lines.append(f"- {symbol.upper()} PB Rank: {pct:.1f}% of all A-shares have lower PB")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Macro quantitative indicators
# ---------------------------------------------------------------------------


def get_macro_indicators(
    indicator: Annotated[
        str,
        'Macro indicator: "pmi", "cpi", "m2", "social_finance"',
    ],
    curr_date: str | None = None,
    look_back_days: int | None = None,
) -> str:
    """Fetch China macro quantitative indicators via akshare.

    Raises NoMarketDataError for indicators not supported by akshare, so the
    vendor fallback chain (e.g. FRED) is tried next.
    """
    indicator = indicator.lower().strip()

    with _akshare_task_context(f"📈 宏观指标: {indicator}"), no_proxy():
        if indicator == "pmi":
            df = _safe_call(ak.macro_china_pmi)
            title = "China Manufacturing & Non-Manufacturing PMI"
            cols = ["月份", "制造业-指数", "制造业-同比增长", "非制造业-指数", "非制造业-同比增长"]
        elif indicator == "cpi":
            df = _safe_call(ak.macro_china_cpi)
            title = "China Consumer Price Index (CPI)"
            cols = None  # use all cols
        elif indicator == "m2":
            df = _safe_call(ak.macro_china_money_supply)
            title = "China M2 Money Supply"
            cols = None
        elif indicator in ("social_finance", "社融"):
            df = _safe_call(ak.macro_china_shrzgm)
            title = "China Aggregate Social Financing"
            cols = None
        else:
            raise NoMarketDataError(
                indicator,
                detail=f"Not an akshare-supported China macro indicator: {indicator}",
            )

    if df is None or df.empty:
        raise NoMarketDataError(
            indicator,
            detail=f"no macro data available for indicator '{indicator}' via akshare",
        )

    # Keep last 6 periods
    df = df.head(6)
    lines = [f"## {title} (source: akshare)", f"Total records: {len(df)}", ""]

    display_cols = cols if cols else list(df.columns)
    for _, row in df.iterrows():
        for col in display_cols:
            if col in row:
                lines.append(f"- {col}: {row[col]}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Margin trading (融资融券) — v2.2
# ---------------------------------------------------------------------------


def _nearest_trade_date(base: str | None = None) -> str:
    """Return the most recent trading date as YYYYMMDD, optionally before *base* (YYYYMMDD)."""
    df = _safe_call(ak.tool_trade_date_hist_sina)
    if base is not None:
        # Defensive: ensure base has ≥8 chars for YYYYMMDD parsing
        base = base[:8]
        if len(base) < 8:
            logger.warning("_nearest_trade_date received short base=%r, falling back to now", base)
            base = None
    if df is None or df.empty:
        return (base or datetime.now().strftime("%Y%m%d"))[:8]
    date_col = "trade_date" if "trade_date" in df.columns else df.columns[0]
    target = datetime.strptime(base, "%Y%m%d") if base else datetime.now()
    try:
        valid = df[df[date_col] <= target.strftime("%Y-%m-%d")]
    except TypeError:
        valid = df[pd.to_datetime(df[date_col]).dt.date <= target.date()]
    if valid.empty:
        return target.strftime("%Y%m%d")
    latest = valid[date_col].max()
    if hasattr(latest, "strftime"):
        return latest.strftime("%Y%m%d")
    return str(latest).replace("-", "")


def get_margin_trading(
    symbol: str,
    curr_date: str | None = None,
) -> str:
    """Fetch A-share margin-trading (融资融券) data via akshare.

    Uses SSE/SZSE daily margin-trading detail tables and filters to the
    requested stock. If the most recent trading day has no data, tries up
    to 5 prior trading days to handle stale-trade-date edge cases.
    """
    code = to_akshare_symbol(symbol, "bare")
    exchange = to_akshare_symbol(symbol, "lower_prefix")[:2]

    if exchange not in ("sh", "sz"):
        return (
            f"Margin-trading data for {symbol} ({exchange}) is not available "
            f"via akshare. Only SSE (sh) and SZSE (sz) A-shares are supported. "
            f"Consider enabling smartmoney_db for local cached data."
        )

    stock_col = "标的证券代码" if exchange == "sh" else "证券代码"
    margin_func = ak.stock_margin_detail_sse if exchange == "sh" else ak.stock_margin_detail_szse

    # Try up to 5 prior trading dates, capped by curr_date when provided
    base_date = _to_yyyymmdd(curr_date) if curr_date else datetime.now().strftime("%Y%m%d")
    for _ in range(5):
        date_str = _nearest_trade_date(base_date)
        with _akshare_task_context(f"📈 {symbol} 融资融券"), no_proxy():
            df = _safe_call(margin_func, date=date_str)
        if df is not None and not df.empty:
            row = df[df[stock_col] == code]
            if not row.empty:
                r = row.iloc[0].to_dict()

                def _get(*keys, _r=r):
                    for k in keys:
                        if k in _r and _r[k] is not None:
                            return _r[k]
                    return "N/A"

                lines = [
                    f"## {symbol.upper()} Margin Trading (融资融券) (source: akshare / SSE·SZSE)",
                    f"Date: {date_str}",
                    "",
                    f"- 融资余额: {_get('融资余额', '融资余额')}",
                    f"- 融资买入额: {_get('融资买入额', '融资买入额')}",
                    f"- 融券余量: {_get('融券余量', '融券余量')}",
                    f"- 融券余额: {_get('融券余额', '融券余额')}",
                    f"- 融资融券余额: {_get('融资融券余额', '融资融券余额')}",
                ]
                return "\n".join(lines)
        # Move base date back by one day for next attempt
        base_dt = datetime.strptime(base_date, "%Y%m%d") - timedelta(days=1)
        base_date = base_dt.strftime("%Y%m%d")

    raise NoMarketDataError(
        symbol,
        detail=f"no margin-trading data found for {symbol} in the last 5 trading days via akshare",
    )


# ---------------------------------------------------------------------------
# Dragon tiger (龙虎榜) — v2.2
# ---------------------------------------------------------------------------


def get_dragon_tiger(
    symbol: str,
    curr_date: str | None = None,
) -> str:
    """Fetch A-share dragon-tiger-board (龙虎榜) data via akshare.

    Uses ``stock_lhb_stock_detail_date_em`` to discover trading dates when the
    stock appeared on the LHB, then fetches buy/sell detail for the most recent
    date.  Falls back to the nearest trade date if the date-discovery API fails.
    """
    code = to_akshare_symbol(symbol, "bare")

    # Discover dates when this stock appeared on the LHB.
    # A network failure here is not fatal: we fall back to the nearest trade date.
    date_str = _nearest_trade_date(_to_yyyymmdd(curr_date) if curr_date else None)
    try:
        with _akshare_task_context(f"🔥 {symbol} 龙虎榜日期"), no_proxy():
            dates_df = _safe_call(ak.stock_lhb_stock_detail_date_em, symbol=code)
    except Exception:
        dates_df = None
    if dates_df is not None and not dates_df.empty and "交易日" in dates_df.columns:
        try:
            # akshare returns 交易日 as datetime.date; accept datetime/str too.
            # stock_lhb_stock_detail_em requires date as YYYYMMDD.
            if curr_date:
                parsed = pd.to_datetime(dates_df["交易日"], errors="coerce")
                valid = dates_df[parsed <= pd.to_datetime(curr_date)]
                latest = valid["交易日"].max() if not valid.empty else None
            else:
                latest = dates_df["交易日"].max()
            if latest is not None and pd.notna(latest):
                if hasattr(latest, "strftime"):
                    date_str = latest.strftime("%Y%m%d")
                else:
                    digits = "".join(ch for ch in str(latest) if ch.isdigit())
                    if len(digits) >= 8:
                        date_str = digits[:8]
        except (TypeError, ValueError):
            pass  # date discovery failed — keep the nearest-trade-date fallback

    with _akshare_task_context(f"🔥 {symbol} 龙虎榜"), no_proxy():
        buy_df = _safe_call(
            ak.stock_lhb_stock_detail_em, symbol=code, date=date_str, flag="买入"
        )
        sell_df = _safe_call(
            ak.stock_lhb_stock_detail_em, symbol=code, date=date_str, flag="卖出"
        )

    buy_rows = [] if buy_df is None or buy_df.empty else buy_df.to_dict("records")
    sell_rows = [] if sell_df is None or sell_df.empty else sell_df.to_dict("records")

    if not buy_rows and not sell_rows:
        raise NoMarketDataError(
            symbol,
            detail=f"no dragon-tiger-board data for {symbol} on {date_str} via akshare",
        )

    lines = [
        f"## {symbol.upper()} Dragon Tiger Board (龙虎榜) (source: akshare / Eastmoney)",
        f"Date: {date_str}",
        "",
    ]

    if buy_rows:
        lines.append(f"### Buy-side 买入 ({len(buy_rows)} entries)")
        for r in buy_rows:
            lines.append(f"- {r.get('营业部名称', 'N/A')}: "
                         f"{r.get('买入金额', 'N/A')} (净额 {r.get('净额', 'N/A')})")
        lines.append("")

    if sell_rows:
        lines.append(f"### Sell-side 卖出 ({len(sell_rows)} entries)")
        for r in sell_rows:
            lines.append(f"- {r.get('营业部名称', 'N/A')}: "
                         f"{r.get('卖出金额', 'N/A')} (净额 {r.get('净额', 'N/A')})")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Block trade (大宗交易) — v2.2
# ---------------------------------------------------------------------------


def get_block_trade(
    symbol: str,
    curr_date: str | None = None,
) -> str:
    """Fetch A-share block-trade (大宗交易) data via akshare.

    AkShare does not expose a dedicated individual-stock block-trade API.
    This function falls back with a guided message so the agent can request
    smartmoney_db instead.
    """
    return (
        f"No block-trade (大宗交易) data for {symbol} via akshare. "
        f"AkShare's public API does not expose a per-stock block-trade endpoint. "
        f"Consider enabling smartmoney_db (quant_core.db) for local cached "
        f"block-trade data, or use get_insider_transactions as a proxy for "
        f"large off-market activity."
    )


# ---------------------------------------------------------------------------
# Sector fund flow (板块资金流向) — v2.2
# ---------------------------------------------------------------------------


def get_sector_fund_flow(sector_name: str) -> str:
    """Fetch A-share sector fund-flow (板块资金流向) via akshare.

    Uses the Eastmoney sector fund-flow history API for the requested
    industry name (e.g. 白酒, 银行, 新能源).
    """
    with _akshare_task_context(f"🌊 {sector_name} 板块资金流"), no_proxy():
        df = _safe_call(ak.stock_sector_fund_flow_hist, symbol=sector_name)

    if df is None or df.empty:
        raise NoMarketDataError(
            sector_name,
            detail=f"no sector fund-flow data for '{sector_name}' via akshare",
        )

    lines = [
        f"## {sector_name} Sector Fund Flow (板块资金流向) (source: akshare / Eastmoney)",
        f"Total records: {len(df)}",
        "",
    ]
    for _, r in df.iterrows():
        lines.append(f"**Date**: {r.get('日期', 'N/A')}")
        lines.append(f"- 主力净流入: {r.get('主力净流入', 'N/A')}")
        lines.append(f"- 小单净流入: {r.get('小单净流入', 'N/A')}")
        lines.append(f"- 中单净流入: {r.get('中单净流入', 'N/A')}")
        lines.append(f"- 大单净流入: {r.get('大单净流入', 'N/A')}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Shareholder count (股东户数) — v2.2
# ---------------------------------------------------------------------------


def get_shareholder_count(
    symbol: str,
    curr_date: str | None = None,
) -> str:
    """Fetch A-share shareholder-count (股东户数) via akshare.

    Uses the Eastmoney per-stock shareholder-count detail API.
    """
    code = to_akshare_symbol(symbol, "bare")

    with _akshare_task_context(f"👥 {symbol} 股东户数"), no_proxy():
        df = _safe_call(ak.stock_zh_a_gdhs_detail_em, symbol=code)

    df = _filter_df_by_date(df, "股东户数统计截止日", curr_date)

    if df is None or df.empty:
        raise NoMarketDataError(
            symbol,
            detail=f"no shareholder-count data for {symbol} via akshare",
        )

    lines = [
        f"## {symbol.upper()} Shareholder Count (股东户数) (source: akshare / Eastmoney)",
        f"Total records: {len(df)}",
        "",
    ]
    for _, r in df.iterrows():
        lines.append(f"**Date**: {r.get('股东户数统计截止日', 'N/A')}")
        lines.append(f"- 股东户数: {r.get('股东户数', 'N/A')}")
        lines.append(f"- 户均持股市值: {r.get('户均持股市值', 'N/A')}")
        lines.append(f"- 户均持股数量: {r.get('户均持股数量', 'N/A')}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Pledge ratio (股权质押) — real-time fallback, not batched
# ---------------------------------------------------------------------------


def get_pledge_ratio(symbol: str) -> str:
    """Fetch A-share pledge-ratio data via akshare (real-time)."""
    code = to_akshare_symbol(symbol, "bare")

    with _akshare_task_context(f"🔒 {symbol} 股权质押"), no_proxy():
        df = _safe_call(ak.stock_gpzy_individual_pledge_ratio_detail_em, symbol=code)

    if not isinstance(df, pd.DataFrame) or df.empty:
        raise NoMarketDataError(
            symbol, detail=f"no pledge-ratio data found for {symbol} via akshare"
        )

    lines = [
        f"## {symbol.upper()} Pledge Ratio (source: akshare / Eastmoney)",
        f"Total records: {len(df)}",
        "",
    ]
    for _, row in df.iterrows():
        lines.append(f"**Pledger**: {row.get('股东名称', 'N/A')}")
        lines.append(f"- 质押数量: {row.get('质押股份数量', 'N/A')}")
        lines.append(f"- 占所持比例: {row.get('占所持股份比例', 'N/A')}%")
        lines.append(f"- 占总股本比例: {row.get('占总股本比例', 'N/A')}%")
        lines.append(f"- 质押机构: {row.get('质押机构', 'N/A')}")
        lines.append(f"- 最新价: {row.get('最新价', 'N/A')}")
        lines.append(f"- 预估平仓线: {row.get('预估平仓线', 'N/A')}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Dividend history (分红送转) — real-time fallback, not batched
# ---------------------------------------------------------------------------


def get_dividend_history(symbol: str) -> str:
    """Fetch A-share dividend history via akshare (real-time)."""
    code = to_akshare_symbol(symbol, "bare")

    with _akshare_task_context(f"💰 {symbol} 分红送转"), no_proxy():
        df = _safe_call(ak.stock_fhps_detail_em, symbol=code)

    if df is None or df.empty:
        raise NoMarketDataError(
            symbol, detail=f"no dividend history found for {symbol} via akshare"
        )

    lines = [
        f"## {symbol.upper()} Dividend History (source: akshare / Eastmoney)",
        f"Total records: {len(df)}",
        "",
    ]
    for _, row in df.iterrows():
        lines.append(f"**Report Period**: {row.get('报告期', 'N/A')}")
        lines.append(f"- 分红方案: {row.get('分红方案', 'N/A')}")
        lines.append(f"- 除权除息日: {row.get('除权除息日', 'N/A')}")
        lines.append(f"- 股权登记日: {row.get('股权登记日', 'N/A')}")
        lines.append(f"- 红股上市日: {row.get('红股上市日', 'N/A')}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Research reports (个股研报) — real-time fallback, not batched
# ---------------------------------------------------------------------------


def get_research_reports(
    symbol: str,
    curr_date: str | None = None,
) -> str:
    """Fetch A-share research reports via akshare (real-time)."""
    code = to_akshare_symbol(symbol, "bare")

    with _akshare_task_context(f"📄 {symbol} 个股研报"), no_proxy():
        df = _safe_call(ak.stock_research_report_em, symbol=code)

    df = _filter_df_by_date(df, "日期", curr_date)

    if df is None or df.empty:
        raise NoMarketDataError(
            symbol, detail=f"no research reports found for {symbol} via akshare"
        )

    lines = [
        f"## {symbol.upper()} Research Reports (source: akshare / Eastmoney)",
        f"Total records: {len(df)}",
        "",
    ]
    for _, row in df.iterrows():
        lines.append(f"**{row.get('报告名称', 'N/A')}**")
        lines.append(f"- 机构: {row.get('机构', 'N/A')}")
        lines.append(f"- 东财评级: {row.get('东财评级', 'N/A')}")
        lines.append(f"- 日期: {row.get('日期', 'N/A')}")
        lines.append(f"- 行业: {row.get('行业', 'N/A')}")
        # optional earnings forecast columns
        for yr in ["2026", "2027", "2028"]:
            eps_key = f"{yr}-盈利预测-收益"
            pe_key = f"{yr}-盈利预测-市盈率"
            eps = row.get(eps_key)
            pe = row.get(pe_key)
            if eps is not None and not (isinstance(eps, float) and math.isnan(eps)):
                pe_str = f", PE: {pe:.1f}" if pe is not None and not (isinstance(pe, float) and math.isnan(pe)) else ""
                lines.append(f"  - {yr}E EPS: {eps:.2f}{pe_str}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Earnings estimates (analyst consensus)
# ---------------------------------------------------------------------------


_QUARTER_ENDS = ("0331", "0630", "0930", "1231")


def _recent_report_periods(curr_date: str | None, count: int = 4) -> list[str]:
    """Return the ``count`` most recent quarterly report periods (YYYYMMDD),
    newest first, starting from the one most likely to be published already.

    ``ak.stock_yjyg_em`` is keyed by reporting period, and any single period
    only carries preannouncements for the minority of firms that issued one, so
    callers walk back through several periods to find the latest available row.
    """
    period = _yjbb_report_date_for(curr_date)
    year, mmdd = int(period[:4]), period[4:]
    idx = _QUARTER_ENDS.index(mmdd)
    periods = [period]
    for _ in range(count - 1):
        idx -= 1
        if idx < 0:
            idx = len(_QUARTER_ENDS) - 1
            year -= 1
        periods.append(f"{year}{_QUARTER_ENDS[idx]}")
    return periods


def get_earnings_estimates(symbol: str, curr_date: str | None = None) -> str:
    """Fetch A-share company earnings preannouncement (业绩预告) via akshare.

    ``ak.stock_yjyg_em`` is period-based (one call returns every stock's
    preannouncement for a reporting period), so walk back through the most
    recent quarterly periods and return the first that carries a row for this
    ticker. Not every firm issues a preannouncement, so an absence here is a
    legitimate ``NoMarketDataError`` rather than a fetch failure.
    """
    code = to_akshare_symbol(symbol, "bare").zfill(6)

    df_hit = None
    period_hit = None
    with _akshare_task_context(f"📊 {symbol} 盈利预测"), no_proxy():
        for period in _recent_report_periods(curr_date):
            df = _safe_call(ak.stock_yjyg_em, date=period)
            if df is None or df.empty or "股票代码" not in df.columns:
                continue
            sub = df[df["股票代码"].astype(str).str.zfill(6) == code]
            if not sub.empty:
                df_hit, period_hit = sub, period
                break

    if df_hit is None:
        raise NoMarketDataError(
            symbol, detail=f"no earnings preannouncement found for {symbol} via akshare"
        )

    lines = [
        f"## {symbol.upper()} Earnings Preannouncement 业绩预告 "
        f"(period {period_hit}, source: akshare / Eastmoney)",
        f"Total records: {len(df_hit)}",
        "",
    ]
    for _, row in df_hit.iterrows():
        lines.append(f"**Forecast Type 预告类型**: {row.get('预告类型', 'N/A')}")
        lines.append(f"- Metric 预测指标: {row.get('预测指标', 'N/A')}")
        lines.append(f"- Change 业绩变动: {row.get('业绩变动', 'N/A')}")
        lines.append(f"- Forecast Value 预测数值: {row.get('预测数值', 'N/A')}")
        lines.append(f"- Change % 业绩变动幅度: {row.get('业绩变动幅度', 'N/A')}")
        lines.append(f"- Prior-Year Value 上年同期值: {row.get('上年同期值', 'N/A')}")
        lines.append(f"- Reason 业绩变动原因: {row.get('业绩变动原因', 'N/A')}")
        lines.append(f"- Announced 公告日期: {row.get('公告日期', 'N/A')}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Institutional holdings
# ---------------------------------------------------------------------------


def get_institutional_holdings(
    symbol: str,
    curr_date: str | None = None,
) -> str:
    """Fetch A-share top shareholders and institutional holdings via akshare."""
    code = to_akshare_symbol(symbol, "bare")

    with _akshare_task_context(f"🏛 {symbol} 机构持仓"), no_proxy():
        holder_df = _safe_call(ak.stock_main_stock_holder, symbol=code)

    lines = [
        f"## {symbol.upper()} Institutional Holdings (source: akshare / Eastmoney)",
        "",
    ]

    if holder_df is None or holder_df.empty:
        raise NoMarketDataError(
            symbol, detail=f"no institutional holdings data found for {symbol} via akshare"
        )

    lines.append(f"### Top Shareholders ({len(holder_df)} records)")
    for _, row in holder_df.iterrows():
        lines.append(f"- {row.get('股东名称', 'N/A')}: {row.get('持股数量', 'N/A')} shares ({row.get('持股比例', 'N/A')}%)")
    lines.append("")

    return "\n".join(lines)
