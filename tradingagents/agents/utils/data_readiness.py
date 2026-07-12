from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from tradingagents.dataflows.akshare_common import is_a_share_ticker
from tradingagents.dataflows.errors import NoMarketDataError
from tradingagents.dataflows.stockstats_utils import load_ohlcv
from tradingagents.ticker_resolver import resolve_ticker

logger = logging.getLogger(__name__)


@dataclass
class ReadinessItem:
    label: str          # "日K行情", "资金流向", "新闻API" ...
    category: str       # "cacheable" | "realtime"
    status: str         # "cached" | "preloaded" | "available" | "unavailable" | "skipped"
    details: str        # "最新 2026-07-01" / "接口联通" / "无该标的记录"
    analyst: str        # 关联分析师 key


@dataclass
class ReadinessReport:
    items: list[ReadinessItem] = field(default_factory=list)
    all_ready: bool = True
    warning_count: int = 0


_MAJOR_INDEX_CODES = {
    "000001": "上证指数",
    "399001": "深证成指",
    "399006": "创业板指",
    "000688": "科创50",
}


# 分析师到所需数据源的映射
ANALYST_DATA_REQUIREMENTS: dict[str, list[dict]] = {
    "market": [
        {"key": "ohlcv",         "label": "日K行情",     "cache": True},
        {"key": "indicators",    "label": "技术指标",    "cache": True, "derived": "ohlcv"},
        {"key": "fund_flow",     "label": "资金流向",    "cache": True},
        {"key": "limit_up_down", "label": "涨跌停统计",  "cache": True},
        {"key": "index_daily",   "label": "指数日线",    "cache": True},
    ],
    "social": [
        {"key": "stocktwits",    "label": "StockTwits",  "cache": False},
        {"key": "reddit",        "label": "Reddit",      "cache": False},
    ],
    "news": [
        {"key": "news_akshare",  "label": "新闻 (A股)",  "cache": False},
    ],
    "fundamentals": [
        {"key": "company_info",  "label": "公司信息",    "cache": False},
        {"key": "fin_statements","label": "财务报表",    "cache": True},
    ],
    "governance": [
        {"key": "dragon_tiger",  "label": "龙虎榜",      "cache": False},
        {"key": "margin_trading","label": "融资融券",    "cache": False},
        {"key": "shareholders",  "label": "股东户数",    "cache": False},
        {"key": "pledge",        "label": "股权质押",    "cache": False},
        {"key": "northbound",    "label": "北向资金",    "cache": True},
    ],
    "industry": [
        {"key": "industry_val",  "label": "行业估值",    "cache": False},
        {"key": "macro",         "label": "宏观数据",    "cache": False},
    ],
}


def _check_ohlcv(ticker: str, trade_date: str, config: dict) -> ReadinessItem:
    """检查 OHLCV 行情缓存。load_ohlcv 会自动下载+缓存。"""
    try:
        data = load_ohlcv(ticker, trade_date)
        if data is None or data.empty:
            return ReadinessItem(
                "日K行情", "cacheable", "unavailable",
                "无可用数据", "market"
            )
        pd_date = pd.to_datetime(data["Date"]).max()
        latest = pd_date.strftime("%Y-%m-%d")
        days_old = (pd.to_datetime(trade_date) - pd_date).days
        status = "cached" if days_old <= 1 else "preloaded"
        return ReadinessItem(
            "日K行情", "cacheable", status,
            f"最新 {latest}" + (f" ({days_old}日前)" if days_old > 1 else ""),
            "market"
        )
    except (NoMarketDataError, Exception) as exc:
        return ReadinessItem(
            "日K行情", "cacheable", "unavailable",
            f"加载失败: {type(exc).__name__}", "market"
        )


def _check_smartmoney_table(
    ticker: str, table: str, label: str, analyst: str,
    date_col: str = "trade_date"
) -> ReadinessItem | None:
    """检查 smartmoney_db 中某张表是否有该标的的数据。"""
    if not is_a_share_ticker(ticker):
        return None
    try:
        from tradingagents.dataflows.smartmoney_vendor import (
            _df_from_sql,
            _to_smartmoney_symbol,
        )
        code = _to_smartmoney_symbol(ticker)
        df = _df_from_sql(
            f"SELECT COUNT(*) as cnt, MAX({date_col}) as latest "
            f"FROM {table} WHERE ts_code = ?",
            (code,),
        )
        if df is not None and not df.empty:
            cnt = df.iloc[0]["cnt"]
            latest = df.iloc[0]["latest"]
            if cnt and cnt > 0:
                latest_str = str(latest) if latest else "N/A"
                return ReadinessItem(
                    label, "cacheable", "cached",
                    f"{cnt} 条记录" + (f"，最新 {latest_str}" if latest_str != "N/A" else ""),
                    analyst,
                )
    except Exception as exc:
        logger.debug("smartmoney_db check failed for %s.%s: %s", ticker, table, exc)
    return None


