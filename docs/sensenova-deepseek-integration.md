#商汤 SenseNova Token Plan 集成指南

> **适用场景**：在 TradingAgents 框架中使用商汤 SenseNova Token Plan 提供的模型（sensenova-6.8-flash-lite / deepseek-flash）。
> **文档性质**：实战指南 + 最佳实践。

---

## 1. SenseNova Token Plan 平台简介

商汤科技通过 SenseNova Token Plan 提供 OpenAI 兼容 API，无需翻墙即可访问。

**API 端点**：`https://token.sensenova.cn/v1`

### 1.1 可用模型清单

| 模型名称 | Model ID | 上下文长度 | 描述 |
|---------|---------|-----------|------|
| SenseNova 6.8 Flash-Lite | `sensenova-6.8-flash-lite` | 256K | 轻量多模态智能体模型，支持文本对话与图像输入 |
| DeepSeek V4.1 Flash | `deepseek-flash` | 1M | 最新一代高效通用模型（2026-09 上线 Token Plan），支持思考/非思考模式、工具调用；旧 `deepseek-v4-flash` / `deepseek-v4-pro` 均下线并重定向至此 |
| GLM-5.2 | `glm-5.2` | 1M | 智谱旗舰开源模型，长程 Coding / 复杂工程任务 |
| Kimi K3 | `kimi-k3` | 1M | 月之暗面旗舰开源多模态 Agent 模型 |

### 1.2 积分规则（2026-08-28 起，公测）

配额按**积分（token 实际用量）**计量，不是按调用次数。账户有两类积分池：

| 积分池 | 适用范围 | 滚动 5 小时额度 | 滚动周额度 |
|--------|---------|----------------|-----------|
| 通用积分 | 所有已开放模型 | 60,000 积分 | 600,000 积分 |
| Flash-Lite 专属积分 | 仅 Flash-Lite 系列 | 60,000 积分 | 600,000 积分 |

- 使用 Flash-Lite 时**优先扣专属积分**，专属不足后再扣通用积分（通用部分不参与返赠）；其他模型只扣通用积分
- 不同模型按实际用量扣除不同积分，具体费率以账户"积分明细"为准
- **Flash-Lite 消费返赠**（2026-08-28 起）：每消耗 1 专属积分返赠 1 通用积分，按自然日汇总、每小时结算到账，返赠自到账起 30 天有效且**不占用**滚动 5 小时/周额度——Flash-Lite 用量实质上是"负成本"的

**影响**：并行运行的 researcher agent 容易在 5 小时窗口内耗尽通用积分，建议：
- 研究深度（debate rounds）不要设太高
- deep 角色优先用 `deepseek-flash`（思考链短、积分消耗低），而非 GLM-5.2 / V4 Pro / K3
- 能用 Flash-Lite 的角色尽量用 Flash-Lite（烧专属积分 = 返赠通用积分）
- batch 均匀贴着滚动 5 小时窗口跑，避免集中爆发

**客户端限流（按模型 pacing）**：框架按 `llm_requests_per_minute` 配置做进程级 pacing，
键可以是 `"provider/model"`（模型级条目优先于裸 provider 条目）。默认值
（flash-lite 5 rpm、deepseek-flash 1.7 rpm、兜底 5 rpm）是**保守的调用速率
pacer**，并非积分配额的直接换算——积分按 token 计量，精确的窗口预算需要根据账户
"积分明细"里的实际费率折算。配额耗尽类错误（quota exceeded / insufficient
balance）不会在同档重试，直接进入 fallback 链的下一个 provider/model。

**请求超时**：`llm_request_timeout`（默认 600 秒，可用
`TRADINGAGENTS_LLM_REQUEST_TIMEOUT` 覆盖）为每次 LLM 请求设 HTTP 超时，
避免半开连接导致运行无限期挂起；超时后走重试/fallback。

---

## 2. 快速接入

### 2.1 获取 API Key

