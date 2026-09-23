# Jev 自适应门控方案设计文档

> ⚠️ **状态：已过时（STALE / DEPRECATED）— 仅作历史记录保留，勿按本文实施**
>
> **原计划**：用 Jev 做架构级门控 —— 双层路由（代码数值预筛 + Jev 语义判断）把每只票分流到
> DeepThink / FastThink，优化整条决策管线的成本与耗时。
>
> **实际走向**：后续只落地了其中一小步 —— **用 Jev 对新闻端做质量门控**（见已实施的
> [`docs/jev_news_gate_design.md`](jev_news_gate_design.md)）。ticker 级 skip / FastThink /
> DeepThink 路由（本文 Phase 0/1/2）**从未开工，已放弃**。
>
> **⚠️ 本文 §5.3–§5.6 的代码段与已落地实现冲突，一律以 `docs/jev_news_gate_design.md`
> 和 `tradingagents/llm_clients/typesafe_client.py` 为准：**
>
> | 本文写的 | 实际代码 |
> |---|---|
> | `from typesafe_sdk import Noul, TypeSafeClient` | 刻意**不用** SDK，`requests.post` 直连 |
> | `class TypeSafeGateway` / `route()` | `class TypeSafeNewsGate` / `score_articles()` |
> | 配置键 `jev_enabled`、`jev_deepthink_novel_threshold` | `jev_news_gate_*` |

> 版本：v0.2-rev（基于 peer review 修订）
> 原始作者：Sisyphus
> 评审反馈采纳日期：2026-09-22
> **标记为过时：2026-09-23**
> 状态：**已过时 / 不再实施**

---

## 0. 修订说明

v0.1-draft 经 peer review 后识别出以下关键问题，本版本全部修正：

| v0.1 问题 | v0.2 修正 |
|-----------|-----------|
| 将可计算数值规则（振幅>2%、量能突增）外包给概率模型 | 代码预筛负责数值判断（$0成本），Jev 只负责语义判断 |
| `mean()` 聚合稀释单维极强信号（非补偿性判断误用） | 改为 `max()` / 任一触发规则 |
| `_build_instructions()` 引用未定义变量 `trade_date`（NameError） | 重写客户端代码，修复作用域 bug |
| `states` dict 构建后未传入 `system_one()`（地基断裂） | 修正数据流，per-ticker 数值摘要传入 state |
| "13个问题批量"与"5个维度"数字矛盾 | 明确：N×2 个问题（每个 ticker 2 个 Noul） |
| §2.3 与 §6 FastThink 成本数字自相矛盾（$0.05-0.10 vs $0.50/只） | 统一为 $0.05-0.10，§6 表格同步修正 |
| Phase 1 验证方式太弱（"观察是否合理"不是验证） | 新增 Phase 0 shadow mode，收集 2-4 周对照数据后再定阈值 |
| 未分析与 report_auditor.py / batch_summary.json 的兼容性 | 新增 §4 下游兼容性分析 + skip marker 机制 |
| 缺少 snippet 为空时的回退策略 | 新增 §5.3 回退矩阵 |
| 路由决策不可复现（同一 ticker 两次调用可能不同） | 新增路由原始 noul 值落盘要求 |

---

## 1. 背景与问题

### 1.1 当前批量分析流程

`TradingAgents-A-Share` 支持对 watchlist 批量运行完整决策管线：

```bash
tradingagents analyze --watchlist my-list --workers 3
```

每个 ticker 走完整图 pipeline（`TradingAgentsGraph.propagate()`）：

```
6 个 Analyst（并行工具调用）
    ↓
Bull vs Bear 辩论（deep_think_llm，默认 1 轮）
    ↓
Research Manager（bind_structured → ResearchPlan）
    ↓
Trader（bind_structured → TraderProposal，含 entry_price/stop_loss/position_sizing）
    ↓
风控三方辩论（aggressive/neutral/conservative debaters）
    ↓
Portfolio Manager（bind_structured → PortfolioDecision）
```

**每次完整 pipeline 消耗约 13 次 LLM 调用，耗时 2-5 分钟，成本约 $0.40-0.80（SenseNova）**。

### 1.2 核心问题

在典型 watchlist（20-50 只股票）中，**相当一部分 ticker 当日并无明显交易信号**——横盘、波动极小、无重大消息、行业低迷。对这些 ticker 仍然跑完整 DeepThink pipeline 是纯浪费：

- **成本浪费**：20 只 × $0.60 = $12/次 batch run
- **时间浪费**：20 只 × 3 分钟 = 60 分钟（单 worker）
- **API 配额消耗**：SenseNova Token Plan 每日额度有限，浪费在低价值 ticker 上

### 1.3 现有跳过机制的不足

目前 `batch_runner.py` 仅在"已有报告"时跳过（`_is_already_completed()`），无法识别"当日值得分析但尚未跑过"的低价值 ticker。

---

## 2. 设计原则（TypeSafe 官方指导）

TypeSafe 官方文档明确：

> *"Keep known rules, calculations, exact lookups, and execution in code."*
> *"An 'any serious violation' rule needs separate conditions."*
> *"System One models are built for fast, focused judgments. Ask for a judgment a knowledgeable person makes in a second given the right context."*

