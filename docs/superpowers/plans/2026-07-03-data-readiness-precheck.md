# Data Readiness Pre-check 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 CLI 交互流程中增加数据就绪预检查环节，在用户选定分析师后展示数据缓存/可用性状态，自动预加载可缓存数据，避免分析时因数据缺失而产生的等待和失败。

**Architecture:** 新建 `tradingagents/agents/utils/data_readiness.py` 模块，定义 `check_data_readiness()` 函数，根据选定的分析师列表映射到所需数据源，检查缓存状态并预加载。在 `cli/main.py` 的 `get_user_selections()` 中插入 Step 3.5，将分析师选择从 Step 4 提前至 Step 3。展示结果使用 Rich Panel + Table。

**Tech Stack:** Python, Rich (console/Table/Panel), pandas, sqlite3 (smartmoney_db)

## Global Constraints

- 所有数据源检查必须是非 LLM 的、确定性的、快速完成的（< 5 秒总时间）
- 不修改现有 vendor 函数签名和返回格式
- 缓存检查使用现有的 `load_ohlcv()` 和 smartmoney_db 查询
- 实时数据只做连通性检查，不做预拉取
- 展示使用中文，颜色标记：✅ 绿色（可用）、⚠️ 黄色（警告）、❌ 红色（不可用）、🔄 黄色（正在预加载）
- 遵循现有目录结构，不引入新依赖

---

### Task 1: 创建 data_readiness.py 模块

**Files:**
- Create: `tradingagents/agents/utils/data_readiness.py`
- Test: `tests/test_data_readiness.py`

**Interfaces:**
- Produces: `check_data_readiness(ticker, trade_date, selected_analysts, config) -> ReadinessReport`
- Produces: `display_readiness_report(console, report) -> None`
- Produces: `ReadinessItem`, `ReadinessReport` dataclasses
- Produces: `ANALYST_DATA_REQUIREMENTS` dict

- [ ] **Step 1: 创建文件并定义数据模型和映射表**

创建 `tradingagents/agents/utils/data_readiness.py`：

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text


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


# 分析师到所需数据源的映射
ANALYST_DATA_REQUIREMENTS: dict[str, list[dict]] = {
    "market": [
        {"key": "ohlcv",         "label": "日K行情",     "cache": True},
        {"key": "indicators",    "label": "技术指标",    "cache": True, "derived": "ohlcv"},
        {"key": "fund_flow",     "label": "资金流向",    "cache": True},
    ],
    "sentiment": [
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
        {"key": "northbound",    "label": "北向资金",    "cache": False},
    ],
    "industry": [
        {"key": "industry_val",  "label": "行业估值",    "cache": False},
        {"key": "macro",         "label": "宏观数据",    "cache": False},
    ],
}
```

- [ ] **Step 2: 实现缓存检查函数**

在同一个文件中添加：

```python
import logging
import os

import pandas as pd

from tradingagents.dataflows.stockstats_utils import load_ohlcv
from tradingagents.dataflows.errors import NoMarketDataError
from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.akshare_common import is_a_share_ticker

logger = logging.getLogger(__name__)


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
        latest = pd_date.strftime("%Y-%m-%d") if pd_date is not None else "N/A"
        days_old = (pd.to_datetime(trade_date) - pd_date).days if pd_date is not None else 0
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
    ticker: str, table: str, label: str, analyst: str
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
            f"SELECT COUNT(*) as cnt, MAX(trade_date) as latest "
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


def _check_fin_statements(ticker: str, analyst: str) -> ReadinessItem:
    """检查财务报表缓存。"""
    result = _check_smartmoney_table(ticker, "financial_statements", "财务报表", analyst)
    if result:
        return result
    return ReadinessItem(
        "财务报表", "realtime", "available",
        "无缓存，分析时实时获取", analyst
    )


_DERIVED_LABELS = {
    "indicators": "技术指标",
}


def _check_derived(derived_from: str, label: str, analyst: str) -> ReadinessItem:
    """检查衍生数据（如技术指标从行情计算）。"""
    return ReadinessItem(
        label, "cacheable", "available",
        f"由 {derived_from} 即时计算", analyst
    )
