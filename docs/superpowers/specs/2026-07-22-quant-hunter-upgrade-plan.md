# Quant Hunter 适配 quant_data 升级的选股与量化增强方案 (Quant Hunter Upgrade Plan)

> **目标项目**: `quant_hunter` (`src/smartmoney_hunter/`)  
> **数据依托**: `quant_data` (`quant_core.db` - 5.2GB, 60+ 表)  
> **日期**: 2026-07-22  
> **状态**: 方案规划 (Design Phase)

---

## 1. 背景与升级动机

近期 `quant_data` 管道完成了重磅数据升级，补充了**筹码分布 (chip_distribution_em)**、**历史估值分位数 (historical_valuation)**、**机构调研 (institution_survey)**、**业绩预告 (earnings_forecast)** 及 **概念题材 (concept_member)** 等高 Alpha 数据。

`quant_hunter` 作为 A 股量化选股与筛选引擎（Screener Engine），目前主要基于传统的 K 线形态、MA/MACD/RSI 基础技术指标和简单财务表进行筛查。通过将 `quant_data` 的最新数据能力接入 `quant_hunter`，可以实现**从“传统技术/财务筛查”到“筹码主力行为+估值分位+机构动向三位一体选股”的质变**。

---

## 2. 详细升级架构设计

### 模块一：数据库与数据加载层增强 (`database.py` & `data_loader.py`)

在 `smartmoney_hunter/database.py` 中新增 5 个高效查询接口：
1. `get_chip_distribution_batch(symbols, curr_date)`: 批量读取获利盘比例、平均成本 `avg_cost`、90% 集中度 `concentration_90`，并计算 `price_to_cost_bias`。
2. `get_valuation_percentiles_batch(symbols, curr_date)`: 批量读取近 3 年 PE/PB 历史百分位及同行业相对折溢价。
3. `get_institution_survey_stats(symbols, days=30)`: 统计近 30 天机构调研频次与调研机构数量。
4. `get_latest_earnings_forecast(symbols)`: 获取最新业绩预告类型（预增/扭亏）与净利润同比变动中位数。
5. `get_stock_concepts(symbol)`: 提取股票归属的热门概念题材。

---

### 模块二：新增量化条件因子库与 AST 表达式引擎 (`filters/` & `screener.py`)

在 `smartmoney_hunter/filters/` 中新增 4 大类 12 个高 Alpha 条件过滤器，并支持在 CLI `--expr` 中组合：

#### 1. 筹码分布类 (Chip Distribution Factors)
- `profit_ratio_gt:80`：获利盘比例 > 80%
- `price_to_cost_bias_between:-2,5`：股价距筹码平均成本偏离度在 -2% ~ +5% 之间（突破主升浪临界点）
- `chip_concentration_90_lt:12`：90% 筹码集中度 < 12%（筹码高度单峰集中，主力锁仓）

#### 2. 估值分位与防爆类 (Valuation & Quality Factors)
- `pe_percentile_3y_lt:20`：PE(TTM) 处于近 3 年底部 20% 分位数以内
- `sector_pe_discount_gt:15`：相比所属行业平均 PE 折价 > 15%
- `deep_value_quality_guard`：自动剔除低 PE 但 ROE<5% 的价值陷阱标的

#### 3. 聪明资金与关注度类 (Smart Money Factors)
- `institution_survey_count_30d_gt:3`：近 30 天机构调研次数 ≥ 3 次
- `institutional_holding_ratio_gt:25`：机构持仓比例 > 25%

#### 4. 业绩爆发力类 (Earnings Momentum Factors)
- `earnings_forecast_type_in:预增,扭亏`：最新业绩预告为预增或扭亏
- `earnings_forecast_growth_gt:50`：业绩预告净利润同比变动下限 > 50%

---

### 模块三：新增高 Alpha 预设选股策略 (`strategy.py`)

在 `smartmoney_hunter/strategy.py` 中新增 3 套量化选股策略：