据此，本方案遵循以下分层原则：

```
第一层（代码，$0，100% 可复现）：可计算的数值规则
    - 日振幅、量比、换手率、涨跌停状态
    - 这些是确定性计算，不应外包给概率模型

第二层（Jev，~$0.0002/次）：纯语义判断
    - 新闻/公告对基本面的实质影响
    - 行业逻辑是否发生当日变化
    - 这才是 System One 模型的价值区

第三层（DeepThink / FastThink pipeline）：综合投资决策
    - 需要多 Agent 协作、辩论、结构化输出的场景
```

---

## 3. 解决方案：双层门控架构

### 3.1 架构总览

```
watchlist [24只]
     │
     ▼
┌─────────────────────────────────────┐
│  Layer 1: 代码数值预筛（$0，确定性的）│
│  计算：amplitude / volume_ratio /   │
│         limit_up_down / turnover    │
└─────────────┬───────────────────────┘
              │
    ┌─────────┼──────────┐
    ▼         ▼          ▼
直接Deep    Jev语义     直接Deep
  Think     门控         Think
 (记录)   (sev-latest)   (记录)
    │         │            │
    └────┬────┴────────────┘
         ▼
  ┌─────────────────┐
  │  Layer 2: Jev   │  ← 只对数值未触发的票问 2 个语义 Noul
  │  语义门控       │     （news significance + sector shift）
  └────────┬────────┘
           │
    ┌──────┼──────┐
    ▼      ▼      ▼
  skip  Fast    Deep
 (记录) Think   Think
         │       │
    轻量图   完整 pipeline
    (~30s)   (~3min)
```

### 3.2 Layer 1：代码数值预筛

在 Jev 调用之前，先用本地 OHLCV 数据做确定性筛选：

```python
def _code_prefilter(tickers: list[str], trade_date: str) -> dict[str, str]:
    """Layer 1: 基于可计算数值规则做确定性路由。

    返回 {ticker: route}，route 为 "fast_think"（数值平淡）或 None（未触发，需 Jev 判断）。
    注意：不返回 "skip"——单层规则无法捕获所有低价值场景。
    """
    routes: dict[str, str] = {}
    for ticker in tickers:
        try:
            # 从 smartmoney_db 或 akshare 取当日 OHLCV（轻量查询，~100ms）
            ohlcv = _fetch_daily_ohlcv(ticker, trade_date)
            if ohlcv is None:
                continue  # 数据缺失 → 默认 DeepThink（fail-open）

            daily_range_pct = (
                (ohlcv["high"] - ohlcv["low"]) / ohlcv["close"] * 100
                if ohlcv["close"] > 0 else 0
            )
            volume_ratio = ohlcv.get("volume_ratio", 1.0)  # 今日量 / 5日均量
            is_limit = ohlcv.get("is_limit_up") or ohlcv.get("is_limit_down")

            # 非补偿性规则：任一条件触发即升级
            triggers_deep = (
                daily_range_pct > 3.0    # 显著波动
                or volume_ratio > 2.0    # 量能异常放大
                or is_limit              # 涨停/跌停
            )
            if triggers_deep:
                routes[ticker] = "deep_think"  # 直接跳过 Jev
            elif daily_range_pct < 0.5 and volume_ratio < 0.8:
                routes[ticker] = "fast_think"  # 极度平淡，可降级
            # else: 数值中等 → 交给 Jev 做语义判断
        except Exception as exc:  # noqa: BLE001
            logger.debug("Prefilter failed for %s: %s", ticker, exc)
            # 数据获取失败 → fail-open，路由到 DeepThink
    return routes
```

**为什么不用 `skip`？** 单层规则会漏掉"数值平淡但有利好新闻"的票（如大盘横盘但个股出重组公告）。Layer 1 只做粗筛，最终路由由 Layer 2（Jev）+ Layer 1 共同决定。

### 3.3 Layer 2：Jev 语义门控

只对 Layer 1 未做决策的 ticker 调用 Jev。每个 ticker 问 2 个 Noul 问题：

```python
# 构建 state：包含 per-ticker 数值摘要（Layer 1 已算好）+ 少量新闻关键词
state = {
    "trade_date": trade_date,
    "tickers": {
        ticker: {
            "daily_range_pct": round(daily_range_pct, 2),
            "volume_ratio": round(volume_ratio, 2),
            "is_limit": is_limit,
            # 从公告/新闻源预取 2-3 条标题关键词（轻量，~50 tokens/条）
            "recent_headlines": [h["title"] for h in _fetch_headlines(ticker, days=1)],
            "sector": sector_name,
        }
        for ticker in pending_tickers  # Layer 1 未决策的票
    }
}

# 每只票 2 个 Noul 问题，全部并行评估
questions = {}
for ticker in pending_tickers:
    questions[f"{ticker}.news_material"] = Noul(
        instructions=(
            f"Given this A-share stock's context on {trade_date}: "
            f"daily range {state['tickers'][ticker]['daily_range_pct']}%, "
            f"volume ratio {state['tickers'][ticker]['volume_ratio']}x, "
            f"sector {state['tickers'][ticker]['sector']}, "
            f"recent headlines: {state['tickers'][ticker]['recent_headlines']}. "
            f"Does any headline represent a materially significant event "
            f"(e.g. earnings surprise, M&A, policy change, major contract, "
            f"regulatory action) that would justify a full multi-agent deep analysis?"
        ),
    )
    questions[f"{ticker}.sector_shift"] = Noul(
        instructions=(
            f"Given sector {state['tickers'][ticker]['sector']} context on {trade_date}, "
            f"and these recent headlines: {state['tickers'][ticker]['recent_headlines']}, "
            f"has the sector's fundamental logic changed in a way that materially "
            f"affects the investment thesis for stocks in this sector?"
        ),
    )

response = client.system_one(
    state=state,
    questions=questions,
    model="jev-latest",
)
```

