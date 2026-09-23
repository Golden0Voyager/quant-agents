# Jev 新闻质量门控（News Quality Gate）施工规格

> 版本：v1.0
> 日期：2026-09-22
> 状态：待开发（开发交由第三方，完成后按 §11 审计检查清单验收）
> 上游文档：`docs/jev_adaptive_gate_design.md`（本规格是其收敛后的最小第一步）

---

## 1. 背景与目标

`jev_adaptive_gate_design.md` 提出了"Jev 门控 → skip / FastThink / DeepThink 三级路由"的完整方案。评审后发现两处缺陷：**(a) 示例代码构建了 per-ticker 行情数据但从未传给模型**，门控实际上在无数据状态下做判断；**(b) 用 5 维 Noul 取 mean 聚合**，会稀释"单维度极强"的信号（如突发重组），恰好漏掉最值得分析的票。

本规格只做原方案中最小、最稳、最符合 Jev 定位的一步：**新闻质量门控**。理由：

- 新闻重要性是**纯语义判断**，代码算不了——这正是 System One 模型的价值区；
- 数值类判断（振幅、量比）留在代码里做，不外包给概率模型；
- 不涉及 ticker 级路由，不改变"哪些票跑 pipeline"的行为，爆炸半径最小。

**目标**：在新闻文本进入 News / Sentiment Analyst 之前，用 Jev 对每条新闻判断"是否包含对投资决策有实质影响的信息"，把低信息密度的新闻降级为"仅列标题"，让分析师的注意力集中在有信息量的新闻上。

---

## 2. 总体设计

### 2.1 数据流

```
akshare stock_news_em (DataFrame)
    ↓ 日期过滤（akshare_vendor.py:466-468）
    ↓ 公司名相关性拆分（akshare_vendor.py:487-496，不点名的进 unrelated_rows）
    ↓
【Jev Gate】← 本规格新增，挂在 :496 之后、:504 markdown 拼装之前
    ↓
kept 文章 → 全文渲染（标题+正文+时间+链接）
demoted 文章 → 新增的"低信息密度"小节，仅列标题
unrelated_rows → 既有"疑为代码碰撞"小节（不变）
```

yfinance 路径同理，挂载点见 §5.3。

### 2.2 为什么挂在 vendor 层

- News Analyst（工具调用型，`news_analyst.py`）和 Sentiment Analyst（prefetch 型，`sentiment_analyst.py:84`）消费的是**同一份 `get_news` 输出文本**。vendor 层过滤对两者一致生效，不会出现两个分析师看到不同新闻集的分裂状态。
- 符合 `tradingagents/agents/AGENTS.md` 契约："数据获取一律经 `utils/*_tools.py` → `tradingagents/dataflows/`，角色节点不直连数据源"。analyst 节点零改动。

### 2.3 降级（demote），不删除

低分文章移入独立的 title-only 小节，标题仍然可见。信息不丢失、行为可逆，与既有 `unrelated_rows` 模式（`akshare_vendor.py:520-534`）一致。渲染文案必须明确标注是"Jev 门控判定"，与"疑为代码碰撞"小节区分。

### 2.4 失败全开（fail-open）

门控在任何异常情况下必须**原样返回全部文章**，绝不抛异常。特别注意：`route_to_vendor` 会把 vendor 异常当作失败并触发 fallback 链（`interface.py:1447-1464`），门控若抛异常会污染这个语义（把"门控故障"变成"数据缺失"）。所有异常在门控内部消化，记 warning 日志。

### 2.5 Shadow 模式先行

默认 `jev_news_gate_shadow=true`：门控正常运行、结果写日志和 JSONL，但**不实际降级**。跑若干次 batch 人工核对降级合理性后，再置 false 正式生效。

---

## 3. Jev 调用契约

### 3.1 接入方式

**用 `requests` 直连 HTTP API，不安装 typesafe-sdk**。`requests>=2.32.4` 已是项目依赖（`pyproject.toml:23`），零新增依赖。

```
POST https://api.typesafe.ai/v1/systemone
Headers: Authorization: Bearer <TYPESAFE_API_KEY>, Content-Type: application/json
Timeout: jev_news_gate_timeout（默认 10s）
```

### 3.2 请求结构

