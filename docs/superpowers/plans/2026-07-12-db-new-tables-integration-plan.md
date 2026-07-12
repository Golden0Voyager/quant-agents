# 利用新数据库表增强分析能力 — 实施计划

## 目标

把 PR #12 新增的数据库表接入 `quant_agents` 分析流水线，优先落地三个高价值、低改动成本的能力：

1. **北向资金流向**（`north_flow` → `get_northbound_hold`）
2. **涨跌停市场情绪**（`limit_up_down` → 新工具 `get_limit_up_down`）
3. **大盘/板块指数日线**（`index_daily` → 新工具 `get_index_daily`）

## 背景

- 当前 `tradingagents/dataflows/smartmoney_vendor.py` 已从 `quant_core.db` 读取多张 A 股表，但 `north_flow` 等 PR #12 表仍抛出 `RuntimeError`。
- `route_to_vendor`（`interface.py`）是统一的 vendor 路由 seam；新增读函数 + 注册即可让 agent 工具无感知接入。
- `data_readiness.py` 负责批量运行前的数据预检，新表需要对应的缓存检查。

## 设计原则

- **深度优先**：每个数据维度一个 `smartmoney_vendor.py` 读函数，返回 LLM 可直接消费的 markdown 字符串；agent 工具只做 thin wrapper。
- **零破坏**：所有改动 additive；不删除现有 vendor 映射、不改现有工具签名、不改 agent 核心逻辑。
- **可测试**：每个新读函数都有 SQLite in-memory 测试覆盖；端到端通过 `tests/cli/` 验证 agent 工具链。

## 任务分解

### Task 1：接入 `north_flow` 北向资金

**改动文件**
- `tradingagents/dataflows/smartmoney_vendor.py`：实现 `get_northbound_hold(symbol)` 读取 `north_flow` 表。
- `tradingagents/dataflows/interface.py`：把 `get_smartmoney_northbound_hold` 注册到 `VENDOR_METHODS["get_northbound_hold"]`，替换当前抛异常的占位。
- `tradingagents/agents/utils/data_readiness.py`：为 governance analyst 的 `northbound` 项增加 `_check_smartmoney_table(..., "north_flow", ...)`，让预检知道本地是否有缓存。
- `tests/test_smartmoney_vendor.py`：新增 `north_flow` 表及 `get_northbound_hold` 测试用例。

**接口约定**
```python
def get_northbound_hold(symbol: str) -> str:
    """Read Stock Connect (northbound) flow for an A-share from quant_core.db.

    Returns markdown with recent trade_date, buy_amount, sell_amount,
    net_amount for the ticker, or raises RuntimeError so route_to_vendor
    falls back to akshare.
    """
```

**验收标准**
- A 股调用 `get_northbound_hold("600519.SS")` 时，`route_to_vendor` 优先命中 `smartmoney_db`。
- 本地无数据时仍优雅降级到 akshare。
- `check_data_readiness` 对 governance analyst 显示北向资金缓存状态。

### Task 2：接入 `limit_up_down` 涨跌停统计

**改动文件**
- `tradingagents/dataflows/smartmoney_vendor.py`：实现 `get_limit_up_down(trade_date: str)` 读取 `limit_up_down` 表。
- `tradingagents/dataflows/interface.py`：在 `VENDOR_METHODS` 新增 `"get_limit_up_down"` 映射；在 `TOOLS_CATEGORIES` 里新增类别或归入 `technical_indicators` / `macro_data`。
- `tradingagents/agents/utils/news_data_tools.py`（或新建 `market_breadth_tools.py`）：新增 `@tool get_limit_up_down`。
- `tradingagents/agents/analysts/market_analyst.py`：把 `get_limit_up_down` 加入 tools 列表，并在 system prompt 里提示“分析个股时参考当日涨跌停家数判断市场情绪”。
- `tradingagents/agents/analysts/sentiment_analyst.py`：在 prompt 里注入涨跌停数据块（类似 news/hot_rank/guba 的预取模式），或仅让 Market Analyst 使用。
- `tradingagents/agents/utils/data_readiness.py`：新增 `limit_up_down` 缓存检查（按日期而非按标的）。
- `tests/test_smartmoney_vendor.py`：新增 `limit_up_down` 表及测试。
- `tests/cli/test_batch_runner.py` 或新建测试：验证 Market Analyst 工具列表包含新工具。

**接口约定**
```python
def get_limit_up_down(trade_date: str) -> str:
    """Fetch market-wide limit-up/limit-down stats for a trading date.

    Returns markdown with total limit-up, limit-down, up_limit_stocks count,
    etc., from quant_core.db. Raises RuntimeError if missing.
    """
```

