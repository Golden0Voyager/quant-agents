# Quant Data Agent High-Alpha Integration Design (v2.0 - Robustness & Optimization Package)

> **Author**: Senior Quant Expert & LLM Trading Systems Architect  
> **Date**: 2026-07-22  
> **Status**: Approved  
> **Target Project**: `quant_agents` (`tradingagents/`)  
> **Database Source**: `quant_data` (`quant_core.db`)

---

## 1. Executive Summary & Expert Review

本设计旨在将 `quant_data` 数据库（5.2GB，60+ 张量化特征表）中的高 Alpha 核心数据深度接入 `quant_agents` 的 Multi-Agent 架构中。

在基础接入方案的基础上，v2.0 增加了 **4 大高鲁棒性防爆与工具瘦身优化包（Robustness Package）**：
1. **数据时效性滞后预警 (Data Freshness Guard)**：落后当前交易日 >2 天时自动注入警告，规避 Pipeline 同步中断风险。
2. **机构情报工具合并 (Tool Consolidation)**：合并机构持仓与机构调研为 `get_institutional_intelligence`，缓解 Tool Choice 延迟。
3. **次新股/小样本分位数校验 (Sample Size Guard)**：历史条数 <120 时注入小样本警示，防止次新股估值误判。
4. **行业相对估值与周期股防爆 (Sector Relative & Cyclical Guard)**：引入同行业 PE 折溢价对比，并拦截低 PE 周期股顶点陷阱。

---

## 2. Quantitative Factor & Robustness Guards Design

### 2.1 筹码分布与成本偏离度 (`get_chip_distribution`)
* **关联表**: `chip_distribution_em` / `chip_distribution`, `daily_bars`
* **计算逻辑**:
  - `profit_ratio`: 获利盘比例 (%)
  - `avg_cost`: 筹码平均成本
  - `concentration_90` / `concentration_70`: 筹码集中度
  - `price_to_cost_bias`: 股价成本乖离率 `(close - avg_cost) / avg_cost * 100%`
  - **Data Freshness Guard**: 比对 `curr_date` 与最新 `trade_date`，偏离 >2 天时注入头部预警。

### 2.2 估值分位数、行业相对估值与周期股拦截 (`get_historical_valuation`)
* **关联表**: `historical_valuation`, `fundamentals`, `quarterly_financials`, `sector_industry`
* **计算逻辑**:
  - PE-TTM / PB 近 3 年历史百分位 (%)
  - **Sample Size Guard**: 检验历史条数 `len(df)`，若 `< 120` 注入次新股样本量警示。
  - **Sector Relative**: 计算与同行业平均 PE 的折溢价比例 `(curr_pe - avg_pe) / avg_pe * 100%`。
  - **Cyclical Guard**: 对周期性行业（钢铁、煤炭、航运、化工等）且 PE < 8 时，注入周期股景气顶点风险预警。

### 2.3 机构综合情报合并 (`get_institutional_intelligence`)
* **关联表**: `institution_survey`, `institutional_holdings`
* **整合逻辑**:
  - 将原本独立的机构持仓与机构调研合并输出。
  - 减少 GovernanceAnalyst 的 Tool 数量，提升 LLM 工具选择速度。

### 2.4 业绩预告 (`get_earnings_forecast`)
* **关联表**: `earnings_forecast`
* **计算逻辑**: 预告类型（预增/扭亏/预减）与净利润同比变动幅度。

### 2.5 概念板块 (`get_concept_board`)
* **关联表**: `concept_board`, `concept_member`
* **计算逻辑**: 所属概念题材清单与题材热度。

---

## 3. Architecture & Multi-Agent Routing

### 工具绑定表 (v2.0)

| 分析师 Agent | 专属工具配置 | 核心量化与防爆防护 |
|---|---|---|
| **MarketAnalyst** | `get_chip_distribution` | 筹码密集峰、获利盘比例、成本偏离度、时效滞后防护 |
| **FundamentalsAnalyst** | `get_historical_valuation`, `get_earnings_forecast` | 估值百分位、ROE匹配、次新股小样本防护、行业相对折溢价、周期股陷阱拦截 |
| **GovernanceAnalyst** | `get_institutional_intelligence` *(合并)* | 机构调研频次、机构持仓变动、龙虎榜博弈、质押/解禁风险 |
| **IndustryAnalyst** | `get_concept_board` | 概念题材归属、板块轮动 |

---

## 4. Implementation Steps & Verification Plan

### 4.1 实施步骤
1. Update `smartmoney_vendor.py`: Implement `_check_stale_warning`, `get_institutional_intelligence`, sample size guards, and sector-relative/cyclical stock checks.
2. Update `news_data_tools.py` & `agent_utils.py` & `interface.py`: Expose `get_institutional_intelligence`.
3. Update `governance_analyst.py`: Use `get_institutional_intelligence` instead of separate survey/holdings calls.
4. Update `data_readiness.py`: Align data requirements.
5. Update `tests/test_smartmoney_vendor.py`: Add unit tests covering all 4 robustness guards.
6. Run full unit test suite.
