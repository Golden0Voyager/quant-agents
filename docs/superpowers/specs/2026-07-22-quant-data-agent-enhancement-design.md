# Quant Data Agent High-Alpha Integration Design

> **Author**: Senior Quant Expert & LLM Trading Systems Architect  
> **Date**: 2026-07-22  
> **Status**: Approved  
> **Target Project**: `quant_agents` (`tradingagents/`)  
> **Database Source**: `quant_data` (`quant_core.db`)

---

## 1. Executive Summary & Expert Review

本设计旨在将 `quant_data` 数据库（5.2GB，60+ 张量化特征表）中的高 Alpha 核心数据深度接入 `quant_agents` 的 Multi-Agent 架构中。

根据资深财经量化专家的审查，单纯透传原始 SQL 结果给 LLM 会存在两大缺陷：
1. **因子无量纲化与语义缺乏**：LLM 对纯数字缺乏直观量化感知（例如仅知道成本 15 元，无法精准推算与当前股价的乖离率及支撑弹力）。
2. **价值陷阱与游资噪音**：单独看 PE 低分位数容易陷入“价值陷阱”；单独看龙虎榜容易误把游资对倒当成机构抢筹。

因此，本方案在 **方案一（分析师按需工具增强）** 的基础上，进行了**量化因子级增强（Quant Factor Synthesis）**，在 vendor 层实现量化计算与风险预警，再抛给 Agent 分析师。

---

## 2. Quantitative Factor Design (量化因子设计)

### 2.1 筹码分布与成本偏离度 (`get_chip_distribution`)
* **关联表**: `chip_distribution` / `chip_distribution_em`
* **计算逻辑**:
  - `profit_ratio`: 获利盘比例 (%)
  - `avg_cost`: 筹码平均成本
  - `concentration_90` / `concentration_70`: 筹码集中度
  - `cost_bias`: 股价成本乖离率 `(close - avg_cost) / avg_cost * 100%`
* **量化信号判定**:
  - 筹码单峰密集 + 获利盘 > 80% + 低乖离率 (0~5%) → **突破主升浪前兆**
  - 获利盘 < 15% + 处于成本区下方 → **超跌筑底区/沉淀区**

### 2.2 估值分位数与 ROE 匹配 (`get_historical_valuation`)
* **关联表**: `historical_valuation`, `fundamentals`, `quarterly_financials`
* **计算逻辑**:
  - PE-TTM / PB 近 3 年及近 5 年历史百分位 (%)
  - 结合近 4 季度 `ROE` 变动趋势
* **量化信号判定**:
  - PE 百分位 < 20% 且 ROE 稳定/上升 → **真安全边际 (Deep Value)**
  - PE 百分位 < 20% 但 ROE 连续下降 → **警惕价值陷阱 (Value Trap Alert)**

### 2.3 龙虎榜与游资/机构筹码博弈 (`get_dragon_tiger_summary`)
* **关联表**: `dragon_tiger`
* **计算逻辑**:
  - 近 30 天上榜次数
  - 机构席位净买入总额 vs 游资席位净买入总额
  - 买一/买二集中度
* **量化信号判定**:
  - 机构主买且集中度高 → **机构建仓突破**
  - 游资高频对倒且机构净卖出 → **游资炒作/出货风险**

### 2.4 机构调研与关注度升温 (`get_institution_survey`)
* **关联表**: `institution_survey`
* **计算逻辑**:
  - 近 90 天调研次数、接待机构家数
  - 头部机构（知名公募/私募）参与度
  - 调研核心关注主题提取

### 2.5 业绩预告与增长弹性 (`get_earnings_forecast`)
* **关联表**: `earnings_forecast`, `quarterly_financials`
* **计算逻辑**:
  - 业绩预告类型（预增/扭亏/预减/略减）
  - 净利润同比变动中枢与超预期幅度

### 2.6 概念板块与轮动共振 (`get_concept_board`)
* **关联表**: `concept_board`, `concept_member`
* **计算逻辑**:
  - 所属概念板块清单与核心主线题材标记

---

## 3. Architecture & Multi-Agent Routing (架构与工具路由)