### 3.4 路由聚合：非补偿性规则

```python
def _aggregate_route(
    code_route: dict[str, str],   # Layer 1 结果
    jev_answers: dict[str, dict], # Layer 2 结果 {ticker: {news: noul, sector: noul}}
    jev_deep_threshold: float = 0.6,
) -> dict[str, str]:
    """非补偿性聚合：任一维度超阈值即升级，不因其他维度低而稀释。"""
    routes = dict(code_route)  # Layer 1 已决策的不变

    for ticker in jev_answers:
        if ticker in routes:
            continue  # Layer 1 已决定，不走 Jev

        news_noul = jev_answers[ticker]["news_material"].noul
        sector_noul = jev_answers[ticker]["sector_shift"].noul

        # 非补偿性：任一维度 ≥ 阈值 → DeepThink
        # （符合 TypeSafe 官方："any serious violation" rule needs separate conditions）
        if news_noul >= jev_deep_threshold or sector_noul >= jev_deep_threshold:
            routes[ticker] = "deep_think"
        else:
            routes[ticker] = "fast_think"

    return routes
```

**为什么不用 mean()？**

```python
# 错误示例（v0.1 的问题）：
# 一只票突发重大资产重组（news_noul=0.95），但其余 4 个维度全平（0.1-0.3）
# mean([0.95, 0.1, 0.1, 0.1, 0.1]) = 0.252 → 路由到 FastThink/跳过
# 结果：漏掉了最值得 DeepThink 的票

# 正确做法：max() / 任一触发
# max([0.95, 0.1, 0.1, 0.1, 0.1]) = 0.95 → 路由到 DeepThink ✓
```

### 3.5 完整路由决策树

```
对每只 ticker：

  Layer 1（代码预筛）
    │
    ├─ amplitude>3% OR volume_ratio>2x OR limit_up/down
    │        └─→ DeepThink（跳过 Jev）
    │
    ├─ amplitude<0.5% AND volume_ratio<0.8
    │        └─→ FastThink（跳过 Jev）
    │
    └─ 数值中等（上述都不满足）
             └─→ Layer 2（Jev）
                    │
                    ├─ news_material ≥ 0.6  OR  sector_shift ≥ 0.6
                    │        └─→ DeepThink
                    │
                    └─ 两者均 < 0.6
                             └─→ FastThink
```

**不设置 Skip 路径**：Layer 1 极端平淡的票走 FastThink 而非 Skip，因为 FastThink 仍可产出有价值的方向性建议（Buy/Hold/Sell），成本仅比 Skip 多 ~$0.05，且避免漏掉误判。

---

## 4. 下游兼容性分析

### 4.1 batch_summary.json 兼容性

`batch_summary.json` 的结构（来自 `batch_runner.py:1158-1224`）：

```json
{
  "ticker": "000001.SZ",
  "company": "平安银行",
  "rating": "Buy",          // 或 null（FastThink 无结构化 PM 决策）
  "entry": 12.50,           // 或 null
  "stop": 11.80,            // 或 null
  "size": "5%",             // 或 null
  "confidence": "high",     // 或 "—"
  "status": "completed",    // 新增："fast_think" / "skip"
  "cost": 0.42,
  "tokens_in": 45000,
  "tokens_out": 1200
}
```