```json
{
  "model": "jev-latest",
  "state": {
    "ticker": "300454.SZ",
    "company_name": "深信服",
    "date_range": "2026-09-15 to 2026-09-22"
  },
  "questions": {
    "article_0": {
      "type": "noul",
      "instructions": {
        "article": {
          "title": "...",
          "body": "...(截断至 500 字)",
          "publisher": "...",
          "pub_date": "..."
        },
        "question": "Does this article contain material, decision-relevant information about `state.company_name`'s business, stock, or industry that an investor should not miss?"
      },
      "criteria": {
        "true": "Earnings or guidance changes, major contracts or orders, M&A, regulatory actions, management changes, significant industry policy directly affecting the company, competitive developments that describe the company's own strategic or market position (including rivals' moves in markets where the company competes), or substantive analysis with new company-specific information",
        "false": "The company is mentioned but the article provides no new decision-relevant information: routine fund-flow data recaps, shareholder-count updates, margin-trading table entries, or generic market or sector roundups where the company appears only as a name in a list or a data table, with no description of its strategy, competitive position, or business developments"
      }
    },
    "article_1": { "...": "同上结构" }
  }
}
```

要点：

- **每篇文章一个 Noul**，一次请求批量发送（questions 并行评估，增加问题数几乎不增加延迟）。
- `instructions` 用结构化 object：`article` 字段放数据，`question` 字段放固定问题。这保证了 per-article 数据**真实进入请求**（修复原方案"state 构建了但没传"的断链缺陷）。
- `criteria.false` 的措辞是有意的：`akshare_vendor.py:494` 的相关性拆分只按公司名是否出现过滤，正文数据表格里点了名的榜单快讯（资金流/两融榜常含公司名）会**通过**名称匹配——gate 的 false 标准补的正是这部分漏网之鱼，不是重复劳动。
- **criteria 措辞修订**（2026-09-22）：初版 false 条款结尾是"generic sector commentary where the company is only one of many examples"。实测发现一条竞争情报被误降级：

  > 标题：`64亿元战投世纪互联交割：宁德时代"入"而不"主"`（0.150 降级）
  > 正文：`近期，比亚迪（002594.SZ）与亿纬锂能（300014.SZ）等对手正在储能赛道加速扩张。将储能产品嵌入数据中心场景，既能为宁德时代开辟下游客户...`

  正文明确描述了比亚迪在储能赛道的**竞争定位**，是分析师需要的竞争情报，却被"one of many examples"套住。修订后的边界是：**点名描述公司自身战略/竞争定位 → true；仅作为名单或数据表格中的名字出现 → false**。true 条款同步补充"competitive developments that describe the company's own strategic or market position (including rivals' moves in markets where the company competes)"。
- body 截断到 500 字控制 token；标题必须完整。

### 3.3 响应结构

```json
{
  "model": "jev-1.13.0",
  "answers": {
    "article_0": { "type": "noul", "noul": 0.92 },
    "article_1": { "type": "noul", "noul": 0.15 }
  },
  "usage": { "input_tokens": 6500, "output_tokens": 120 }
}
```

`noul ∈ [0,1]`：接近 1 = 强烈"是"（有实质信息），接近 0 = 强烈"否"，接近 0.5 = 模型不确定（**不是**"中等重要"）。代码用阈值 `jev_news_gate_threshold`（默认 0.5）分流：≥ 阈值 kept，< 阈值 demoted。

### 3.4 Token 预算

30 条（`jev_news_gate_max_articles` 上限）× ~200 tokens/条（instructions + criteria 摊销）+ state ≈ 6-7k input tokens，约 $0.001-0.005/票/次。相对完整 pipeline 的 $0.40-0.80 可忽略。超出 30 条的部分不送判，直接按 kept 处理。

---

## 4. 配置项

`tradingagents/default_config.py`：

`_BASE_CONFIG` 新增（注释风格与现有一致）：

