<p align="center">
  <img src="assets/TauricResearch.png" style="width: 60%; height: auto;">
</p>

<p align="center">
  <img src="docs/assets/cover.png" alt="quant-agents 双 LLM 管线封面" style="width: 100%; height: auto;">
</p>

# quant-agents

## A 股多 Agent 交易决策框架 · A-Share Multi-Agent Trading Framework

> English: An A-share–optimized fork of [TradingAgents](https://github.com/TauricResearch/TradingAgents) — a multi-agent LLM debate pipeline for Chinese market decision support, built on LangGraph.

> 基于 [TradingAgents](https://github.com/TauricResearch/TradingAgents)（[arXiv 2412.20138](https://arxiv.org/abs/2412.20138)）的 A 股一站式实现

[![A-Share](https://img.shields.io/badge/A--Share-Optimized-10B981?style=for-the-badge&logo=chinanet&logoColor=white)](#-a-股核心能力)
[![LangGraph](https://img.shields.io/badge/Orchestration-LangGraph-6366F1?style=for-the-badge)](https://github.com/langchain-ai/langgraph)
[![arXiv](https://img.shields.io/badge/arXiv-2412.20138-B31B1B?style=for-the-badge&logo=arxiv)](https://arxiv.org/abs/2412.20138)
[![License](https://img.shields.io/badge/License-Apache_2.0-374151?style=for-the-badge)](LICENSE)

---

## 简介

`quant-agents` 是为 **中国 A 股市场**深度定制的多 Agent 交易决策框架。它以 TradingAgents 多 Agent 架构为基础，把通用 LLM 交易流水线换成 A 股原生的数据源、标的解析、报告输出与持久化方案。

它要解决的是：**让 A 股用户开箱即用 TradingAgents 的多 Agent 能力，而不必自己解决 yfinance 拿不到 A 股数据、中文公司名识别、人民币金额展示、A 股特色数据维度（龙虎榜 / 资金流 / 股权质押 / 融资融券）等工程问题。**

---

## 与上游 TradingAgents 的差异

| 维度 | TradingAgents（上游） | quant-agents（本仓库） |
|---|---|---|
| 目标市场 | 美股为主 | A 股优先，兼容港股 / 日股 / 美股 / 欧股 / 印度 / Crypto |
| 行情与财务数据 | yfinance | 本地 SQLite 中台（可选）→ AkShare/东方财富 → HiThink → yfinance 多级路由 |
| 标的输入 | 交易所代码 | 代码后缀自动补全（`.SS`/`.SZ`/`.BJ`）+ 中文公司名解析 |
| 报告输出 | 英文 | 中文报告 + 人民币金额格式 + 公司名消毒 |
| A 股特色数据 | — | 龙虎榜、主力资金流、股权质押、融资融券、北向持股、大宗交易 |
| LLM 接入 | 主流国际厂商 | 20 个 provider 接入（含国内厂商与双端点）+ 双 LLM 分层 + fallback 链 + 限流/定价面板 |
| 持久化 | checkpoint | 决策记忆日志（含已实现收益反思）+ checkpoint resume + 持仓对比报告 |

上游论文与原始实现请引用 [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents)（见文末引用）。

---

## 🔌 数据中台集成（可选）

本项目可与 [`quant_data`](https://github.com/Golden0Voyager/quant-pipeline) 数据中台深度协同（**纯可选**，没有它也能通过在线 API 完整运行）：

1. **只读直连**：通过 [`smartmoney_vendor.py`](tradingagents/dataflows/smartmoney_vendor.py) 以**只读** SQLite 连接本地 `quant_core.db`（路径由 `QUANT_DB_PATH` 配置，默认 `~/Code/quant_data/quant_core.db`）。
2. **数据共享**：运行决策管线或回测时，优先读取本地已清洗的日线 OHLCV、预计算技术指标、基本面财务三表与个股主力资金流向。
3. **降级网络路由**：本地库缺失或过期时，自动回退到在线 API（AkShare / yfinance），保证管线可用。

---

## 🔌 A 股核心能力

| 维度 | 能力 |
|---|---|
| **数据源** | AkShare 直连东方财富（5 大 A 股特色数据维度 + 板块资金流 + 行业 + 宏观 + 研报） |
| **标的解析** | 中文公司名 → A 股代码自动识别（`平安银行` → `000001.SZ`） |
| **多市场** | A 股原生（`.SS` / `.SZ` / `.BJ`），同时支持港股 / 日股 / 美股 / 伦敦 / 印度 / Crypto |
| **决策输出** | Trader 结构化输出建仓点 / 止损点 / 仓位比例（`TraderProposal` schema） |
| **持久化** | 决策日志（`~/.tradingagents/memory/`）+ LangGraph checkpoint resume（崩溃自动续跑） |
| **报告** | 中文报告输出 + 公司名消毒（防 LLM 幻觉国企 / 股票代码）+ 报告审计器 |
| **LLM** | 20 个 provider 接入（OpenAI / Anthropic / Google / xAI / DeepSeek / Qwen / GLM / MiniMax / Kimi / Xiaomi MIMO / SenseNova / Agnes / ModelScope / NVIDIA / OpenRouter / Azure / 本地 Ollama 等）+ 双 LLM 分层 + 自动 fallback 链 |
| **工具** | 中文 Rich TUI dashboard + 批量并发（`--workers N`）+ watchlist / profile 持久化 |
| **测试** | 3300+ unit tests（`uv run python -m pytest -m unit`） |

---

## 架构

<p align="center">
  <img src="docs/assets/section_12.png" alt="多 Agent 决策架构总览" style="width: 100%; height: auto;">
</p>

```
                   ┌────────────────────────┐
                   │  Fundamentals Analyst  │
                   └───────────┬────────────┘
                               ▼
┌──────────────┐  ┌────────────────────────┐  ┌──────────────┐
│ Market       ├─►│  Bull vs Bear Debate   │◄─┤ Governance   │
│ Analyst      │  │   (Research Manager)   │  │ Analyst      │
└──────────────┘  └───────────┬────────────┘  └──────────────┘
                               ▼
┌──────────────┐  ┌────────────────────────┐  ┌──────────────┐
│ Industry     ├─►│      Trader Agent      │◄─┤ News         │
│ Analyst      │  │ (Entry / Stop / Size)  │  │ Analyst      │
└──────────────┘  └───────────┬────────────┘  └──────────────┘
                               ▼
                   ┌────────────────────────┐
                   │  Risk Debate → PM      │
                   │ (Aggr / Neutral / Conserv) │
                   └────────────────────────┘
```

执行流程：6 个 Analyst 并行跑 → 多空 Research Manager 辩论 → Trader 出结构化下单建议 → 风控三方辩论 → Portfolio Manager 拍板。完整 graph 见 `tradingagents/graph/setup.py`。

---

## A 股专属 Analyst 团队

| Analyst | 输入 | 输出 |
|---|---|---|
| **Market Analyst** | 行情 + 技术指标 + 板块资金流 | 趋势、支撑 / 阻力、动量信号 |
| **Industry Analyst** | 行业分类 + 宏观指标（CPI / PMI） | 行业景气度、宏观背景 |
| **Governance Analyst** | 股东户数 + 股权质押 + 融资融券 + 龙虎榜 + 大宗交易 | 治理风险、解禁压力、资金异动 |
| **News Analyst** | 全球新闻 + 公司公告 + 研报 | 事件影响、信息面 |
| **Fundamentals Analyst** | 财务三表 + 分红历史 + 业绩预告 | 财务健康、盈利预期 |
| **Sentiment Analyst** | 资金流向 + 北向持股 | 主力意图、市场情绪 |

6 个 Analyst 跑完后进入 Bull vs Bear 多空辩论（默认 1 轮，可配置），由 Research Manager 给出综合判断，Trader 据此输出 `TraderProposal`（建仓点 / 止损点 / 仓位比例），最后由 Portfolio Manager 拍板。

---

## 标的与市场

**A 股优先**（这是本 fork 的核心场景）：

```python
# 交易所后缀
"600519.SS"  # 沪市主板（贵州茅台）
"000001.SZ"  # 深市主板（平安银行）
"301236.SZ"  # 深市创业板
"830799.BJ"  # 北交所

# 中文公司名（自动解析）
"平安银行"   # → 000001.SZ
"贵州茅台"   # → 600519.SS
"宁德时代"   # → 300750.SZ
```

**其他市场**（同一套代码也能跑）：

| 市场 | 示例 ticker |
|---|---|
| 港股 | `0700.HK` |
| 日股 | `7203.T` |
| 美股 | `AAPL` |
| 伦敦 | `AZN.L` |
| 印度 | `RELIANCE.NS`, `.BO` |
| Crypto | `BTC-USD`, `ETH-USD` |

非 A 股市场走在线数据源，A 股优先本地库 → AkShare（东方财富）；路由在 `tradingagents/dataflows/interface.py` 自动完成。

---

## 快速上手

```bash
# 1. 克隆（使用 uv 管理依赖，详见 AGENTS.md）
git clone https://github.com/Golden0Voyager/quant-agents.git
cd quant-agents
uv venv && source .venv/bin/activate
uv pip install -e .

# 2. 配置 API key（任选一家即可）
cp .env.example .env
# 编辑 .env，填入至少一个 LLM provider 的 key

# 3. 跑单 ticker 分析（交互式 TUI）
uv run tradingagents
# 按提示选 ticker（如 "平安银行" 或 "000001.SZ"）、日期、LLM provider

# 4. 跑批量分析
uv run tradingagents analyze --watchlist my-list --workers 3
# 报告输出到 reports/YYYYMMDD_batch_<list>/<ticker>/complete_report.md
```

---

## 持久化与回测

- **决策日志** `~/.tradingagents/memory/trading_memory.md`：每次跑完自动追加决策；下次跑同 ticker 时 PM 提示词会带历史决策 + 实际收益 + 反思
- **Checkpoint resume**：CLI `analyze` 默认开启，崩溃后自动从上次成功的节点续跑（`--clear-checkpoints` 清空重跑）
- **批量并发**：`--workers N` 控制并行度；报告输出到 `reports/YYYYMMDD_batch_<list>/`
- **报告审计**：`uv run python scripts/report_auditor.py reports/<batch_dir>` 跨报告交叉验证

---

## 回测与复现

本仓库不维护公开的成绩单——LLM 输出具有采样随机性，任何回测表现都受模型版本、温度、时间窗口与数据质量影响，**不构成投资建议**。

自行复现的方法：

```bash
# 1. 准备 watchlist（每行一个 ticker 或中文名）
# 2. 批量跑历史日期分析
uv run tradingagents analyze --watchlist my-list --workers 3

# 3. 审计报告一致性
uv run python scripts/report_auditor.py reports/<batch_dir>
```

- 决策与已实现收益会沉淀进记忆日志，可跨批次对比
- 上游关于 Reproducibility 的讨论见 [TradingAgents 原始 README](https://github.com/TauricResearch/TradingAgents#reproducibility)

---

## 双 LLM 设计

<p align="center">
  <img src="docs/assets/section_20.png" alt="Deep Think vs Quick Think Path" style="width: 100%; height: auto;">
</p>

**双 LLM 路径**：Deep Think（Research Manager / Trader / Portfolio Manager）走深度推理；Quick Think（6 个 Analyst + 5 个辩论角色）走并行工具调用。`quant-agents` 在此基础上把 LLM factory 拓展到 20 个 provider 接入 + 2 个 A 股特殊 reasoning client（`DeepSeekChatOpenAI` / `MiniMaxChatOpenAI`），并支持 per-role 模型覆盖与多级 fallback 链。

详细调用链见 [docs/llm-pipeline.md](docs/llm-pipeline.md)。

---

## 安装与配置

### uv（推荐）

```bash
uv venv
source .venv/bin/activate
uv pip install -e .
```

### pip（备选）

```bash
pip install -e .
```

### 国内 LLM（任选其一）

```bash
export DASHSCOPE_API_KEY=...      # Qwen（通义千问）
export ZHIPU_API_KEY=...          # GLM（智谱）
export DEEPSEEK_API_KEY=...       # DeepSeek
export SENSENOVA_API_KEY=...      # SenseNova Token Plan（商汤）
export MIMO_API_KEY=...           # Xiaomi MIMO
export KIMI_CODING_API_KEY=...    # Kimi（Moonshot）
export AGNES_API_KEY=...          # Agnes AI（免费档）
export MODELSCOPE_API_KEY=...     # ModelScope（每日免费额度）
```

### 国际 LLM（任选其一）

```bash
export OPENAI_API_KEY=...         # OpenAI
export ANTHROPIC_API_KEY=...      # Anthropic Claude
export GOOGLE_API_KEY=...         # Google Gemini
export XAI_API_KEY=...            # xAI Grok
export OPENROUTER_API_KEY=...     # OpenRouter（聚合）
export AZURE_OPENAI_API_KEY=...   # Azure OpenAI
export NVIDIA_API_KEY=...         # NVIDIA NIM（免费 credits）
```

### 本地模型

```bash
export OLLAMA_BASE_URL=http://localhost:11434/v1   # 默认；改远程地址即可
```

### 高级

```bash
# 覆盖 DEFAULT_CONFIG 任意字段
export TRADINGAGENTS_LLM_PROVIDER=qwen
export TRADINGAGENTS_DEEP_THINK_LLM=qwen3-max
export TRADINGAGENTS_MAX_DEBATE_ROUNDS=3

# 国内 / 国际 endpoint 切换
export DASHSCOPE_CN_API_KEY=...    # Qwen 国内
export ZHIPU_CN_API_KEY=...        # GLM 国内
```

完整配置项见 `tradingagents/default_config.py`；provider / 模型目录见 `tradingagents/llm_clients/model_catalog.py`。

---

## 贡献

欢迎社区贡献!详细指南见 [`CONTRIBUTING.md`](CONTRIBUTING.md),内容包括:开发环境设置、git 分支纪律、commit 规范、CI 门禁(lint / typecheck / unit tests + coverage ≥ 90%)、数据源 / LLM 客户端改动的注意事项。

- **安全反馈**:请阅读 [`SECURITY.md`](SECURITY.md) 并通过 [GitHub Security Advisories](https://github.com/Golden0Voyager/quant-agents/security/advisories) 私密报告,**不要**在公开 Issue 中披露漏洞细节。
- **行为准则**:参与本项目即视为同意 [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md)。
- **Issue / PR 模板**:开 Issue 或 PR 时会引导你填写标准化表单,加速 triage。

---

## 问题反馈

遇到 bug 或有改进建议：

- 提 Issue：<https://github.com/Golden0Voyager/quant-agents/issues>
- 跑 `uv run python scripts/report_auditor.py reports/<batch_dir>` 自检报告

详细集成文档见 [`docs/`](docs/)；开发约定见 [`AGENTS.md`](AGENTS.md)。

---

## 引用

如果这个框架对你的量化研究有帮助，请引用 TradingAgents 原始工作：

```bibtex
@misc{xiao2025tradingagentsmultiagentsllmfinancial,
      title={TradingAgents: Multi-Agents LLM Financial Trading Framework},
      author={Yijia Xiao and Edward Sun and Di Luo and Wei Wang},
      year={2025},
      eprint={2412.20138},
      archivePrefix={arXiv},
      primaryClass={q-fin.TR},
      url={https://arxiv.org/abs/2412.20138},
}
```

---

## 致谢

- [Tauric Research](https://github.com/TauricResearch) — TradingAgents 原始作者
- 所有贡献者
