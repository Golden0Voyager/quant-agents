"""Akshare vendor — A-share financial data via Eastmoney/Xueqiu using akshare.

Each function mirrors the signature of its yfinance counterpart in y_finance.py
and returns a plain string suitable for direct LLM consumption. All A-share
monetary values pass through akshare_common.format_money_cn() so the unit
(亿/万) is always explicit in the output.
"""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from datetime import datetime
from typing import Annotated, Optional

import akshare as ak
import pandas as pd

from .akshare_common import (
    _akshare_retry,
    format_money_cn,
    is_a_share_ticker,
    no_proxy,
    safe_float,
    to_akshare_symbol,
)
from .stockstats_utils import _clean_dataframe

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Patch akshare's tqdm so every progress bar carries context and looks tidy.
# ---------------------------------------------------------------------------
_current_akshare_task: str | None = None
_original_get_tqdm = ak.utils.tqdm.get_tqdm


def _patched_get_tqdm(enable: bool = True):
    tqdm_cls = _original_get_tqdm(enable)
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
            super().__init__(iterable, desc=desc, *args, **kwargs)

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
        return (
            f"No data found for symbol '{symbol}' between "
            f"{start_date} and {end_date}"
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
    """Invoke an akshare function, returning None on any exception."""
    try:
        return func(*args, **kwargs)
    except Exception as exc:
        logger.debug("akshare call %s failed: %s", getattr(func, "__name__", "?"), exc)
        return None


def _yjbb_report_date_for(curr_date: Optional[str]) -> str:
    """Pick the most recently available yjbb report date as YYYYMMDD.

    Q1 results land in late April, Q2 in late August, Q3 in late October,
    so we lag by ~one month relative to the period-end.
    """
    if curr_date:
        y, m, _ = curr_date.split("-")
        y, m = int(y), int(m)
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
        f"# Source: akshare (Eastmoney)",
        "",
    ]

    if info_df is not None and not info_df.empty:
        info = dict(zip(info_df["item"], info_df["value"]))
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
        return f"No fundamentals available for {symbol} via akshare."
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
    curr_date: Optional[str] = None,
) -> str:
    """Fetch A-share balance sheet (latest report period) via akshare."""
    code = to_akshare_symbol(symbol, "upper_prefix")
    with _akshare_task_context(f"📊 {symbol} 资产负债表"), no_proxy():
        df = ak.stock_balance_sheet_by_report_em(symbol=code)

    if df is None or df.empty:
        return f"No balance sheet available for {symbol} via akshare."

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
    curr_date: Optional[str] = None,
) -> str:
    """Fetch A-share cash flow statement (latest report period) via akshare."""
    code = to_akshare_symbol(symbol, "upper_prefix")
    with _akshare_task_context(f"📊 {symbol} 现金流量表"), no_proxy():
        df = ak.stock_cash_flow_sheet_by_report_em(symbol=code)

    if df is None or df.empty:
        return f"No cash flow statement available for {symbol} via akshare."

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
    curr_date: Optional[str] = None,
) -> str:
    """Fetch A-share income statement (latest report period) via akshare."""
    code = to_akshare_symbol(symbol, "upper_prefix")
    with _akshare_task_context(f"📊 {symbol} 利润表"), no_proxy():
        df = ak.stock_profit_sheet_by_report_em(symbol=code)

    if df is None or df.empty:
        return f"No income statement available for {symbol} via akshare."

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
        return f"No K-line data for {symbol} to compute {indicator}."

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
        return f"No news found for {symbol} between {start_date} and {end_date} via akshare."

    # Filter by date
    df["发布时间"] = pd.to_datetime(df["发布时间"], errors="coerce")
    mask = (df["发布时间"] >= start_dt) & (df["发布时间"] <= end_dt + pd.Timedelta(days=1))
    df = df[mask]

    if df.empty:
        return f"No news found for {symbol} between {start_date} and {end_date} via akshare."

    lines = [
        f"## {symbol.upper()} News from {start_date} to {end_date} (source: akshare / Eastmoney)\n",
        f"Total articles: {len(df)}\n",
    ]
    for _, row in df.iterrows():
        lines.append(f"### {row['新闻标题']} (source: {row['文章来源']})")
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