```
┌────────────────────────────────────────────────────────────────────────┐
│                        quant_data (quant_core.db)                      │
│ ┌──────────────────┬──────────────────────┬──────────────────────────┐ │
│ │chip_distribution │ historical_valuation │   institution_survey     │ │
│ └─────────┬────────┴──────────┬───────────┴────────────┬─────────────┘ │
└───────────┼───────────────────┼────────────────────────┼───────────────┘
            │                   │                        │
            ▼                   ▼                        ▼
┌────────────────────────────────────────────────────────────────────────┐
│                      smartmoney_vendor.py (Vendor)                     │
│ ┌──────────────────┬──────────────────────┬──────────────────────────┐ │
│ │get_chip_distrib  │get_historical_valuat │get_institution_survey    │ │
│ └─────────┬────────┴──────────┬───────────┴────────────┬─────────────┘ │
└───────────┼───────────────────┼────────────────────────┼───────────────┘
            │                   │                        │
            ▼                   ▼                        ▼
┌────────────────────────────────────────────────────────────────────────┐
│                      agent_utils.py / interface.py                     │
│               (LangChain @tool Decorators & Verification)              │
└───────────┬───────────────────┬────────────────────────┬───────────────┘
            │                   │                        │
            ▼                   ▼                        ▼
┌────────────────────────────────────────────────────────────────────────┐
│                     Analyst Agents (LangGraph Nodes)                   │
│ ┌────────────────┐ ┌────────────────────┐ ┌─────────────────────────┐ │
│ │ MarketAnalyst  │ │FundamentalsAnalyst │ │    GovernanceAnalyst    │ │
│ │(Chip+Price/Vol)│ │(Valuation+Forecast)│ │(Survey+DragonTiger+Pled)│ │
│ └────────────────┘ └────────────────────┘ └─────────────────────────┘ │
└────────────────────────────────────────────────────────────────────────┘
```

### 工具绑定表

| 分析师 Agent | 专属新增工具 | 核心量化洞察重点 |
|---|---|---|
| **MarketAnalyst** | `get_chip_distribution` | 筹码密集峰、获利盘比例、股价成本乖离率 |
| **FundamentalsAnalyst** | `get_historical_valuation`, `get_earnings_forecast` | 估值百分位、ROE匹配度、价值陷阱预警、业绩弹性 |
| **GovernanceAnalyst** | `get_institution_survey`, `get_dragon_tiger_summary` | 机构调研频次、龙虎榜游资/机构博弈、股权质押与解锁风险 |
| **IndustryAnalyst** | `get_concept_board` | 概念题材属性、主线轮动共振 |

---

## 4. Implementation Steps & Verification Plan

### 4.1 详细修改步骤
1. **[smartmoney_vendor.py](file:///Users/hainingyu/Code/quant_agents/tradingagents/dataflows/smartmoney_vendor.py)**: 实现上述 5 个带有量化因子计算与格式化的 SQL 函数。
2. **[interface.py](file:///Users/hainingyu/Code/quant_agents/tradingagents/dataflows/interface.py)**: 在 router 中接入 smartmoney 接口路由。
3. **[agent_utils.py](file:///Users/hainingyu/Code/quant_agents/tradingagents/agents/utils/agent_utils.py)**: 使用 `@tool` 封装并增加清晰 Prompt Docstrings。
4. **分析师 Agent 节点**:
   - `tradingagents/agents/analysts/market_analyst.py`
   - `tradingagents/agents/analysts/fundamentals_analyst.py`
   - `tradingagents/agents/analysts/governance_analyst.py`
   - `tradingagents/agents/analysts/industry_analyst.py`
5. **[data_readiness.py](file:///Users/hainingyu/Code/quant_agents/tradingagents/agents/utils/data_readiness.py)**: 更新 `ANALYST_DATA_REQUIREMENTS` 包含新维度。

### 4.2 验证计划
- **单元测试**: 在 `tests/test_smartmoney_vendor.py` 中增加新 SQL 查询函数的测试用例，确保以真实/Mock SQLite 数据能正确输出格式化结果与因子计算。
- **集成测试**: 运行 `pytest -m unit` 确保现有 2500+ 测试全部通过。
- **实战验证**: 挑选典型标的（如 `600519.SS` 或 `000001.SZ`）运行单标的分析流程，检查生成的分析报告中是否包含筹码分布、历史估值百分位、机构调研等量化特征洞察。