```python
# ── Jev 新闻质量门控 ─────────────────────────────────
# 用 TypeSafe Jev（System One）对个股新闻做语义重要性判断，
# 低信息密度文章降级为仅列标题。默认关闭；开启后默认 shadow
# 模式（只记录不降级）。详见 docs/jev_news_gate_design.md
"jev_news_gate_enabled": False,        # 总开关
"jev_news_gate_shadow": True,          # shadow 模式：只记录不降级
"jev_api_key_env": "TYPESAFE_API_KEY", # API Key 环境变量名
"jev_model": "jev-latest",             # Jev 模型版本
"jev_news_gate_threshold": 0.5,        # noul ≥ 此值保留全文，否则降级
"jev_news_gate_keep_floor": 2,         # 相关文章 ≤ 此数不过滤（见下注）
"jev_news_gate_max_articles": 30,      # 单次最多送判条数，超出部分直接保留
"jev_news_gate_timeout": 10,           # HTTP 超时（秒）
```

`keep_floor` 取 2 的权衡（2026-09-22 由 5 下调）：初版取 5 的理由是"只剩 3-4 条时每条都珍贵、
误降级代价高，且 prompt 本来就不长"。**shadow 实测数据推翻了后半句**——小样本票的垃圾率反而
更高：

| 分组 | 条数 | 降级 | 垃圾率 |
|------|------|------|--------|
| 大样本（比亚迪 7 条） | 7 | 3 | 43% |
| 小样本（≤4 条，9 只票） | 16 | 12 | **75%** |

山金国际 4 条全是"三大指数收跌"，瑞芯微 3 条全是资金流榜单。`keep_floor=5` 恰好排除了垃圾率
最高的那批票。下调到 2（触发门槛 ≥3 条）后，覆盖率从 13% 提升到 43%，同时避开"只有 1-2 条时
误降级代价高"的风险。

各取值实测效果（23 条新闻，10 只票）：

| keep_floor | 触发条件 | 实际降级 | 占总量 |
|-----------|---------|---------|--------|
| 5（初版） | ≥6 条 | 3 条 | 13% |
| 3 | ≥4 条 | 7 条 | 30% |
| **2（现行）** | ≥3 条 | 10 条 | 43% |
| 0 | ≥1 条 | 15 条 | 65% |

`_ENV_OVERRIDES`（`:11-33`）新增映射：

```python
"TRADINGAGENTS_JEV_NEWS_GATE_ENABLED":   "jev_news_gate_enabled",
"TRADINGAGENTS_JEV_NEWS_GATE_SHADOW":    "jev_news_gate_shadow",
"TRADINGAGENTS_JEV_NEWS_GATE_THRESHOLD": "jev_news_gate_threshold",
"TRADINGAGENTS_JEV_MODEL":               "jev_model",
```

---

## 5. 代码改动清单

### 5.1 新增 `tradingagents/llm_clients/typesafe_client.py`

```python
"""TypeSafe Jev（System One）薄客户端，requests 直连，不依赖 typesafe-sdk。"""
import logging
import os

import requests

logger = logging.getLogger(__name__)

_API_URL = "https://api.typesafe.ai/v1/systemone"
_BODY_TRUNC = 500


class TypeSafeNewsGate:
    """新闻语义重要性评估。所有失败路径返回 None，由调用方 fail-open。"""

    def __init__(self, config: dict):
        self._api_key = os.getenv(config.get("jev_api_key_env", "TYPESAFE_API_KEY"), "")
        self._model = config.get("jev_model", "jev-latest")
        self._timeout = config.get("jev_news_gate_timeout", 10)
        self._max_articles = config.get("jev_news_gate_max_articles", 30)

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    def score_articles(
        self,
        articles: list[dict],   # {"title","body","publisher","pub_date","link"}
        context: dict,          # {"ticker","company_name","date_range"}
    ) -> list[float] | None:
        """返回与 articles 等长的 noul 概率列表；任何失败返回 None。"""
        if not self.available or not articles:
            return None
        judged = articles[: self._max_articles]
        questions = {
            f"article_{i}": {
                "type": "noul",
                "instructions": {
                    "article": {
                        "title": a["title"],
                        "body": a["body"][:_BODY_TRUNC],
                        "publisher": a["publisher"],
                        "pub_date": a["pub_date"],
                    },
                    "question": _QUESTION,
                },
                "criteria": {"true": _CRITERIA_TRUE, "false": _CRITERIA_FALSE},
            }
            for i, a in enumerate(judged)
        }
        try:
            resp = requests.post(
                _API_URL,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={"model": self._model, "state": context, "questions": questions},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            answers = resp.json()["answers"]
            scores = [answers[f"article_{i}"]["noul"] for i in range(len(judged))]
        except Exception as exc:  # noqa: BLE001 — 门控故障绝不外抛
            logger.warning("Jev news gate scoring failed: %s", exc)
            return None
        # 超出 max_articles 的部分不送判，按满分处理（直接保留）
        scores.extend([1.0] * (len(articles) - len(judged)))
        return scores
```