def get_insider_transactions(
    symbol: Annotated[str, "A-share ticker e.g. 300454.SZ"],
) -> str:
    """Fetch A-share shareholder change data from 10jqka via akshare."""
    code = to_akshare_symbol(symbol, "bare")

    with _akshare_task_context(f"🏛 {symbol} 股东变动"), no_proxy():
        df = _safe_call(ak.stock_shareholder_change_ths, symbol=code)

    if df is None or df.empty:
        return f"No shareholder change data found for {symbol} via akshare."

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
        return (
            f"No company announcements found for {symbol} "
            f"between {start_date} and {end_date} via akshare."
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


def get_fund_flow(symbol: str) -> str:
    """Fetch A-share individual stock fund flow (主力/超大单/大单/中单/小单)."""
    code = to_akshare_symbol(symbol, "bare")
    prefix = to_akshare_symbol(symbol, "lower_prefix")[:2]  # "sh" or "sz"

    with _akshare_task_context(f"💰 {symbol} 资金流向"), no_proxy():
        df = _safe_call(ak.stock_individual_fund_flow, stock=code, market=prefix)

    if df is None or df.empty:
        return f"No fund flow data found for {symbol} via akshare."

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


def get_northbound_hold(symbol: str) -> str:
    """Fetch A-share northbound (Stock Connect) holding data."""
    code = to_akshare_symbol(symbol, "bare")
    prefix = to_akshare_symbol(symbol, "lower_prefix")[:2]

    with _akshare_task_context(f"🌏 {symbol} 北向资金"), no_proxy():
        df = _safe_call(ak.stock_hsgt_individual_em, stock=code, market=prefix)

    if df is None or df.empty:
        return f"No northbound holding data found for {symbol} via akshare."

    df = df.head(5)
    lines = [
        f"## {symbol.upper()} Northbound (Stock Connect) Holdings (source: akshare / Eastmoney)",
        f"Total records: {len(df)} trading days",
        "",
    ]
    for _, row in df.iterrows():
        lines.append(f"**Date**: {row.get('日期', 'N/A')}")
        lines.append(f"- Holding Shares: {row.get('持股数量', 'N/A')}")
        lines.append(f"- Holding Market Value: {row.get('持股市值', 'N/A')}")
        lines.append(f"- % of Tradable Shares: {row.get('占流通股比例', 'N/A')}%")
        lines.append(f"- Net Buy (shares): {row.get('当日成交净买额', 'N/A')}")
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
        return (
            f"No restricted share release data found for {symbol} "
            f"between {start_date} and {end_date} via akshare."
        )

    # Client-side filter by stock code
    if "股票代码" in df.columns:
        df = df[df["股票代码"].astype(str).str.strip() == code]

    if df.empty:
        return (
            f"No restricted share release events for {symbol} "
            f"between {start_date} and {end_date}."
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
        return f"No individual info data available for {symbol} via akshare."

    info = dict(zip(info_df["item"], info_df["value"]))
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
    indicator: Annotated[str, 'Macro indicator: "pmi", "cpi", "m2", "social_finance"'],
) -> str:
    """Fetch China macro quantitative indicators via akshare."""
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
            df = _safe_call(ak.macro_china_m2)
            title = "China M2 Money Supply"
            cols = None
        elif indicator in ("social_finance", "社融"):
            df = _safe_call(ak.macro_china_shrzgm)
            title = "China Aggregate Social Financing"
            cols = None
        else:
            return f"Unsupported macro indicator: {indicator}. Supported: pmi, cpi, m2, social_finance."

    if df is None or df.empty:
        return f"No macro data available for indicator '{indicator}' via akshare."

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


def get_margin_trading(symbol: str) -> str:
    """Fetch A-share margin-trading data via akshare."""
    raise RuntimeError("Margin trading via akshare not yet implemented")


# ---------------------------------------------------------------------------
# Dragon tiger (龙虎榜) — v2.2
# ---------------------------------------------------------------------------


def get_dragon_tiger(symbol: str) -> str:
    """Fetch A-share dragon-tiger-board data via akshare."""
    raise RuntimeError("Dragon tiger via akshare not yet implemented")


# ---------------------------------------------------------------------------
# Block trade (大宗交易) — v2.2
# ---------------------------------------------------------------------------


def get_block_trade(symbol: str) -> str:
    """Fetch A-share block-trade data via akshare."""
    raise RuntimeError("Block trade via akshare not yet implemented")


# ---------------------------------------------------------------------------
# Sector fund flow (板块资金流向) — v2.2
# ---------------------------------------------------------------------------


def get_sector_fund_flow(sector_name: str) -> str:
    """Fetch A-share sector fund-flow data via akshare."""
    raise RuntimeError("Sector fund flow via akshare not yet implemented")


# ---------------------------------------------------------------------------
# Shareholder count (股东户数) — v2.2
# ---------------------------------------------------------------------------


def get_shareholder_count(symbol: str) -> str:
    """Fetch A-share shareholder-count data via akshare."""
    raise RuntimeError("Shareholder count via akshare not yet implemented")


# ---------------------------------------------------------------------------
# Pledge ratio (股权质押) — real-time fallback, not batched
# ---------------------------------------------------------------------------


def get_pledge_ratio(symbol: str) -> str:
    """Fetch A-share pledge-ratio data via akshare (real-time)."""
    code = to_akshare_symbol(symbol, "bare")

    with _akshare_task_context(f"🔒 {symbol} 股权质押"), no_proxy():
        df = _safe_call(ak.stock_gpzy_individual_pledge_ratio_detail_em, symbol=code)

    if df is None or df.empty:
        return f"No pledge-ratio data found for {symbol} via akshare."

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
        return f"No dividend history found for {symbol} via akshare."

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


def get_research_reports(symbol: str) -> str:
    """Fetch A-share research reports via akshare (real-time)."""
    code = to_akshare_symbol(symbol, "bare")

    with _akshare_task_context(f"📄 {symbol} 个股研报"), no_proxy():
        df = _safe_call(ak.stock_research_report_em, symbol=code)

    if df is None or df.empty:
        return f"No research reports found for {symbol} via akshare."

    lines = [
        f"## {symbol.upper()} Research Reports (source: akshare / Eastmoney)",
        f"Total records: {len(df)}",
        "",
    ]
    for _, row in df.iterrows():
        lines.append(f"**Title**: {row.get('报告标题', 'N/A')}")
        lines.append(f"- 机构: {row.get('机构名称', 'N/A')}")
        lines.append(f"- 分析师: {row.get('分析师', 'N/A')}")
        lines.append(f"- 评级: {row.get('评级', 'N/A')}")
        lines.append(f"- 目标价: {row.get('目标价', 'N/A')}")
        lines.append(f"- 发布日期: {row.get('发布日期', 'N/A')}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Earnings estimates (analyst consensus)
# ---------------------------------------------------------------------------


def get_earnings_estimates(symbol: str) -> str:
    """Fetch A-share analyst earnings estimate consensus via akshare."""
    code = to_akshare_symbol(symbol, "bare")

    with _akshare_task_context(f"📊 {symbol} 盈利预测"), no_proxy():
        df = _safe_call(ak.stock_yjyg_em, symbol=code)

    if df is None or df.empty:
        return f"No earnings estimate data found for {symbol} via akshare."

    lines = [
        f"## {symbol.upper()} Analyst Earnings Estimates (source: akshare / Eastmoney)",
        f"Total records: {len(df)}",
        "",
    ]
    for _, row in df.iterrows():
        lines.append(f"**Report Period**: {row.get('报告期', 'N/A')}")
        lines.append(f"- Forecast Type: {row.get('预告类型', 'N/A')}")
        lines.append(f"- Forecast Content: {row.get('预告内容', 'N/A')}")
        lines.append(f"- Forecast Reason: {row.get('预告原因', 'N/A')}")
        lines.append(f"- Change Lower Limit: {row.get('变动下限', 'N/A')}")
        lines.append(f"- Change Upper Limit: {row.get('变动上限', 'N/A')}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Institutional holdings
# ---------------------------------------------------------------------------


def get_institutional_holdings(symbol: str) -> str:
    """Fetch A-share top shareholders and institutional holdings via akshare."""
    code = to_akshare_symbol(symbol, "bare")

    with _akshare_task_context(f"🏛 {symbol} 机构持仓"), no_proxy():
        holder_df = _safe_call(ak.stock_main_stock_holder, symbol=code)

    lines = [
        f"## {symbol.upper()} Institutional Holdings (source: akshare / Eastmoney)",
        "",
    ]

    if holder_df is not None and not holder_df.empty:
        lines.append(f"### Top Shareholders ({len(holder_df)} records)")
        for _, row in holder_df.iterrows():
            lines.append(f"- {row.get('股东名称', 'N/A')}: {row.get('持股数量', 'N/A')} shares ({row.get('持股比例', 'N/A')}%)")
        lines.append("")
    else:
        lines.append("No top shareholder data available.")

    return "\n".join(lines)