**FastThink 报告处理**：
- `rating`：从 FastResearcher 的纯文本评级解析（复用 `parse_rating()`，已有）
- `entry` / `stop` / `size`：设为 `null`
- `confidence`：设为 `"—"``或 FastResearcher 的文本置信度
- `status`：设为 `"fast_think"`（区别于 `"completed"`）
- `cost`：记录实际 LLM 调用成本（约 $0.05-0.10）

**Skip 处理（不启用，保留接口）**：
- `rating` / `entry` / `stop` / `size`：全部 `null`
- `status`：`"skip"`
- `details`：`"skipped_by_gate: amplitude<0.5% volume_ratio<0.8"`

### 4.2 report_auditor.py 兼容性

`report_auditor.py` 读取 `complete_report.md` 和 `batch_summary.json` 做交叉验证。

**FastThink 报告**：
- 文件路径：`{ticker}/complete_report_fast.md`（不同于 DeepThink 的 `complete_report.md`）
- 格式：简版 markdown，包含 analyst 摘要 + 最终评级 + 理由
- auditor 兼容策略：
  - 对 `status="fast_think"` 的行跳过财务数据一致性检查（无 financials 深度分析）
  - 仅做结构完整性检查（file exists, rating 字段非空）
  - 在 auditor 输出中标注 `[fast]` 前缀

### 4.3 portfolio_backtest.py 兼容性

`portfolio_backtest.py` 读取 `batch_summary.json` 中的 `rating` 字段做回测。

**FastThink 影响**：
- FastThink 的 `rating` 仍为 Buy/Hold/Sell 等标准值，回测逻辑无需改动
- 但 FastThink 缺少 `entry` / `stop` 价格，回测只能做方向性检验（rating 准确率），不做盈亏计算
- 建议在 backtest 输出中区分 `"full"` vs `"directional_only"` 模式

### 4.4 Skip Marker 机制

虽然当前方案不设 Skip（全量 FastThink 兜底），但为将来预留 skip marker 文件：

```
reports/YYYYMMDD_batch_<list>/
├── batch_summary.json
├── batch_summary.md
├── .gate_log/                    # 新增：Jev 门控日志目录
│   ├── gate_decision.json        # 门控决策快照（含原始 noul 值）
│   └── prefilter_results.json    # Layer 1 代码预筛结果
└── 000001.SZ/
    ├── complete_report_fast.md   # FastThink 报告（非 complete_report.md）
    └── ...
```

`gate_decision.json` 结构：
```json
{
  "trade_date": "2026-09-22",
  "watchlist_size": 24,
  "layer1_routes": {
    "000001.SZ": "deep_think",
    "600519.SS": null
  },
  "layer2_jev_calls": 1,
  "layer2_questions": 2,
  "jev_answers": {
    "600519.SS": {
      "news_material": 0.87,
      "sector_shift": 0.23
    }
  },
  "final_routes": {
    "000001.SZ": "deep_think",
    "600519.SS": "deep_think"
  },
  "timestamp": "2026-09-22T09:25:00+08:00"
}
```

**可复现性保障**：每次门控的原始 noul 值落盘，同一批输入的可复现性可通过对比 `gate_decision.json` 验证（Jev 本身有 run-to-run 噪声，但同一请求的多次重复应结果一致）。

---

## 5. 技术实现方案

### 5.1 新增文件

```
tradingagents/
├── llm_clients/
│   └── typesafe_client.py          # Jev 客户端封装
├── graph/
│   └── fast_graph.py               # FastThink 轻量图定义
└── agents/
    └── fast_researcher.py          # FastThink 路径的轻量 Research 节点
```

### 5.2 改动文件

```
tradingagents/
├── default_config.py               # 新增 jev_* 配置项
├── graph/
│   └── signal_prefilter.py         # Layer 1 数值预筛逻辑（新增）

cli/
├── batch_runner.py                 # run() 方法插入双层门控
└── main.py                        # CLI 新增 --jev-adaptive flag
```

### 5.3 配置项（default_config.py）

```python
# Jev 自适应门控配置
"jev_enabled": False,                              # 是否启用（默认关闭）
"jev_api_key_env": "TYPESAFE_API_KEY",             # API Key 环境变量名
"jev_deepthink_novel_threshold": 0.6,              # news/sector noul ≥ 此值 → DeepThink
"jev_prefilter_amplitude_deep": 3.0,               # 日振幅 > 此值 → 直接 DeepThink
"jev_prefilter_amplitude_fast": 0.5,               # 日振幅 < 此值 AND 量比<0.8 → FastThink
"jev_prefilter_volume_ratio_deep": 2.0,            # 量比 > 此值 → 直接 DeepThink
"jev_prefilter_volume_ratio_fast": 0.8,            # 量比 < 此值 AND 振幅<0.5% → FastThink
"jev_model": "jev-latest",                         # Jev 模型版本
"jev_fail_open": True,                             # API 故障时 fallback 到全量 DeepThink
"jev_log_dir": ".gate_log",                        # 门控日志目录（相对 output_dir）
```

环境变量覆盖：
```bash
export TYPESAFE_API_KEY=sk-xxx
export TRADINGAGENTS_JEV_ENABLED=true
export TRADINGAGENTS_JEV_DEEPTHINK_NOVEL_THRESHOLD=0.6
export TRADINGAGENTS_JEV_PREFILTER_AMPLITUDE_DEEP=3.0
```

### 5.4 Jev 客户端（typesafe_client.py）

```python
"""tradingagents/llm_clients/typesafe_client.py"""
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

try:
    from typesafe_sdk import Noul, TypeSafeClient
    _HAS_SDK = True
except ImportError:
    _HAS_SDK = False


class TypeSafeGatewayError(Exception):
    """Jev gateway 操作失败时抛出，上游捕获后 fail-open 到 DeepThink。"""
    pass