`_QUESTION` / `_CRITERIA_TRUE` / `_CRITERIA_FALSE` 用 §3.2 的全文，定义为模块级常量。

### 5.2 新增 `tradingagents/dataflows/news_gate.py`

纯逻辑模块，不 import akshare/yfinance 任何符号，便于单测：

```python
"""Jev 新闻质量门控。所有路径 fail-open：返回 None 表示"不过滤"。"""
import json
import logging
import os
from datetime import datetime, timezone

from tradingagents.dataflows.config import get_config
from tradingagents.llm_clients.typesafe_client import TypeSafeNewsGate

logger = logging.getLogger(__name__)


def _akshare_row_to_article(row) -> dict:
    """akshare stock_news_em 行 → 统一 article dict。列名见 akshare_vendor.py:504-518。"""
    return {
        "title": str(row.get("新闻标题", "")),
        "body": str(row.get("新闻内容", "")),
        "publisher": str(row.get("文章来源", "")),
        "pub_date": str(row.get("发布时间", "")),
        "link": str(row.get("新闻链接", "")),
    }


def apply_news_gate(
    articles: list[dict],
    symbol: str,
    company_name: str,
    date_range: str = "",
) -> tuple[list[int], list[int]] | None:
    """返回 (kept_indices, demoted_indices)；None 表示不过滤（fail-open）。"""
    config = get_config()
    if not config.get("jev_news_gate_enabled"):
        return None
    if len(articles) <= config.get("jev_news_gate_keep_floor", 5):
        return None

    gate = TypeSafeNewsGate(config)
    scores = gate.score_articles(
        articles,
        {"ticker": symbol, "company_name": company_name, "date_range": date_range},
    )
    if scores is None:
        return None

    threshold = config.get("jev_news_gate_threshold", 0.5)
    kept = [i for i, s in enumerate(scores) if s >= threshold]
    demoted = [i for i, s in enumerate(scores) if s < threshold]
    _log_decision(config, symbol, articles, scores, kept, demoted,
                  shadow=config.get("jev_news_gate_shadow", True))

    if config.get("jev_news_gate_shadow", True):
        logger.info("Jev gate (shadow) %s: would demote %d/%d articles",
                    symbol, len(demoted), len(articles))
        return None
    return kept, demoted


def _log_decision(config, symbol, articles, scores, kept, demoted, shadow):
    """决策落盘 JSONL，shadow/正式模式都写，供审计与阈值校准。"""
    path = os.path.join(config.get("data_cache_dir", "."), "jev_gate_decisions.jsonl")
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "shadow": shadow,
        "articles": [
            {"title": a["title"], "score": s, "kept": i in kept}
            for i, (a, s) in enumerate(zip(articles, scores))
        ],
    }
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.warning("Jev gate decision log write failed: %s", exc)
```

### 5.3 修改 `tradingagents/dataflows/akshare_vendor.py`（`get_news`）

在 `:496`（相关性拆分完成）之后、`:504`（markdown 拼装）之前插入：

```python
    from tradingagents.dataflows import news_gate

    articles = [news_gate._akshare_row_to_article(row) for _, row in df.iterrows()]
    gate_result = news_gate.apply_news_gate(
        articles, symbol, company_name,
        date_range=f"{start_date} to {end_date}",
    )
    demoted_rows = []
    if gate_result:
        kept_idx, demoted_idx = gate_result
        demoted_rows = [list(df.iterrows())[i] for i in demoted_idx]
        df = df.iloc[kept_idx]
```

渲染段（`:504-534`）在 `unrelated_rows` 小节之后增加：