```

- [ ] **Step 3: 实现主检查函数和展示函数**

在同一个文件中添加：

```python
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

    for analyst_key in selected_analysts:
        requirements = ANALYST_DATA_REQUIREMENTS.get(analyst_key, [])
        for req in requirements:
            if req["key"] in checked_keys:
                continue
            checked_keys.add(req["key"])

            if req["cache"] and req.get("derived"):
                # 衍生数据（如技术指标从行情计算）
                item = _check_derived(req["derived"], req["label"], analyst_key)
            elif req["key"] == "ohlcv":
                item = _check_ohlcv(ticker, trade_date, config or {})
            elif req["key"] == "fund_flow":
                item = _check_fund_flow(ticker, analyst_key)
            elif req["key"] == "fin_statements":
                item = _check_fin_statements(ticker, analyst_key)
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
        console.print(f"\n[green]✅ 所有数据就绪[/green]")
    console.print()
```

- [ ] **Step 4: 写测试**

创建 `tests/test_data_readiness.py`：

```python
"""Tests for the data readiness pre-check module."""

from unittest.mock import patch, MagicMock

import pytest
import pandas as pd

from tradingagents.agents.utils.data_readiness import (
    check_data_readiness,
    ReadinessReport,
    ReadinessItem,
    ANALYST_DATA_REQUIREMENTS,
)


def test_analyst_mapping_has_all_analysts():
    """所有已知的 analyst key 都有对应的数据需求映射。"""
    expected_keys = {"market", "sentiment", "news", "fundamentals", "governance", "industry"}
    assert expected_keys.issubset(ANALYST_DATA_REQUIREMENTS.keys())


def test_no_duplicate_keys_across_analysts():
    """不同 analyst 映射的数据 key 不应重复（已去重）。"""
    seen = {}
    for analyst, reqs in ANALYST_DATA_REQUIREMENTS.items():
        for req in reqs:
            if req["key"] in seen:
                # 允许重复，但只在被 check_data_readiness 去重后能不重复
                pass
            seen[req["key"]] = analyst


@patch("tradingagents.agents.utils.data_readiness.load_ohlcv")
def test_check_market_analyst_ohlcv_cached(mock_load_ohlcv):
    """市场分析师的 OHLCV 检查应返回 cached 状态。"""
    mock_df = pd.DataFrame({
        "Date": ["2026-07-01", "2026-07-02", "2026-07-03"],
        "Close": [10.0, 10.5, 11.0],
    })
    mock_load_ohlcv.return_value = mock_df

    report = check_data_readiness("000001.SZ", "2026-07-03", ["market"])
    assert report.all_ready is True
    ohlcv_item = [i for i in report.items if i.label == "日K行情"][0]
    assert ohlcv_item.status == "cached"
    assert "2026-07-03" in ohlcv_item.details


@patch("tradingagents.agents.utils.data_readiness.load_ohlcv")
def test_check_market_analyst_ohlcv_stale(mock_load_ohlcv):
    """OHLCV 最新日期早于 trade_date 时，状态应为 preloaded。"""
    mock_df = pd.DataFrame({
        "Date": ["2026-06-28", "2026-06-30"],
        "Close": [10.0, 10.5],
    })
    mock_load_ohlcv.return_value = mock_df

    report = check_data_readiness("000001.SZ", "2026-07-03", ["market"])
    ohlcv_item = [i for i in report.items if i.label == "日K行情"][0]
    assert ohlcv_item.status == "preloaded"


@patch("tradingagents.agents.utils.data_readiness.load_ohlcv")
def test_check_market_analyst_ohlcv_unavailable(mock_load_ohlcv):
    """OHLCV 无数据时应返回 unavailable。"""
    from tradingagents.dataflows.errors import NoMarketDataError
    mock_load_ohlcv.side_effect = NoMarketDataError("TEST", "test", "no data")

    report = check_data_readiness("INVALID", "2026-07-03", ["market"])
    ohlcv_item = [i for i in report.items if i.label == "日K行情"][0]
    assert ohlcv_item.status == "unavailable"


def test_realtime_data_always_available():
    """实时数据（新闻、情绪等）应标记为 available。"""
    report = check_data_readiness("AAPL", "2026-07-03", ["news", "sentiment"])
    for item in report.items:
        assert item.category == "realtime"
        assert item.status == "available"