class TypeSafeGateway:
    """Jev 门控客户端，封装与 TypeSafe API 的交互。"""

    def __init__(self, config: dict):
        if not _HAS_SDK:
            raise TypeSafeGatewayError(
                "typesafe-sdk not installed. "
                "Install with: uv add typesafe-sdk"
            )

        api_key = os.getenv(config.get("jev_api_key_env", "TYPESAFE_API_KEY"))
        if not api_key:
            raise TypeSafeGatewayError(
                f"{config.get('jev_api_key_env', 'TYPESAFE_API_KEY')} not set."
            )

        self._client = TypeSafeClient(api_key=api_key)
        self._model = config.get("jev_model", "jev-latest")
        self._novel_threshold = config.get("jev_deepthink_novel_threshold", 0.6)
        self._fail_open = config.get("jev_fail_open", True)

    def route(
        self,
        tickers: list[str],
        trade_date: str,
        ticker_context: dict[str, dict],
    ) -> dict[str, dict]:
        """对一批 ticker 执行 Jev 语义评估，返回 {ticker: {key: noul_value}}。

        Args:
            tickers: 待评估的 ticker 列表（仅包含 Layer 1 未决策的票）
            trade_date: 交易日期字符串
            ticker_context: {ticker: {daily_range_pct, volume_ratio, sector, recent_headlines}}

        Returns:
            {ticker: {"news_material": 0.xx, "sector_shift": 0.xx}}

        Raises:
            TypeSafeGatewayError: API 调用失败时抛出，上游应 fail-open
        """
        if not tickers:
            return {}

        # 构建 state：per-ticker 数值摘要 + 新闻关键词
        state_tickers: dict[str, Any] = {}
        for ticker in tickers:
            ctx = ticker_context.get(ticker, {})
            state_tickers[ticker] = {
                "daily_range_pct": ctx.get("daily_range_pct", 0.0),
                "volume_ratio": ctx.get("volume_ratio", 1.0),
                "sector": ctx.get("sector", "unknown"),
                "recent_headlines": ctx.get("recent_headlines", []),
            }

        state = {
            "trade_date": trade_date,
            "tickers": state_tickers,
        }

        # 构建问题：每个 ticker 2 个 Noul
        questions: dict[str, Any] = {}
        for ticker in tickers:
            ctx = state_tickers[ticker]
            headlines = ", ".join(ctx["recent_headlines"][:3]) if ctx["recent_headlines"] else "none"
            questions[f"{ticker}.news_material"] = Noul(
                instructions=(
                    f"Stock {ticker} on {trade_date}: "
                    f"daily range {ctx['daily_range_pct']}%, volume ratio {ctx['volume_ratio']}x, "
                    f"sector {ctx['sector']}. "
                    f"Recent headlines: {headlines}. "
                    f"Does any headline represent a materially significant event "
                    f"(earnings surprise, M&A, policy change, major contract, regulatory action) "
                    f"justifying full multi-agent deep analysis? Return probability of yes."
                ),
            )
            questions[f"{ticker}.sector_shift"] = Noul(
                instructions=(
                    f"Sector {ctx['sector']} on {trade_date}. "
                    f"Recent headlines affecting this sector: {headlines}. "
                    f"Has the sector's fundamental investment logic changed materially today? "
                    f"Return probability of yes."
                ),
            )

        try:
            response = self._client.system_one(
                state=state,
                questions=questions,
                model=self._model,
            )
        except Exception as exc:
            if self._fail_open:
                logger.warning(
                    "Jev API call failed for %d tickers, failing open to DeepThink: %s",
                    len(tickers), exc,
                )
                # 返回全 deep_think 占位，上游会 fallback
                return {t: {"news_material": 1.0, "sector_shift": 1.0} for t in tickers}
            raise TypeSafeGatewayError(f"Jev API call failed: {exc}") from exc

        # 解析结果
        results: dict[str, dict] = {}
        for ticker in tickers:
            news_key = f"{ticker}.news_material"
            sector_key = f"{ticker}.sector_shift"
            results[ticker] = {
                "news_material": response.answers[news_key].noul,
                "sector_shift": response.answers[sector_key].noul,
            }
        return results
```

### 5.5 BatchRunner 集成点

在 `cli/batch_runner.py` 的 `run()` 方法中，**在 ticker 循环之前**插入双层门控：

```python
# ── Jev Adaptive Gate (双层门控) ─────────────────────────────────
jev_routes: dict[str, str] = {}
jev_log: dict[str, Any] = {}  # 用于落盘 gate_decision.json