```python
    if demoted_rows:
        lines.append(
            f"### 🤖 以下 {len(demoted_rows)} 条新闻经 Jev 门控判定信息密度较低"
            "（例行数据回顾/股东户数更新/泛泛行业点评等），仅列标题供参考："
        )
        for _, row in demoted_rows:
            lines.append(f"- {row.get('新闻标题', 'N/A')} ({row.get('发布时间', 'N/A')})")
        lines.append("")
```

注意：gate 后 `df` 为空**不抛错**——沿用 `:498-502` 既有先例（零直接命中降级为标题区而非抛 `NoMarketDataError` 触发 fallback）。

### 5.4 修改 `tradingagents/dataflows/yfinance_news.py`（`get_news_yfinance`）

⚠️ **挂载点不能在 `:92`**（`article_limit` 在日期过滤之前，会把窗口外文章送去评估）。正确做法：把 `:112-125` 的渲染循环改为先收集、后渲染：

```python
        filtered: list[dict] = []
        for article in news:
            data = _extract_article_data(article)
            if not _in_news_window(data["pub_date"], start_dt, end_dt):
                continue
            filtered.append(data)

        if not filtered:
            return f"No news found for {ticker}{resolved} between {start_date} and {end_date}"

        from tradingagents.dataflows import news_gate
        gate_result = news_gate.apply_news_gate(
            filtered, ticker, company_name="",   # yfinance 无中文名解析，context 从简
            date_range=f"{start_date} to {end_date}",
        )
        kept_data, demoted_data = filtered, []
        if gate_result:
            kept_idx, demoted_idx = gate_result
            kept_data = [filtered[i] for i in kept_idx]
            demoted_data = [filtered[i] for i in demoted_idx]

        news_str = ""
        for data in kept_data:
            news_str += f"### {data['title']} (source: {data['publisher']})\n"
            # ... 原有 summary/link 渲染不变 ...
        if demoted_data:
            news_str += f"### 🤖 {len(demoted_data)} articles demoted by Jev gate (low information density), titles only:\n"
            for data in demoted_data:
                news_str += f"- {data['title']} ({data['pub_date']})\n"
```

### 5.5 修改 `tradingagents/default_config.py`

见 §4。

### 5.6 新增 `tests/test_news_gate.py`

7 个场景，全部不依赖网络（mock `TypeSafeNewsGate.score_articles`；vendor 集成路径另 mock `ak.stock_news_em`）：

| # | 场景 | 断言 |
|---|------|------|
| 1 | `jev_news_gate_enabled=False` | 返回 None，不发起任何 HTTP |
| 2 | 文章数 ≤ keep_floor | 返回 None |
| 3 | mock 分数 [0.9, 0.2, 0.8] | kept=[0,2], demoted=[1] |
| 4 | `score_articles` 返回 None（模拟 API 故障） | 返回 None，不抛异常 |
| 5 | shadow=True | 返回 None 但写了 JSONL |
| 6 | akshare 集成：mock `ak.stock_news_em` + mock 分数 | demoted 文章出现在 🤖 标题区、不出现在正文区 |
| 7 | JSONL 决策日志 | 记录含 ts/symbol/shadow/每条 title+score+kept |

### 5.7 文档同步

- `tradingagents/dataflows/AGENTS.md`：边界节加一行 `news_gate.py` 说明；
- 根 `AGENTS.md` Data Vendors 段加一句 Jev 门控；
- `docs/jev_adaptive_gate_design.md` 文末加"实施进展"注记：新闻门控已按本规格落地，并注明原方案的 state 断链 / mean 聚合两处缺陷在此规格中已修正。

---

## 6. 行为规约汇总（fail-open 全路径）

| 条件 | 行为 |
|------|------|
| `jev_news_gate_enabled=False`（默认） | 完全现状，零开销 |
| 未配置 API key | 返回 None，记 warning，行为同现状 |
| HTTP 超时 / 非 200 / JSON 解析失败 / answers 缺键 | 返回 None，记 warning |
| 相关文章数 ≤ keep_floor(5)，且 shadow=False | 返回 None（省 API 调用） |
| shadow=True（默认） | **绕过 keep_floor**，评分 + 写日志 + 写 JSONL，但**不降级** |
| 文章数 > max_articles(30) | 前 30 条送判，超出部分直接保留 |
| demote 后 kept 为空 | 正常渲染 demoted 标题区，不抛错、不触发 vendor fallback |