```python
PRESET_STRATEGIES = {
    # 策略 1：筹码密集突破猎手 (单边主升浪前夕)
    "chip_breakout_hunter": {
        "name": "筹码密集突破猎手",
        "description": "寻找获利盘高(>75%)、成本偏离度低(-2%~5%)且筹码高度集中(<12%)的突破标的",
        "conditions": [
            "profit_ratio_gt:75",
            "price_to_cost_bias_between:-2,5",
            "chip_concentration_90_lt:12",
            "volume_ratio_gt:1.5",
        ],
        "weight_schema": {"price_to_cost_bias": 0.4, "profit_ratio": 0.3, "volume_ratio": 0.3},
    },
    
    # 策略 2：真安全边际成长策略 (防价值陷阱)
    "deep_value_growth_hunter": {
        "name": "真安全边际成长策略",
        "description": "寻找PE分位数<20%、ROE>10%且业绩预告大增(>30%)的低估高增长标的",
        "conditions": [
            "pe_percentile_3y_lt:20",
            "roe_gt:10",
            "earnings_forecast_growth_gt:30",
            "sector_pe_discount_gt:10",
        ],
        "weight_schema": {"pe_percentile": 0.4, "roe": 0.3, "forecast_growth": 0.3},
    },
    
    # 策略 3：机构抱团与密集调研策略 (聪明资金动向)
    "institutional_accumulation_hunter": {
        "name": "机构密集调研与抱团策略",
        "description": "寻找近30天机构调研次数≥3次、机构持仓占比高且筹码集中度上升的标的",
        "conditions": [
            "institution_survey_count_30d_gt:3",
            "institutional_holding_ratio_gt:25",
            "chip_concentration_trend:up",
        ],
        "weight_schema": {"survey_count": 0.4, "holding_ratio": 0.3, "concentration": 0.3},
    },
}
```

---

### 模块四：AI 单股分析研判 Prompt 升级 (`ai_analyzer.py`)

将 `quant_data` 的筹码分布、估值分位与机构调研上下文注入 `ai_analyzer.py` 中：
- 执行 `hunter analyze --code 600519` 时，除了常规 K 线与财务报表外，自动附带筹码偏离度与 3 年估值百分位图谱。
- 提示词中加入防爆指示：引导 DeepSeek / Qwen 判断“真安全边际 vs 价值陷阱”与“周期股低 PE 顶点风险”。

---

### 模块五：TUI 终端与 CLI 展现升级 (`tui/` & `cli.py`)

1. **CLI 命令扩展**:
   - `hunter scan --preset chip_breakout_hunter --top 20`
   - `hunter scan --preset deep_value_growth_hunter --top 20`
   - `hunter scan --preset institutional_accumulation_hunter --top 20`
   - `hunter quick --expr "profit_ratio > 80 and price_to_cost_bias < 5"`
2. **TUI 界面图形展示 (Textual)**:
   - 在 `hunter tui` 的股票详情视窗中，增加筹码集中度图表与估值百分位柱状条。

---

## 3. 实施计划与里程碑 (Milestones)

| 里程碑 | 内容 | 预估工时 |
|---|---|---|
| **Phase 1** | `database.py` & `data_loader.py` 增加 5 大 SQL 批量提取接口 | 0.5 天 |
| **Phase 2** | `filters/` 新增筹码、估值分位、机构调研、业绩预告 12 个过滤器 | 0.5 天 |
| **Phase 3** | `strategy.py` 新增 3 套预设策略 & `screener.py` 权重融合 | 0.5 天 |
| **Phase 4** | `ai_analyzer.py` 注入筹码与估值上下文 & 防爆 Prompt | 0.5 天 |
| **Phase 5** | CLI 命令与 TUI 界面适配 & 单元测试全覆盖 | 0.5 天 |

---

## 4. 验证方案

1. **单元测试**:
   - 在 `quant_hunter` 的 `tests/` 下新增 `test_chip_filters.py`, `test_valuation_percentile_filters.py` 等测试文件。
2. **选股实测**:
   - 运行 `python -m smartmoney_hunter.cli scan --preset chip_breakout_hunter` 并在真实 A 股数据上验证筛选出的突破股票列表质量。
