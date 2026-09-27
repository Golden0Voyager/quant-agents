from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from tradingagents.dataflows.akshare_common import is_a_share_ticker
from tradingagents.dataflows.errors import NoMarketDataError
from tradingagents.dataflows.freshness import nearest_prior_session
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
    # When the requested trade_date is not a trading session (weekend/holiday
    # run), the anchor snaps back to the nearest prior session;
    # anchor_aligned_from records the originally requested date and
    # anchor_date the effective one, so the UI can say what happened.
    anchor_date: str | None = None
    anchor_aligned_from: str | None = None


_MAJOR_INDEX_CODES = frozenset({
    "000001",
    "399001",
    "399006",
    "000688",
})


# 分析师到所需数据源的映射
ANALYST_DATA_REQUIREMENTS: dict[str, list[dict]] = {
    "market": [
        {"key": "ohlcv",             "label": "日K行情",     "cache": True},
        {"key": "indicators",        "label": "技术指标",    "cache": True, "derived": "ohlcv"},
        {"key": "chip_distribution", "label": "筹码分布",    "cache": True},
        {"key": "fund_flow",         "label": "资金流向",    "cache": True},
        {"key": "limit_up_down",     "label": "涨跌停统计",  "cache": True},
        {"key": "index_daily",       "label": "指数日线",    "cache": True},
    ],
    "social": [
        {"key": "stocktwits",        "label": "StockTwits",  "cache": False},
        {"key": "reddit",            "label": "Reddit",      "cache": False},
    ],
    "news": [
        {"key": "news_akshare",      "label": "新闻 (A股)",  "cache": False},
    ],
    "fundamentals": [
        {"key": "company_info",      "label": "公司信息",    "cache": False},
        {"key": "fin_statements",    "label": "财务报表",    "cache": True},
        {"key": "historical_val",    "label": "历史估值分位","cache": True},
        {"key": "earnings_forecast", "label": "业绩预告",    "cache": True},
    ],
    "governance": [
        {"key": "dragon_tiger",      "label": "龙虎榜",      "cache": True},
        {"key": "margin_trading",    "label": "融资融券",    "cache": True},
        {"key": "shareholders",      "label": "股东户数",    "cache": True},
        {"key": "pledge",            "label": "股权质押",    "cache": False},
        {"key": "northbound",        "label": "北向资金",    "cache": True},
        {"key": "inst_intel",        "label": "机构综合情报","cache": True},
    ],
    "industry": [
        {"key": "industry_val",      "label": "行业估值",    "cache": False},
        {"key": "concept_board",     "label": "概念题材",    "cache": True},
        {"key": "macro",             "label": "宏观数据",    "cache": False},
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
    date_col: str = "trade_date",
    trade_date: str | None = None,
    stale_sessions: int | None = None,
    whole_table: bool = False,
) -> ReadinessItem | None:
    """检查 smartmoney_db 中某张表是否有该标的的数据。

    当给出 ``stale_sessions`` 且表内最新日期距 ``trade_date`` 缺失的
    交易日数达到该阈值时，状态标为 "stale"（面板亮黄灯）——提示
    pipeline 回补可能已停摆。按交易日而非日历日度量：周末/长假期间
    滞后不会增长，不会误报。``whole_table`` 用于龙虎榜这类稀疏表：
    按整表最大日期判断 pipeline 健康度，而不是按单个标的的出现记录。
    """
    if not is_a_share_ticker(ticker):
        return None
    try:
        from tradingagents.dataflows.freshness import trading_sessions_between
        from tradingagents.dataflows.smartmoney_vendor import (
            _df_from_sql,
            _to_smartmoney_symbol,
        )
        code = _to_smartmoney_symbol(ticker)
        where = "" if whole_table else " WHERE ts_code = ?"
        params = () if whole_table else (code,)
        df = _df_from_sql(
            f"SELECT COUNT(*) as cnt, MAX({date_col}) as latest "
            f"FROM {table}{where}",
            params,
        )
        if df is not None and not df.empty:
            cnt = df.iloc[0]["cnt"]
            latest = df.iloc[0]["latest"]
            if cnt and cnt > 0:
                latest_str = str(latest) if latest else "N/A"
                details = (
                    f"{cnt} 条记录"
                    + (f"，最新 {latest_str}" if latest_str != "N/A" else "")
                )
                if stale_sessions and latest and trade_date:
                    lag = trading_sessions_between(str(latest), str(trade_date)[:10])
                    if lag is not None and lag >= stale_sessions:
                        return ReadinessItem(
                            label, "cacheable", "stale",
                            details + f" ⚠️ 滞后 {lag} 个交易日"
                            f"（超 {stale_sessions} 个交易日阈值，"
                            f"分析时将走线上刷新）",
                            analyst,
                        )
                return ReadinessItem(
                    label, "cacheable", "cached",
                    details,
                    analyst,
                )
    except Exception as exc:
        logger.debug("smartmoney_db check failed for %s.%s: %s", ticker, table, exc)
    return None


def _check_fund_flow(ticker: str, analyst: str, trade_date: str | None = None) -> ReadinessItem:
    """检查资金流向缓存（当天傍晚发布，缺锚定日当天即滞后）。"""
    result = _check_smartmoney_table(
        ticker, "fund_flow", "资金流向", analyst,
        trade_date=trade_date, stale_sessions=1,
    )
    if result:
        return result
    return ReadinessItem(
        "资金流向", "realtime", "available",
        "无缓存，分析时实时获取", analyst
    )


def _check_margin_trading(ticker: str, analyst: str, trade_date: str | None = None) -> ReadinessItem:
    """检查融资融券缓存（官方 T+1 早上披露，锚定日最新到 T-1 属正常）。"""
    result = _check_smartmoney_table(
        ticker, "margin_trading", "融资融券", analyst,
        trade_date=trade_date, stale_sessions=2,
    )
    if result:
        return result
    return ReadinessItem(
        "融资融券", "realtime", "available",
        "无缓存，分析时实时获取", analyst
    )


def _check_dragon_tiger(ticker: str, analyst: str, trade_date: str | None = None) -> ReadinessItem:
    """检查龙虎榜缓存（稀疏表按整表最大日期判断，与线上守卫一致；
    当天傍晚发布，锚定日最新到 T-1 属正常）。"""
    result = _check_smartmoney_table(
        ticker, "dragon_tiger", "龙虎榜", analyst,
        trade_date=trade_date, stale_sessions=2,
        whole_table=True,
    )
    if result:
        return result
    return ReadinessItem(
        "龙虎榜", "realtime", "available",
        "无缓存，分析时实时获取", analyst
    )


def _check_shareholders(ticker: str, analyst: str, trade_date: str | None = None) -> ReadinessItem:
    """检查股东户数缓存（季度披露，阈值放宽到 65 个交易日）。"""
    result = _check_smartmoney_table(
        ticker, "shareholder_count", "股东户数", analyst,
        trade_date=trade_date, stale_sessions=65,
    )
    if result:
        return result
    return ReadinessItem(
        "股东户数", "realtime", "available",
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
        placeholders = ",".join("?" * len(_MAJOR_INDEX_CODES))
        df = _df_from_sql(
            f"SELECT COUNT(*) as cnt, MAX(trade_date) as latest "
            f"FROM index_daily WHERE ts_code IN ({placeholders}) AND trade_date <= ?",
            tuple(_MAJOR_INDEX_CODES) + (trade_date,),
        )
        if df is not None and not df.empty:
            cnt = df.iloc[0]["cnt"]
            latest = df.iloc[0]["latest"]
            if cnt and cnt > 0:
                latest_str = str(latest) if latest else ""
                return ReadinessItem(
                    "指数日线", "cacheable", "cached",
                    f"{int(cnt)} 条记录" + (f"，最新 {latest_str}" if latest_str else ""),
                    analyst,
                )
    except Exception as exc:
        logger.debug("smartmoney_db check failed for index_daily: %s", exc)
    return ReadinessItem(
        "指数日线", "realtime", "available",
        "无缓存，分析时实时获取", analyst
    )


def _check_fin_statements(ticker: str, analyst: str, trade_date: str | None = None) -> ReadinessItem:
    """检查财务报表缓存（季度披露节奏：对照披露截止日+宽限算出的应披露报告期）。"""
    result = _check_smartmoney_table(
        ticker, "quarterly_financials", "财务报表", analyst, date_col="report_period"
    )
    if result:
        if trade_date:
            try:
                from tradingagents.dataflows.freshness import expected_report_period
                expected = expected_report_period(trade_date)
                if expected:
                    import re

                    m = re.search(r"(\d{4}-\d{2}-\d{2})", result.details)
                    if m and m.group(1) < expected:
                        result.status = "stale"
                        result.details += (
                            f" ⚠️ 最新报告期 {m.group(1)} 落后于应披露期 {expected}"
                            f"（分析时将走线上刷新）"
                        )
            except Exception as exc:
                logger.debug("quarterly expected-period check failed: %s", exc)
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
    # 周末/节假日运行时把锚点对齐到最近交易日：按日查询的表（涨跌停等）
    # 在非交易日永远查空，对齐后检查结果才有意义；交易日滞后本来就不增长，
    # 对齐不改变新鲜度判定结果。日历不可用时保持原锚点（永不阻断）。
    session = nearest_prior_session(trade_date)
    if session and session != str(trade_date)[:10]:
        report.anchor_aligned_from = str(trade_date)[:10]
        trade_date = session
    report.anchor_date = trade_date

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
                item = _check_fund_flow(ticker, analyst_key, trade_date)
            elif req["key"] == "fin_statements":
                item = _check_fin_statements(ticker, analyst_key, trade_date)
            elif req["key"] == "northbound":
                item = _check_northbound(ticker, analyst_key)
            elif req["key"] == "margin_trading":
                item = _check_margin_trading(ticker, analyst_key, trade_date)
            elif req["key"] == "dragon_tiger":
                item = _check_dragon_tiger(ticker, analyst_key, trade_date)
            elif req["key"] == "shareholders":
                item = _check_shareholders(ticker, analyst_key, trade_date)
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
            elif item.status == "stale":
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
            "unavailable": "❌", "skipped": "⏭️", "stale": "⚠️",
        }.get(item.status, "❓")
        table = cacheable if item.category == "cacheable" else realtime
        style = {
            "unavailable": "red", "stale": "yellow",
        }.get(item.status, "green")
        table.add_row(
            status_emoji,
            f"[{style}]{item.label}[/{style}]",
            f"[dim]{item.details}[/dim]"
        )

    console.print()
    if report.anchor_aligned_from and report.anchor_date:
        console.print(
            f"[cyan]📅 {report.anchor_aligned_from} 非交易日，"
            f"分析锚点已对齐至最近交易日 {report.anchor_date}[/cyan]"
        )
    console.print(Panel.fit(
        "[bold]数据就绪检查[/bold]",
        border_style="cyan",
    ))
    if cacheable.row_count > 0:
        console.print(cacheable)
    if realtime.row_count > 0:
        console.print(realtime)
    stale_count = sum(1 for i in report.items if i.status == "stale")
    if not report.all_ready:
        console.print(f"\n[red]⚠ {report.warning_count} 项数据不可用，分析可能受限[/red]")
    elif stale_count > 0:
        console.print(
            f"\n[yellow]⚠ {stale_count} 项缓存数据超龄，分析时将自动走线上刷新"
            f"（pipeline 回补可能停摆，建议检查 quant_pipeline 运行记录）[/yellow]"
        )
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