> **shadow 绕过 keep_floor 的理由**（2026-09-22 修订）：keep_floor 的作用是省 API 调用，
> 但 shadow 模式的目的恰恰是收集**小样本票**的分数分布来校准这个阈值。若 shadow 也受
> keep_floor 约束，则 23 只票里只有 1 只会留下日志，永远攒不够决策数据——形成
> "没数据 → 无法定阈值 → 阈值继续挡数据"的死循环。实测：keep_floor=5 时 8 只有新闻的
> 票里只有比亚迪（7 条）触发，其余 7 只（1-4 条）全部静默跳过。

---

## 7. 验证命令

```bash
uv run python -m pytest tests/test_news_gate.py -m unit         # 新测试
uv run python -m pytest -m unit -k "news or vendor or akshare"  # 回归
uv run python -m pytest -m unit                                 # 全量单测
```

手动 shadow 验证（需真实 `TYPESAFE_API_KEY`）：

```bash
TRADINGAGENTS_JEV_NEWS_GATE_ENABLED=true uv run python -m cli.main batch my-list
# 检查 <data_cache_dir>/jev_gate_decisions.jsonl 中被判低分的标题是否合理
```

---

## 8. 出界事项（本轮不做）

1. ticker 级 skip / FastThink / DeepThink 路由（原方案 Phase 1/2，待新闻门控验证后再议）；
2. 安装 typesafe-sdk；
3. 修改 `interface.py` 路由链、任何 analyst 节点、sentiment 的 prefetch 结构；
4. `get_global_news` / `get_cailianpress_telegrams` 接门控（宏观新闻语义不同，问题措辞需单独调校）；
5. 阈值自动校准（先人工看 JSONL，攒数据后再议）。

---

## 9. 风险与缓解

| 风险 | 缓解 |
|------|------|
| Jev 对中文 A 股新闻校准未验证 | shadow 先行 + keep_floor + 降级不删除三重缓解；阈值 0.5 待 JSONL 数据校准 |
| 门控延迟拖慢 pipeline | 超时 10s 硬上限；相对 2-5 分钟的 pipeline 可忽略 |
| TypeSafe API 故障 | fail-open 返回 None，行为退化为现状 |
| 审计不可追溯 | 每次决策（含 shadow）写 JSONL，含每篇文章的分数 |

---

## 10. 与原方案的关系

本规格落地后，原 `jev_adaptive_gate_design.md` 的后续阶段若要推进，必须先修正其两处缺陷：(a) §3.4 `typesafe_client.py` 的 per-ticker state 未传入 `system_one()`（本规格 §3.2 的结构化 instructions 是正确范式）；(b) §2.4 的 mean 聚合改为"任一维度超阈值即升级"的非补偿规则。数值维度（振幅/量比）应改为代码计算，不送 Jev。

---

## 11. 审计检查清单（验收标准）

开发完成后逐条核对：

- [ ] gate 挂载点在日期过滤/相关性拆分**之后**：akshare 在相关性拆分后、markdown 拼装前；yfinance 在 `_in_news_window` 过滤循环后。**不**在 `yfinance_news.py:92` 的 `article_limit` 处
- [ ] per-article 数据真实进入请求体（结构化 instructions 的 `article` 字段），不存在"构建了但没传"的断链
- [ ] §6 行为规约表全部 7 行逐条验证通过
- [ ] demoted 文章仍可见（标题列表），无信息丢失；demote 后为空不抛 `NoMarketDataError`
- [ ] JSONL 决策日志在 shadow 和正式模式都落盘，格式含 ts/symbol/shadow/每篇 title+score+kept
- [ ] 单测不依赖网络（mock `score_articles` 和 `ak.stock_news_em`），7 个场景全过
- [ ] `TRADINGAGENTS_JEV_*` env 覆盖生效；默认配置（门控关闭）下输出与现状逐字节一致
- [ ] 未越界：未改 `interface.py` / analyst 节点 / `pyproject.toml` 依赖；`get_global_news`、`get_cailianpress_telegrams` 未接门控
- [ ] `uv run python -m pytest -m unit` 全量通过