def _check_fund_flow(ticker: str, analyst: str) -> ReadinessItem:
    """检查资金流向缓存。"""
    result = _check_smartmoney_table(ticker, "fund_flow", "资金流向", analyst)
    if result:
        return result
    return ReadinessItem(
        "资金流向", "realtime", "available",
        "无缓存，分析时实时获取", analyst
    )


def _check_northbound(ticker: str, analyst: str) -> ReadinessItem:
    """检查北向资金缓存。"""
    result = _check_smartmoney_table(ticker, "north_flow", "北向资金", analyst)
    if result:
        return result
    return ReadinessItem(
        "北向资金", "realtime", "available",
        "无缓存，分析时实时获取", analyst
    )


def _check_limit_up_down(
    ticker: str, trade_date: str, analyst: str
) -> ReadinessItem:
    """检查涨跌停统计缓存（按日期，不按标的）。"""
    if not is_a_share_ticker(ticker):
        return ReadinessItem(
            "涨跌停统计", "realtime", "available",
            "非A股标的，分析时获取", analyst
        )
    try:
        from tradingagents.dataflows.smartmoney_vendor import _df_from_sql
        df = _df_from_sql(
            "SELECT COUNT(*) as cnt FROM limit_up_down WHERE trade_date = ?",
            (trade_date,),
        )
        if df is not None and not df.empty:
            cnt = df.iloc[0]["cnt"]
            if cnt and cnt > 0:
                return ReadinessItem(
                    "涨跌停统计", "cacheable", "cached",
                    f"{cnt} 条记录", analyst,
                )
    except Exception as exc:
        logger.debug("smartmoney_db check failed for limit_up_down: %s", exc)
    return ReadinessItem(
        "涨跌停统计", "realtime", "available",
        "无缓存，分析时实时获取", analyst
    )


def _check_index_daily(
    ticker: str, trade_date: str, analyst: str
) -> ReadinessItem:
    """检查大盘/板块指数日线缓存（按主要指数代码，不按标的）。"""
    if not is_a_share_ticker(ticker):
        return ReadinessItem(
            "指数日线", "realtime", "available",
            "非A股标的，分析时获取", analyst
        )
    try:
        from tradingagents.dataflows.smartmoney_vendor import _df_from_sql
        total = 0
        latest = None
        for code in _MAJOR_INDEX_CODES:
            df = _df_from_sql(
                "SELECT COUNT(*) as cnt, MAX(trade_date) as latest "
                "FROM index_daily WHERE ts_code = ? AND trade_date <= ?",
                (code, trade_date),
            )
            if df is not None and not df.empty:
                cnt = df.iloc[0]["cnt"]
                if cnt and cnt > 0:
                    total += int(cnt)
                    row_latest = df.iloc[0]["latest"]
                    if row_latest and (latest is None or str(row_latest) > str(latest)):
                        latest = row_latest
        if total > 0:
            return ReadinessItem(
                "指数日线", "cacheable", "cached",
                f"{total} 条记录" + (f"，最新 {latest}" if latest else ""),
                analyst,
            )
    except Exception as exc:
        logger.debug("smartmoney_db check failed for index_daily: %s", exc)
    return ReadinessItem(
        "指数日线", "realtime", "available",
        "无缓存，分析时实时获取", analyst
    )


def _check_fin_statements(ticker: str, analyst: str) -> ReadinessItem:
    """检查财务报表缓存。"""
    result = _check_smartmoney_table(
        ticker, "quarterly_financials", "财务报表", analyst, date_col="report_period"
    )
    if result:
        return result
    return ReadinessItem(
        "财务报表", "realtime", "available",
        "无缓存，分析时实时获取", analyst
    )


def _check_derived(derived_from: str, label: str, analyst: str) -> ReadinessItem:
    """检查衍生数据（如技术指标从行情计算）。"""
    return ReadinessItem(
        label, "cacheable", "available",
        f"由 {derived_from} 即时计算", analyst
    )


