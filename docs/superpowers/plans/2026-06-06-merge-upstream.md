# quant_agents 上游合并与功能增强报告

**日期**: 2026-06-06
**作者**: Claude Code (Haining)
**上游来源**: TauricResearch/TradingAgents main (53 commits)

---

## 1. 工作概述

本次工程完成了以下核心任务：

1. **完整合并上游** 53 个新提交
2. **解决 28 个文件冲突**，保留全部 A-share 定制
3. **修复 35 个测试失败** → 432 测试全通
4. **修复 CLI bug**（timestamp NameError）
5. **新增 3 个 LLM Provider**（Agnes AI、ModelScope、NVIDIA NIM）
6. **成功执行端到端测试**（平安银行 000001.SZ）

---

## 2. 上游合并详情

### 2.1 合并范围

```bash
# 合并前状态
Local:  202 commits ahead of upstream
Upstream: 53 new commits to merge

# 合并命令
git merge upstream/main
```

### 2.2 上游带来的新功能

| 功能 | 说明 | 影响文件 |
|------|------|----------|
| **Crypto 分析模式** | 只分析不开单的新模式 | `cli/main.py`, `cli/models.py` |
| **Sentiment Analyst** | 替代 Social Media Analyst，集成 StockTwits + Reddit | `tradingagents/agents/analysts/sentiment_analyst.py` |
| **MiniMax LLM** | M2.x 推理模型支持，reasoning_split | `tradingagents/llm_clients/` |
| **模型更新** | GPT-5.5、Claude Opus 4.7、xAI/Grok-4.20 | `tradingagents/llm_clients/model_catalog.py` |
| **分析师执行计划** | 计时钩子 + 可观测性 | `tradingagents/graph/analyst_execution.py` |
| **中国 A-share 基准** | SSE/SZSE 指数支持 | `tradingagents/default_config.py` |
| **环境变量配置** | `TRADINGAGENTS_*` 覆盖 DEFAULT_CONFIG | `tradingagents/default_config.py` |
| **Ollama 远程端点** | `OLLAMA_BASE_URL` 支持 | `tradingagents/llm_clients/openai_client.py` |
| **市场数据验证器** | grounding 数值声明 | `tradingagents/dataflows/market_data_validator.py` |
| **区域选择** | Qwen/GLM/MiniMax 国际/国内 | `cli/utils.py` |

### 2.3 自动保留的 A-share 定制（无冲突）

以下文件在上游不存在，merge 时自动保留：

- ✅ `tradingagents/dataflows/akshare_*.py` — 整个 AkShare 数据层
- ✅ `tradingagents/dataflows/smartmoney_vendor.py` — 本地数据库 vendor
- ✅ `tradingagents/agents/utils/fund_flow_tools.py` — 资金流向工具
- ✅ `tradingagents/agents/utils/industry_data_tools.py` — 行业数据工具
- ✅ `tradingagents/agents/utils/macro_data_tools.py` — 宏观数据工具
- ✅ `tradingagents/ticker_resolver.py` — A-share 代码解析器
- ✅ `tradingagents/portfolio/` — 整个投资组合模块
- ✅ `scripts/report_auditor.py` — 报告审计器
- ✅ `cli/batch_runner.py`, `dashboard.py`, `profiles.py`, `watchlists.py`
- ✅ 全部 A-share 测试文件（18 个）

---

## 3. 冲突解决（28 个文件）

### 3.1 冲突统计

| 指标 | 数值 |
|------|------|
| 冲突文件 | 28 个 |
| 冲突标记 (`<<<<<<<`) | 197 处 |
| 新增文件（上游） | 27 个 |
| 自动合并 | ~30 个文件 |

### 3.2 高冲突文件及解决策略

| 文件 | 冲突数 | 策略 |
|------|--------|------|
| `cli/main.py` | 10 | 保留上游 CLI 重写 + 本地 A-share 功能（batch_runner、中文提示、代码解析） |
| `tradingagents/llm_clients/openai_client.py` | 4 | 合并 upstream 的 capability dispatch + 本地 SenseNova/MiMo/Kimi 支持 |
| `tradingagents/default_config.py` | 3 | 保留 SenseNova 默认 + 合并 upstream 新配置项（benchmark、news 参数） |
| `tradingagents/graph/trading_graph.py` | ? | 保留 upstream 的 analyst execution + 本地的 checkpointer 逻辑 |
| `tradingagents/agents/utils/agent_utils.py` | ? | 保留 upstream 的 `get_instrument_context_from_state` + 本地中文语言指令 |
| `tradingagents/dataflows/interface.py` | ? | 保留本地 AkShare 路由 + 合并 upstream 的 `NoMarketDataError` |

---

## 4. 测试修复（35 → 0 失败）

### 4.1 修复分类

| 类别 | 失败数 | 原因 | 修复方式 |
|------|--------|------|----------|
| `format_money_cn` | 4 | upstream 添加了英文翻译后缀 | 恢复纯中文格式 |
| 配置默认值 | 6 | 测试期望 upstream 默认值 | 更新断言匹配本地 A-share 默认值 |
| Capability dispatch | 9 | DeepSeek/MiniMax 模型能力表 | 安装 `socksio` + 更新 capability 条目 |
| 接口路由 | 1 | vendor 失败行为差异 | 更新测试断言 |
| Portfolio | 1 | Google Sheet 认证过期 | mock `_fetch_from_gsheet` |
| Checkpoint | 1 | 触发真实 LLM 调用 | mock `_resolve_pending_entries` |
| Dashboard | 2 | `set_agent_status` → `update_agent_status` | 更新方法名 + 放宽限制 |
| CLI env skip | 1 | 交互式确认阻塞 | mock `questionary.confirm` |

### 4.2 最终测试结果