if config.get("jev_enabled"):
    try:
        from tradingagents.graph.signal_prefilter import code_prefilter
        from tradingagents.llm_clients.typesafe_client import TypeSafeGateway

        # Layer 1: 代码数值预筛
        jev_log["layer1"] = "running"
        code_routes, prefilter_data = code_prefilter(self.tickers, trade_date)
        jev_log["layer1_routes"] = code_routes
        jev_log["prefilter_tickers_count"] = len(prefilter_data)

        # Layer 2: Jev 语义门控（仅对 Layer 1 未决策的票）
        pending = [t for t in self.tickers if t not in code_routes]
        if pending:
            jev_log["layer2"] = "running"
            gateway = TypeSafeGateway(config)
            jev_answers = gateway.route(pending, trade_date, prefilter_data)
            jev_log["jev_answers"] = jev_answers

            # 聚合：Layer 1 + Layer 2 → 最终路由
            from tradingagents.graph.signal_prefilter import aggregate_routes
            jev_routes = aggregate_routes(code_routes, jev_answers)
        else:
            jev_routes = code_routes  # 全部 Layer 1 已决策

        jev_log["final_routes"] = jev_routes

        # 统计并打印
        from collections import Counter
        dist = Counter(jev_routes.values())
        console.print(f"[dim]Jev gate: {dist.get('deep_think',0)} deep, "
                      f"{dist.get('fast_think',0)} fast[/dim]")

        # 落盘 gate_decision.json（保证可复现性审计）
        gate_log_path = self.output_dir / ".gate_log" / "gate_decision.json"
        gate_log_path.parent.mkdir(parents=True, exist_ok=True)
        gate_log_path.write_text(
            json.dumps(jev_log, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    except Exception as exc:  # noqa: BLE001
        logger.warning("Jev gate failed, falling back to full DeepThink: %s", exc)
        jev_routes = {t: "deep_think" for t in self.tickers}
else:
    jev_routes = {}  # 未启用时所有票默认 deep_think

# 原有 ticker 循环，根据路由分发
for ticker in self.tickers:
    route = jev_routes.get(ticker, "deep_think")
    if route == "deep_think":
        self._run_single(ticker)  # 原有逻辑不变
    elif route == "fast_think":
        self._run_fast_think(ticker, trade_date, on_chunk, stats_handler)
    # 无 skip 路径
```

### 5.6 FastThink 图定义（fast_graph.py）

```python
"""tradingagents/graph/fast_graph.py — FastThink 轻量决策图"""
from langgraph.graph import StateGraph, START, END
from tradingagents.agents.utils.agent_states import AgentState


def build_fast_graph(quick_llm):
    """构建 FastThink 轻量图。

    流程：
      Analysts(quick_think_llm) → FastResearcher(quick_think_llm, text-only) → END

    跳过：Bull/Bear 辩论、Trader、Risk Debate、Portfolio Manager。
    输出：简版 complete_report_fast.md（含 rating + 理由，不含 entry/stop/size）。
    """
    workflow = StateGraph(AgentState)

    # 复用现有 analyst 工厂（接受任意 LLM 实例）
    # 节点名与现有 trading_graph.py 保持一致，避免 dashboard 匹配问题
    analyst_keys = ["market", "social", "news", "fundamentals", "governance", "industry"]
    analyst_factories = {
        "market": lambda: create_market_analyst(quick_llm),
        "social": lambda: create_sentiment_analyst(quick_llm),
        "news": lambda: create_news_analyst(quick_llm),
        "fundamentals": lambda: create_fundamentals_analyst(quick_llm),
        "governance": lambda: create_governance_analyst(quick_llm),
        "industry": lambda: create_industry_analyst(quick_llm),
    }

    # 添加节点：名称与现有 trading_graph.py 完全一致
    for key in analyst_keys:
        node_name = f"{key.title()} Analyst"
        workflow.add_node(node_name, analyst_factories[key]())
        workflow.add_node(f"Clear {key.title()}", create_msg_delete())

    workflow.add_node("FastResearcher", create_fast_researcher(quick_llm))

    # 边：串行 analyst 依次执行
    workflow.add_edge(START, "Market Analyst")
    for i, key in enumerate(analyst_keys):
        agent = f"{key.title()} Analyst"
        clear = f"Clear {key.title()}"
        if i < len(analyst_keys) - 1:
            next_agent = f"{analyst_keys[i+1].title()} Analyst"
        else:
            next_agent = "FastResearcher"
        workflow.add_edge(agent, clear)
        workflow.add_edge(clear, next_agent)

    workflow.add_edge("FastResearcher", END)
    return workflow.compile()
```

---

## 6. 分阶段实施计划

### Phase 0：Shadow Mode（必须，无风险）

**目标**：收集对照数据，校准阈值，**不做任何真实路由**。

**改动**：
- 新增 `tradingagents/graph/signal_prefilter.py`（Layer 1 预筛逻辑）
- 修改 `cli/batch_runner.py`：门控结果只写日志，所有票仍然走 DeepThink

**验证指标**：
1. 收集 2-4 周数据后回答：
   - 被 Layer 1 判定为 fast_think 的票，DeepThink 最终评级为非 Hold 的比例？
   - 被 Jev 判定为 fast_think（news<0.6 AND sector<0.6）的票，DeepThink 最终评级为非 Hold 的比例？
   - 被 Layer 1 判定为 deep_think 的票，DeepThink 评级的一致性？
2. 对比 FastThink vs DeepThink 在相同 ticker 上的评级一致性（抽样）

**不启用**：`jev_enabled=true` 在 Phase 0 无意义，Phase 0 只是打开日志收集。

### Phase 1：Jev 门控上线（有限风险）

**前提**：Phase 0 数据证明阈值合理（非 Hold 漏检率 < 5%）。

**改动**：
- 启用真实路由（skip 路径仍不启用）
- 新增 `tradingagents/llm_clients/typesafe_client.py`
- 修改 `tradingagents/default_config.py` — 新增配置项
- 修改 `cli/main.py` — 新增 `--jev-adaptive` CLI flag
- 修改 `cli/batch_runner.py` — 集成双层门控

**不改动**：现有 graph、agent、schema、所有测试。

**验证**：
- 对比有/无门控的输出一致性（DeepThink 票完全一致）
- FastThink 票的评级质量抽查（与历史 DeepThink 结果对比）
- 测量真实成本和时间节省
- 检查 batch_summary.json 中 `status` 字段正确写入

### Phase 2：阈值自校准（可选）

基于 Phase 1 累积的回测数据自动调整：
- 统计各阈值下的非 Hold 漏检率
- 统计 FastThink vs DeepThink 评级一致性
- 动态优化 `jev_deepthink_novel_threshold` / prefilter 数值阈值

---

## 7. 风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| Layer 1 数值规则误判（把值得 DeepThink 的平淡票判为 FastThink） | 漏掉潜在机会 | fail-open：Layer 1 只设 DeepThink 和 FastThink，不设 Skip；Jev 层再兜底 |
| Jev 误判：semantic noul 低但实际有利好 | 降级到 FastThink，损失结构化输出 | FastThink 仍有评级，不比 Skip 差；Phase 0 数据验证漏检率 |
| Jev 误判：semantic noul 高但实际无意义 | 多跑一次 DeepThink | 仅影响效率，不影响正确性 |
| TypeSafe API 不可用/超时 | 门控失败 | `jev_fail_open=true` 默认全量 DeepThink；异常时写 warning 日志 |
| market_data_snippets 为空（smartmoney_db 缺失） | Jev 在无数据状态下回答 | Layer 1 先检查数据可用性，数据缺失 → 直接 DeepThink（fail-open） |
| 路由非确定性破坏可复现性 | 审计困难 | gate_decision.json 落盘原始 noul 值，供事后审计 |
| FastThink 报告与 report_auditor 不兼容 | 审计误报 | FastThink 报告加 `[fast]` 前缀，auditor 跳过财务一致性检查 |
| 新增依赖 typesafe-sdk | 依赖管理复杂度 | optional dependency，`jev_enabled=false` 时完全不加载 |

---

## 8. 成本与收益预估

### 8.1 Jev 计费规则（官方 data）

| 项目 | 数值 |
|------|------|
| 输入价格 | $42 / Btok = $0.042 / Mtok |
| 输出价格 | 免费 |
| 上下文窗口 | 32k tokens |

### 8.2 Token 估算（24 只股票，假设 Layer 1 决策了 10 只，剩余 14 只进 Jev）

**State tokens**（14 只票的数值摘要 + 新闻关键词）：
```
per-ticker: ~80 tokens (daily_range, volume_ratio, sector, 3 headlines)
14 × 80 = 1,120 tokens
基础设施: ~300 tokens
State 合计: ~1,420 tokens
```

**Questions tokens**（14 × 2 = 28 个问题）：
```
per-question: ~130 tokens (包含 ticker 上下文引用)
28 × 130 = 3,640 tokens
```

**单次请求总计**：~5,060 input tokens

### 8.3 日成本计算

```
日成本 = 5,060 tokens × $0.042 / 1,000,000 = $0.000212 ≈ ¥0.0015
月成本（22个交易日）= $0.0047 ≈ ¥0.034
年成本（260天）= $0.055 ≈ ¥0.40
```

### 8.4 完整收益表（24 只股票，单 worker）

| 指标 | 现状（全 DeepThink） | Phase 1（Jev 门控） |
|------|---------------------|---------------------|
| DeepThink 数量 | 24 | ~14（Layer 1 直接 DeepThink: ~10，Jev 后 DeepThink: ~4） |
| FastThink 数量 | 0 | ~10（Layer 1 FastThink: ~4，Jev 后 FastThink: ~6） |
| Skip 数量 | 0 | 0（不设 Skip 路径） |
| Jev 调用次数 | 0 | 1 次（28 个问题批量） |
| Jev 成本 | $0 | ~$0.0002 |
| DeepThink LLM 成本 | ~$14.4 | ~$8.4 |
| FastThink LLM 成本 | $0 | ~$0.50-1.0 |
| **总成本** | **~$14.4** | **~$8.9** |
| DeepThink 耗时 | ~120 min | ~70 min |
| FastThink 耗时 | 0 | ~5 min |
| Jev 耗时 | 0 | <3s |
| **总时间** | **~120 min** | **~75 min** |
| **成本节省** | — | **38%** |
| **时间节省** | — | **37%** |

> 实际数字因 watchlist 质量、市场活跃度而异。建议 Phase 0 收集 2-4 周数据后校准。

---

## 9. 不在本方案范围内

以下改进**不属于本次方案**，可作为独立议题另行讨论：

1. **SignalProcessor 正则替换**：用 Jev Choice 替代 `signal_processing.py` 中的正则表达式（长期重构目标）
2. **置信度解析替换**：用 Jev Noul 替代 `parse_confidence()` 启发式正则（长期重构目标）
3. **Market Data Validator 语义增强**：用 Jev 补充纯数值规则的盲区（Phase 2 后考虑）
4. **真正 Skip 路径**：Phase 2 数据分析后，若 FastThink 价值确实有限，可增设 Skip 路径
5. **实时盘中门控**：盘中每分钟对持仓 ticker 做 Jev 评估决定是否 re-analyze（独立功能）

---

## 10. 决策问题（供评审参考）

1. **Phase 0 shadow mode 时长**：2 周 vs 4 周？取决于 batch run 频率（每天一次则 2 周 = 14 个样本点）。
2. **FastThink 输出形态**：简版 markdown 报告 vs 纯 JSON rating，哪种更适合下游（report_auditor / portfolio_backtest）？
3. **Jev 成本计入 batch cost**：$0.0002/次是否值得计入 batch_summary.json 的成本统计？（建议计入，便于完整成本归因）
4. **多层 fallback 策略**：当前设计 Layer 1 失败 → DeepThink，Jev 失败 → DeepThink。是否需要中间层（Jev 失败 → FastThink）？
5. **A 股特有场景**：涨跌停制度（10%/20%）、T+1 交易、盘后公告对 Jev 评估的影响是否需要 special prompt engineering？
6. **阈值敏感性**：`jev_deepthink_novel_threshold=0.6` 是否需要在不同市场环境（牛市/熊市/震荡市）下分别校准？

---

## 附录 A：相关文件索引

| 文件 | 职责 | 是否改动 |
|------|------|---------|
| `tradingagents/graph/trading_graph.py` | 完整 DeepThink 图编排 | 不改 |
| `tradingagents/graph/setup.py` | 图节点工厂组装 | 不改 |
| `tradingagents/graph/signal_prefilter.py` | Layer 1 代码数值预筛 + 路由聚合 | **新增** |
| `tradingagents/graph/fast_graph.py` | FastThink 轻量图定义 | **新增** |
| `tradingagents/agents/schemas.py` | ResearchPlan / TraderProposal / PortfolioDecision | 不改 |
| `tradingagents/agents/utils/structured.py` | bind_structured / invoke_structured_or_freetext | 不改 |
| `tradingagents/agents/fast_researcher.py` | FastThink 路径的轻量 Research 节点 | **新增** |
| `tradingagents/llm_clients/typesafe_client.py` | Jev 客户端封装 | **新增** |
| `tradingagents/default_config.py` | 默认配置 | 新增 jev_* 配置项 |
| `cli/batch_runner.py` | 批量执行编排 | 插入双层门控逻辑 |
| `cli/main.py` | CLI 入口 | 新增 --jev-adaptive flag |
| `scripts/report_auditor.py` | 报告审计 | 新增 FastThink 兼容逻辑 |
| `tests/` | 测试 | 新增门控和 FastThink 测试 |

## 附录 B：关键设计决策 rationale

| 决策 | 选项 A | 选项 B（选定） | 理由 |
|------|--------|---------------|------|
| Skip vs FastThink | 设 Skip 路径跳过平淡票 | 不设 Skip，全部走 FastThink | Phase 0 数据未支撑 Skip 的安全性；FastThink 仍产出有价值评级 |
| 聚合逻辑 | mean() 多维权重平均 | max() / 任一触发（非补偿性） | 符合 TypeSafe 官方指导："any serious violation" rule needs separate conditions |
| Jev 问题数 | 5 个维度 per ticker | 2 个语义维度 per ticker | 数值维度已由 Layer 1 代码处理，Jev 只负责纯语义 |
| 阈值初始值 | 拍脑袋 0.3/0.6 | Phase 0 shadow mode 校准 | 0.3/0.6 无数据支撑，Phase 0 收集 labeled outcomes 后再定 |
| 门控失败策略 | 全部 fail-close（不分析） | 全部 fail-open（全量 DeepThink） | 宁可多花钱不错过，符合交易场景风险偏好 |
| 可复现性 | 不记录原始 noul 值 | gate_decision.json 落盘 | 满足审计需求，便于事后回溯 |

## 实施进展

- 2026-09-22：新闻质量门控已按 `docs/jev_news_gate_design.md` 落地（最小第一步）。
  落地文件：`tradingagents/llm_clients/typesafe_client.py`（`TypeSafeNewsGate`）、
  `tradingagents/dataflows/news_gate.py`（`apply_news_gate`）、`tests/test_news_gate.py`（7 场景）。
  本文档 v0.1 的两处缺陷在该规格中已修正：(a) per-article 数据经结构化 `instructions.article`
  字段真实传入 `system_one()`，无"构建了但没传"的断链；(b) 未采用 mean 聚合，
  阈值分流为单题二值判断（≥ threshold 保留），无信号稀释。数值维度（振幅/量比）未送 Jev。
- ticker 级 skip / FastThink / DeepThink 路由（Phase 0/1/2）**已放弃，不再实施**。
  原计划的 Jev 架构级门控最终只保留了新闻端质量门控这一步；
  后续 Jev 相关设计见 `docs/jev_news_gate_design.md`（已落地）与
  `docs/jev_announcement_gate_design.md`（待评审，尚未开工）。