def check_data_readiness(
    ticker: str,
    trade_date: str,
    selected_analysts: list[str],
    config: dict | None = None,
) -> ReadinessReport:
    """运行数据就绪检查，预加载可缓存数据，返回报告。"""
    if config is None:
        from tradingagents.default_config import DEFAULT_CONFIG
        config = DEFAULT_CONFIG.copy()

    report = ReadinessReport()
    checked_keys: set[str] = set()
    _ohlcv_result: ReadinessItem | None = None

    for analyst_key in selected_analysts:
        requirements = ANALYST_DATA_REQUIREMENTS.get(analyst_key, [])
        for req in requirements:
            if req["key"] in checked_keys:
                continue
            checked_keys.add(req["key"])

            if req["key"] == "ohlcv":
                item = _check_ohlcv(ticker, trade_date, config or {})
                _ohlcv_result = item
            elif req["cache"] and req.get("derived") == "ohlcv":
                if _ohlcv_result and _ohlcv_result.status == "unavailable":
                    item = ReadinessItem(
                        req["label"], "cacheable", "unavailable",
                        "基础行情不可用，无法计算", analyst_key
                    )
                else:
                    item = _check_derived(req["derived"], req["label"], analyst_key)
            elif req["key"] == "fund_flow":
                item = _check_fund_flow(ticker, analyst_key)
            elif req["key"] == "fin_statements":
                item = _check_fin_statements(ticker, analyst_key)
            elif req["key"] == "northbound":
                item = _check_northbound(ticker, analyst_key)
            elif req["key"] == "limit_up_down":
                item = _check_limit_up_down(ticker, trade_date, analyst_key)
            elif req["key"] == "index_daily":
                item = _check_index_daily(ticker, trade_date, analyst_key)
            else:
                # 实时数据 — 标记为"分析时获取"
                item = ReadinessItem(
                    req["label"], "realtime", "available",
                    "分析时实时获取", analyst_key
                )
            report.items.append(item)
            if item.status == "unavailable":
                report.all_ready = False
                report.warning_count += 1

    return report


def display_readiness_report(console: Console, report: ReadinessReport) -> None:
    """用 Rich 表格渲染数据就绪检查结果。"""
    cacheable = Table(title="📦 可缓存数据", box=None, show_header=True)
    cacheable.add_column("状态", width=4)
    cacheable.add_column("数据项", width=16)
    cacheable.add_column("详情", width=40)

    realtime = Table(title="📡 实时数据（分析时获取）", box=None, show_header=True)
    realtime.add_column("状态", width=4)
    realtime.add_column("数据项", width=16)
    realtime.add_column("详情", width=40)

    for item in report.items:
        status_emoji = {
            "cached": "✅", "preloaded": "✅", "available": "✅",
            "unavailable": "❌", "skipped": "⏭️",
        }.get(item.status, "❓")
        table = cacheable if item.category == "cacheable" else realtime
        style = "red" if item.status == "unavailable" else "green"
        table.add_row(
            status_emoji,
            f"[{style}]{item.label}[/{style}]",
            f"[dim]{item.details}[/dim]"
        )

    console.print()
    console.print(Panel.fit(
        "[bold]数据就绪检查[/bold]",
        border_style="cyan",
    ))
    if cacheable.row_count > 0:
        console.print(cacheable)
    if realtime.row_count > 0:
        console.print(realtime)
    if report.warning_count > 0:
        console.print(f"\n[red]⚠ {report.warning_count} 项数据不可用，分析可能受限[/red]")
    else:
        console.print("\n[green]✅ 所有数据就绪[/green]")
    console.print()


def check_batch_readiness(
    tickers: list[str],
    trade_date: str,
    selected_analysts: list[str],
) -> tuple[int, int]:
    """Pre-load cacheable data for all tickers. Returns (ready_count, total_count).

    For batch mode: runs silently, triggers OHLCV cache pre-loading.
    Only OHLCV availability is critical — everything else is fetched real-time.
    """
    ready = 0
    for ticker in tickers:
        try:
            try:
                resolved = resolve_ticker(ticker)
                resolved_ticker = resolved["ticker"]
            except Exception:
                resolved_ticker = ticker
            report = check_data_readiness(resolved_ticker, trade_date, selected_analysts)
            ohlcv_items = [i for i in report.items if i.label == "日K行情"]
            ohlcv_ok = ohlcv_items and ohlcv_items[0].status != "unavailable"
            if ohlcv_ok:
                ready += 1
        except Exception:
            logger.debug("Batch readiness check failed for %s", ticker, exc_info=True)
    return ready, len(tickers)