1. 访问 [SenseNova Token Plan 控制台](https://platform.sensenova.cn/)
2. 进入"API 密钥管理"创建 Key
3. 格式：`sk-xxxxxxxxxxxxxxxx`

### 2.2 环境变量配置

```bash
export SENSENOVA_API_KEY="sk-your-key-here"
```

或在项目根目录创建 `.env`：

```bash
cp .env.example .env
# 编辑 .env，填入 SENSENOVA_API_KEY
```

### 2.3 CLI 中选择商汤

```bash
uv run tradingagents
```

在 LLM Provider 选择界面中选择 **"商汤 SenseNova"**，随后选择 quick/deep 模型即可。

### 2.4 Python API 调用

```python
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG

config = DEFAULT_CONFIG.copy()
config["llm_provider"] = "sensenova"
config["quick_think_llm"] = "sensenova-6.8-flash-lite"   # 通用任务（无 reasoning）
config["deep_think_llm"] = "deepseek-flash"               # 推理任务（trader/manager）

ta = TradingAgentsGraph(debug=True, config=config)
_, decision = ta.propagate("600901.SS", "2026-05-09")
print(decision)
```

---

## 3. 技术集成要点

### 3.1 OpenAI 兼容层

SenseNova Token Plan 使用 OpenAI 兼容协议，因此在框架中通过 `ChatOpenAI` 接入即可。

关键配置（位于 `tradingagents/llm_clients/openai_client.py`）：

```python
_PROVIDER_CONFIG = {
    # ... 其他提供商 ...
    "sensenova": ("https://token.sensenova.cn/v1", "SENSENOVA_API_KEY"),
}
```

### 3.2 客户端类路由（核心差异）

两个模型对 `reasoning_content` 的支持不同，框架已自动区分：

| 模型 | 是否返回 reasoning_content | 使用的客户端类 |
|------|--------------------------|--------------|
| `sensenova-6.8-flash-lite` | ❌ 否（OpenAI 兼容接口） | `NormalizedChatOpenAI` |
| `deepseek-flash` | ✅ 是（支持 reasoning_effort） | `DeepSeekChatOpenAI` |

`DeepSeekChatOpenAI` 实现了 reasoning_content 的 sidecar 缓存机制，确保多轮对话中 thinking-mode 的往返正确。

### 3.3 推理力度控制

`deepseek-flash` 支持 `reasoning_effort` 参数：

| 值 | 说明 |
|----|------|
| `"none"` | 关闭思考模式 |
| `"low"` | 轻量推理 |
| `"medium"` | 默认值 |
| `"high"` | 深度推理（适合 trader/manager） |

可通过 `reasoning_effort` 参数传入：

```python
config["deep_think_llm_kwargs"] = {"reasoning_effort": "high"}
```

### 3.4 Structured Output

`deepseek-flash` 支持 `tool_choice`，因此可以使用 function-calling 做结构化输出。
`sensenova-6.8-flash-lite` 也支持工具调用，框架中的 `structured.py` 会自动处理。

---

## 4. 模型分配建议

| Agent 角色 | 推荐模型 | 理由 |
|-----------|---------|------|
| **Analyst** (并行) | `sensenova-6.8-flash-lite` | 轻量快速，256K 上下文；烧专属积分还能 1:1 返赠通用积分 |
| **Bull/Bear 研究员、风险辩论员** | `sensenova-6.8-flash-lite` | 立场文/辩论产出占 deep 角色 token 量 90%+，对抗结构容错好；下放后返赠收益最大 |
| **Research Manager** | `deepseek-flash` | 全线判断密度最高的仲裁节点（评级校准），不建议用轻量模型 |
| **Trader** | `deepseek-flash` | 需要强推理能力做交易决策，思考链短、积分消耗低 |
| **Portfolio Manager** | `deepseek-flash` | 需要强推理能力做风险评估 |

**注意**：积分按 token 实际用量扣减（见 1.2），`glm-5.2` / `deepseek-v4-pro` / `kimi-k3`
的费率通常更高，免费策略下不建议放进日常角色。

**按角色换模型**：`deep_think_llm_roles` 支持
`research_manager` / `trader` / `portfolio_manager` 三个决策角色以及
`bull_researcher` / `bear_researcher` / `aggressive_debater` / `neutral_debater` /
`conservative_debater` 五个辩论角色；未列出的角色共用 `deep_think_llm` 基准链，
每个 override 角色自动带完整 fallback 链。batch 配置文件（如 `config/daily-my.json`）
里同样生效。例如只下放风险辩论员：

```json
"deep_think_llm_roles": {
  "aggressive_debater": "sensenova-6.8-flash-lite",
  "neutral_debater": "sensenova-6.8-flash-lite",
  "conservative_debater": "sensenova-6.8-flash-lite"
}
```

更换关键角色后建议用 `scripts/rating_backtest.py` 对比评级准确率基线，确认无回归。

---

## 5. 常见问题排查

### 5.1 404 Not Found

**症状**：请求返回 `not_found_error`

**根因**：Base URL 配置错误

**解决**：确保 `openai_client.py` 中 sensenova 的 base_url 为 `https://token.sensenova.cn/v1`（不是旧的 `api.sensenova.cn/compatible-mode/v2`）

### 5.2 429 Quota Exceeded

**症状**：频繁遇到 `RateLimitError` 或 `quota_exceeded_error`

**解决**：
- 降低 `max_debate_rounds`（建议 ≤ 2）
- Analyst 使用 `sensenova-6.8-flash-lite`（专属积分池），避免占用通用积分
- 避免同时运行多个 ticker 的分析
- 滚动 5 小时窗口随时间自然恢复，无需等整点
- 框架会自动走 fallback 链（ModelScope / OpenRouter 免费档），配额耗尽错误不再同档重试

### 5.3 中文公司名称幻觉

**症状**：Market Analyst 报告标题写成错误的股票名称

**根因**：Prompt 中只有 ticker 代码，模型自行推断中文名称

**解决**：使用 `ticker_resolver.py` 预先通过 yfinance 获取 `longName`，注入到所有 agent 的 prompt 中。

---

## 6. 最佳实践清单

- [ ] 确保 `.env` 中 `SENSENOVA_API_KEY` 已配置
- [ ] quick_think 用 `sensenova-6.8-flash-lite`，deep_think 用 `deepseek-flash`
- [ ] 在账户"积分明细"中核对各模型实际费率，规划每个 5 小时窗口的 batch 量
- [ ] 监控额度使用情况，避免滚动 5 小时/周窗口内超限
- [ ] 中文 A 股场景下，配合 `ticker_resolver.py` 使用
- [ ] 如需图像输入，`sensenova-6.8-flash-lite` 支持 `image_url` 类型的 content 块

---

## 7. 参考链接

- [SenseNova Token Plan 文档](https://platform.sensenova.cn/docs)
- 本项目代码：
  - `tradingagents/llm_clients/openai_client.py` — 客户端实现
  - `tradingagents/llm_clients/model_catalog.py` — 模型列表
  - `tradingagents/ticker_resolver.py` — A 股解析器（辅助功能）