def test_multiple_analysts_with_shared_data_no_duplicates():
    """多个分析师共享同一数据源（如行情）时不应重复检查。"""
    # industry 和 market 都可能需要行情
    report = check_data_readiness("000001.SZ", "2026-07-03", ["market", "industry"])
    ohlcv_items = [i for i in report.items if i.label == "日K行情"]
    assert len(ohlcv_items) == 1
```

- [ ] **Step 5: 运行测试确认通过**

```bash
uv run pytest tests/test_data_readiness.py -v --tb=short
```

预期: 6 passed

---

### Task 2: 修改 CLI 交互流程

**Files:**
- Modify: `cli/main.py`
- 不涉及测试文件（CLI 交互流程测试需要模拟 questionary，暂不做）

**Interfaces:**
- Consumes: `check_data_readiness()`, `display_readiness_report()` from Task 1
- Modifies: `get_user_selections()` — 调整 step 顺序，插入 Step 3.5

- [ ] **Step 1: 调整分析师选择顺序**

将 Step 4（分析师选择）从 `get_user_selections()` 中移动到 Step 2（日期）之后，Step 3（输出语言）之前。

当前代码结构（约 598-757 行）：
```
Step 2: Analysis Date (line 598-607)
Step 3: Output Language (line 609-622)
Step 4: Select Analysts (line 624-636)
Step 5: Research Depth (line 638-644)
Step 6: LLM Provider (line 646-684)
Step 7: Thinking Agents (line 686-701)
Step 8: Thinking Config (line 703-738)
```

改为：
```
Step 2: Analysis Date (不变)
Step 3: Select Analysts (从原 Step 4 移过来)
Step 3.5: Data Readiness Check (新增)
Step 4: Output Language (原 Step 3)
Step 5: Research Depth (原 Step 5)
Step 6: LLM Provider (原 Step 6)
Step 7: Thinking Agents (原 Step 7)
Step 8: Thinking Config (原 Step 8)
```

具体修改：将 `selected_analysts = select_analysts(...)` 块（约 624-636 行）整体移动到 `analysis_date = get_analysis_date()` 之后、`output_language` 块之前。

- [ ] **Step 2: 插入数据就绪检查步骤**

在分析师选择块之后、输出语言步骤之前，添加：

```python
    # Step 3.5: Data Readiness Check
    from tradingagents.agents.utils.data_readiness import (
        check_data_readiness,
        display_readiness_report,
    )
    console.print()
    report = check_data_readiness(
        ticker=first_ticker if isinstance(first_ticker, str) else selected_tickers[0],
        trade_date=analysis_date,
        selected_analysts=[a.value for a in selected_analysts],
    )
    display_readiness_report(console, report)
    if report.warning_count > 0:
        import questionary
        proceed = questionary.confirm(
            "部分数据不可用，是否继续分析？",
            default=True,
        ).ask()
        if not proceed:
            console.print("[yellow]已取消分析[/yellow]")
            return None_get_user_selections_sentinel()  # 需设计一个 sentinel 返回
```

需要处理 `get_user_selections()` 返回 None 的情况。在 `run_analysis()` 中已有 `if selections is None: selections = get_user_selections()`，所以 `get_user_selections()` 返回 None 时应该让 `run_analysis()` 直接 return。

修改 `get_user_selections()` 签名：
```python
def get_user_selections(preselected_tickers: list[str] | None = None) -> dict | None:
```

并在分析被取消时 `return None`。

- [ ] **Step 3: 修改 `run_analysis()` 处理 None 返回值**

```python
def run_analysis(checkpoint...):
    if selections is None:
        selections = get_user_selections()
    if selections is None:   # 新增
        return               # 用户取消了分析
```

---

### 执行顺序

建议按 Task 1（模块）→ Task 2（CLI 集成）的顺序执行，因为 Task 2 依赖 Task 1 的接口。

**可并行的：**
- Task 1 Step 1-3 与 Task 1 Step 4（测试）可以串行（先写代码再写测试）
- 全部顺序执行即可，依赖关系简单

**估计工作量：** Task 1 ~ 15 分钟，Task 2 ~ 10 分钟，总 ~ 25 分钟