```
432 passed, 1 skipped, 9 warnings, 84 subtests passed
```

---

## 5. Bug 修复

### 5.1 `timestamp` NameError

**文件**: `cli/main.py`

**问题**: `timestamp` 变量在 `run_batch_analysis()` 中仅在 `if save_choice in ("Y", "YES", ""):` 块内定义，当用户跳过保存报告时，后续保存 watchlist 的代码引用未定义的 `timestamp`，导致 `NameError`。

**修复**: 在函数开头统一定义 `timestamp`：

```python
date_stamp = datetime.datetime.now().strftime("%Y%m%d")
timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
```

---

## 6. 新增 LLM Provider

### 6.1 添加的 Provider

| Provider | Base URL | API Key Env | 推荐模型 |
|----------|----------|-------------|----------|
| **Agnes AI** | `https://apihub.agnes-ai.com/v1` | `AGNES_API_KEY` | `agnes-2.0-flash`（免费，256K ctx） |
| **ModelScope** | `https://api-inference.modelscope.cn/v1` | `MODELSCOPE_API_KEY` | `deepseek-ai/DeepSeek-V4-Flash`（日限 2000 次） |
| **NVIDIA NIM** | `https://integrate.api.nvidia.com/v1` | `NVIDIA_API_KEY` | `deepseek-ai/deepseek-v4-pro`（H200/B200 优化） |
| **OpenRouter** | `https://openrouter.ai/api/v1` | `OPENROUTER_API_KEY` | 已存在，动态获取 |

### 6.2 修改的文件

1. `tradingagents/llm_clients/api_key_env.py` — 注册 API key 环境变量
2. `tradingagents/llm_clients/factory.py` — 注册 OpenAI-compatible provider
3. `tradingagents/llm_clients/openai_client.py` — 添加 base URL
4. `tradingagents/llm_clients/model_catalog.py` — 添加模型选项
5. `.env.example` — 添加环境变量占位符

---

## 7. 端到端测试

### 7.1 测试配置

| 配置项 | 值 |
|--------|-----|
| **Ticker** | 000001.SZ（平安银行） |
| **Provider** | SenseNova Token Plan |
| **Model** | sensenova-6.7-flash-lite |
| **Language** | Chinese |
| **Analysts** | market, social, news, fundamentals, governance, industry |

### 7.2 测试结果

| 指标 | 值 |
|------|-----|
| **Status** | ✅ 成功 |
| **Rating** | Underweight（减持） |
| **Entry** | 10.9 |
| **Stop** | 10.45 |
| **Size** | 50% |
| **Report** | `reports/test_e2e/平安银行_000001.SZ/complete_report.md` |

### 7.3 关键发现

- **SenseNova API key 修复**: `api_key_env.py` 缺少 `sensenova` 条目导致 401 错误，已修复
- **MiMo API key 过期**: 备用 provider 测试失败

---

## 8. 提交记录

```
db4933a  chore(env): add API key placeholders for Agnes, ModelScope, NVIDIA
56e65b8  feat(llm): add model catalog entries for Agnes, ModelScope, NVIDIA
56d2c49  feat(llm): add base URLs for Agnes, ModelScope, NVIDIA
38ad2b0  feat(llm): add Agnes, ModelScope, NVIDIA to OpenAI-compatible providers
f042d66  feat(llm): register API key env vars for Agnes, ModelScope, NVIDIA
3d124a7  fix(cli): define timestamp at function start to avoid NameError
344f30c  test(cli): mock questionary.confirm for non-interactive env skip test
d285b9b  test(dashboard): fix method name set_agent_status to update_agent_status
df1cdbf  fix(dashboard): allow update_agent_status to add new agents
01041ac  test(graph): mock pending entries resolution to avoid LLM call
1ce3558  test(portfolio): mock Google Sheet integration for offline tests
efce2e7  test(routing): update vendor failure assertion to match A-share chain
58434f9  test(config): update data vendor assertions for A-share chain
36d38bc  test(config): update assertions for A-share default config
a8b0326  fix(dataflows): restore format_money_cn to pure Chinese output
f967d14  merge: integrate upstream TauricResearch/TradingAgents main (53 commits)
```

---

## 9. 下一步计划

### 9.1 高优先级

1. **修复 report_auditor 文件扫描**
   - 当前审计报告为空（股票数: 0, 文件数: 0）
   - `report_auditor.py` 的目录扫描逻辑需要适配新的报告目录结构

2. **更新 API key**
   - SenseNova key 已确认有效
   - MiMo key 已过期，需要更新 `MIMO_API_KEY`
   - 新添加的 provider 需要配置 key：`AGNES_API_KEY`、`MODELSCOPE_API_KEY`、`NVIDIA_API_KEY`

3. **验证新 Provider 端到端**
   - 使用 Agnes AI（免费）或 ModelScope（日限 2000）执行完整分析
   - 确认 provider chain fallback 工作正常

### 9.2 中优先级

4. **同步 upstream v0.2.0 分支**
   - upstream 有 `v0.2.0` 分支，包含额外修复
   - 评估是否需要 cherry-pick

5. **完善测试覆盖**
   - 为新 provider 添加单元测试（api_key_env、base_url、model_catalog）
   - 添加 `agnes`、`modelscope`、`nvidia` 到 `test_capabilities.py`

6. **文档更新**
   - 更新 `docs/akshare_integration.md` 以反映 upstream 变化
   - 添加新 provider 的使用指南

### 9.3 低优先级

7. **性能优化**
   - 评估 upstream 的 `analyst_concurrency_limit` 对 A-share 分析的影响
   - 测试批量分析性能

8. **CLI 改进**
   - 添加 `--provider` 命令行参数覆盖 profile 配置
   - 支持非交互式批量分析（完全 headless）