**验收标准**
- 新工具可被 LLM 调用并返回格式化涨跌停统计。
- Market Analyst prompt 明确指导其使用此数据。
- 无本地数据时降级到 akshare（如 akshare 有对应接口）或直接报 unavailable。

### Task 3：接入 `index_daily` 指数日线

**改动文件**
- `tradingagents/dataflows/smartmoney_vendor.py`：实现 `get_index_daily(index_code: str, start_date: str, end_date: str)` 读取 `index_daily` 表。
- `tradingagents/dataflows/interface.py`：新增 `VENDOR_METHODS["get_index_daily"]` 映射。
- `tradingagents/agents/utils/core_stock_tools.py`（或新建 `index_data_tools.py`）：新增 `@tool get_index_daily`。
- `tradingagents/agents/analysts/market_analyst.py`：把 `get_index_daily` 加入 tools 列表，并在 system prompt 里提示“调用大盘/板块指数对比个股走势”。
- `tradingagents/agents/utils/data_readiness.py`：新增 `index_daily` 缓存检查（按指数代码）。
- `tests/test_smartmoney_vendor.py`：新增 `index_daily` 表及测试。

**接口约定**
```python
def get_index_daily(
    index_code: Annotated[str, "Index code, e.g. 000001.SH (SSE), 399001.SZ (SZSE), 399006.SZ (ChiNext)"],
    start_date: Annotated[str, "Start date YYYY-MM-DD"],
    end_date: Annotated[str, "End date YYYY-MM-DD"],
) -> str:
    """Fetch index daily OHLCV from quant_core.db.

    Returns markdown OHLCV table for the requested index and date range.
    Raises RuntimeError if missing so route_to_vendor can fall back.
    """
```

**验收标准**
- `get_index_daily("000001.SH", "2026-06-01", "2026-06-19")` 返回上证指数日线。
- Market Analyst 可调用该工具对比个股与大盘走势。

### Task 4：数据预检与测试收尾

**改动文件**
- `tradingagents/agents/utils/data_readiness.py`：确保 Task 1-3 的缓存检查项都被 `check_data_readiness` 调用。
- `tests/test_dataflows_config.py`（如存在工具映射测试）：验证新工具在 `VENDOR_METHODS` 中注册。
- 运行并修复失败测试：
  ```bash
  uv run python -m pytest tests/test_smartmoney_vendor.py tests/test_smartmoney_vendor_edge_cases.py -v
  uv run python -m pytest tests/cli/test_batch_runner.py tests/cli/test_dashboard.py -m unit -v
  ```

## 范围外（后续可扩展）

- PR #12 的宏观表：`gold_price`、`crude_oil`、`fx_rate`、`global_index`、`us_treasury` — 建议先验证 P0 三个表的效果后再接入，可归入 `get_macro_indicators` 的 `smartmoney_db` 实现。
- `dividend_summary` — 已有 `get_dividend_history` 走 akshare，优先级较低。
- PR #9 的增量检测/资源管理接口 — 当前 `quant_agents` 只读 `quant_core.db`，写端由外部 quant_hunter 维护，暂不需要引入。

## 测试策略

- **单元测试**：每个 `smartmoney_vendor.py` 新函数都有 in-memory SQLite 测试（正例 + 空表降级）。
- **集成测试**：通过 `tests/cli/test_batch_runner.py` 的 mock graph 验证新工具出现在 agent tools 中。
- **手动验证**：跑一个 A 股 batch（如 `600519.SS`），检查 `batch_summary.md` 中 Market Analyst 报告是否引用北向资金/涨跌停/指数对比。

## 风险与回退

- **表结构不确定**：如果实际 `quant_core.db` 的 `north_flow` / `limit_up_down` / `index_daily` 列名与计划假设不同，第一次真实 DB 运行可能失败。回退：函数内部捕获异常并抛 `RuntimeError`，`route_to_vendor` 自动 fallback。
- **新工具让 prompt 变长**：每个新增工具都会占用 token。如一次 batch 成本明显上升，可后续把 `limit_up_down` 从 Market Analyst 移到 Sentiment Analyst 或减少 prompt 细节。

## 文件清单

- 修改：
  - `tradingagents/dataflows/smartmoney_vendor.py`
  - `tradingagents/dataflows/interface.py`
  - `tradingagents/agents/utils/data_readiness.py`
  - `tradingagents/agents/analysts/market_analyst.py`
  - `tradingagents/agents/utils/fund_flow_tools.py`（仅 docstring 更新，说明 smartmoney_db 支持）
  - `tradingagents/agents/utils/news_data_tools.py`（新增 `get_limit_up_down`）
  - `tradingagents/agents/utils/core_stock_tools.py`（新增 `get_index_daily`）
- 新增测试：
  - `tests/test_smartmoney_vendor.py` 补充用例
  - 可能新增 `tests/test_market_breadth_tools.py`
