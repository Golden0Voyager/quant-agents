# Project Cleanup & README Rebrand Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebrand README as A-share-first framework (bilingual hero, Chinese body) and clean root/docs/ of stale files; add `docs/README.md` index. Five conventional commits total.

**Architecture:** Documentation-only changes — no code, no test changes. Three cleanup commits (untrack images, archive merge report, delete root stubs), one index commit, one README rebrand commit.

**Tech Stack:** git CLI, markdown. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-06-06-project-cleanup-readme-rebrand-design.md`

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `docs/llm-pipeline_images/*.png` (13 files) | `git rm --cached` | Untrack ~55 MB; keep local copies |
| `docs/llm-pipeline.html` | `git rm` | Delete; HTML duplicates `.md` with embedded base64 images |
| `docs/merge-upstream-2026-06-06.md` | `git mv` | Archive to `docs/superpowers/plans/2026-06-06-merge-upstream.md` |
| `test.py` | `git rm` | Delete; upstream leftover hardcoded AAPL test, no value |
| `requirements.txt` | `git rm` | Delete; contains only `.`, conflicts with `uv` workflow per `CLAUDE.md` |
| `main.py` | (unchanged) | Keep; minimal 19-line single-ticker demo |
| `docs/README.md` | Create | New index for the `docs/` tree |
| `README.md` | Full rewrite | New bilingual hero + Chinese body + 13-section structure |
| `docs/llm-pipeline.md` | (unchanged) | Keep; sole canonical LLM-pipeline doc |

---

### Task 1: Untrack LLM-Pipeline Images and Remove HTML Version

**Files:**
- Modify: `docs/llm-pipeline_images/*.png` (13 files, untrack only)
- Delete: `docs/llm-pipeline.html`

- [ ] **Step 1: Verify current tracking status**

Run: `git ls-files docs/llm-pipeline_images/ | wc -l`
Expected: `13`

Run: `git ls-files docs/llm-pipeline.html`
Expected: `docs/llm-pipeline.html`

- [ ] **Step 2: Untrack all 13 PNG images (keep local files)**

Run:
```bash
git rm --cached docs/llm-pipeline_images/cover.png \
              docs/llm-pipeline_images/infographic.png \
              docs/llm-pipeline_images/section_02.png \
              docs/llm-pipeline_images/section_04.png \
              docs/llm-pipeline_images/section_06.png \
              docs/llm-pipeline_images/section_08.png \
              docs/llm-pipeline_images/section_10.png \
              docs/llm-pipeline_images/section_12.png \
              docs/llm-pipeline_images/section_14.png \
              docs/llm-pipeline_images/section_16.png \
              docs/llm-pipeline_images/section_18.png \
              docs/llm-pipeline_images/section_20.png
```

Expected: `rm 'docs/llm-pipeline_images/cover.png'` ... (13 lines)

- [ ] **Step 3: Verify local files still exist**

Run: `ls docs/llm-pipeline_images/ | wc -l`
Expected: `13`

Run: `git status --short docs/llm-pipeline_images/ | wc -l`
Expected: `13` (all marked as `D` for deletion-from-tracking only)

- [ ] **Step 4: Delete the HTML version**

Run: `git rm docs/llm-pipeline.html`
Expected: `rm 'docs/llm-pipeline.html'`

- [ ] **Step 5: Verify both untracked and deleted**

Run: `git ls-files docs/llm-pipeline.html`
Expected: (empty — file no longer tracked)

Run: `git ls-files docs/llm-pipeline_images/`
Expected: (empty — 13 files no longer tracked)

- [ ] **Step 6: Commit**

Run:
```bash
git add -u docs/llm-pipeline_images/ docs/llm-pipeline.html
git status --short
```

Expected: 14 lines, all marked `D` (13 PNGs + 1 HTML).

Run:
```bash
git commit -m "chore(docs): untrack 55MB llm-pipeline images and remove HTML version"
```

Commit message body (use `git commit --amend -m` or reopen in editor if needed):
```
untrack 13 PNG images (kept on local disk)
delete llm-pipeline.html (duplicates .md with embedded base64)
"删除 llm-pipeline.html，13 张 PNG 改本地保留（后续发 GitHub Release）"
```

Run: `git log --oneline -1`
Expected: top commit message matches the conventional format above.

---

### Task 2: Archive Upstream Merge Report

**Files:**
- Move: `docs/merge-upstream-2026-06-06.md` → `docs/superpowers/plans/2026-06-06-merge-upstream.md`

- [ ] **Step 1: Verify file exists in tracking**

Run: `git ls-files docs/merge-upstream-2026-06-06.md`
Expected: `docs/merge-upstream-2026-06-06.md`

- [ ] **Step 2: Move file using git mv**

Run: `git mv docs/merge-upstream-2026-06-06.md docs/superpowers/plans/2026-06-06-merge-upstream.md`
Expected: (no output)

- [ ] **Step 3: Verify the move**

Run: `git status --short | grep merge-upstream`
Expected: `R  docs/merge-upstream-2026-06-06.md -> docs/superpowers/plans/2026-06-06-merge-upstream.md`

Run: `git ls-files docs/superpowers/plans/ | grep merge`
Expected: `docs/superpowers/plans/2026-06-06-merge-upstream.md`

Run: `git ls-files docs/merge-upstream-2026-06-06.md`
Expected: (empty)

- [ ] **Step 4: Commit**

Run:
```bash
git commit -m "chore(docs): archive upstream merge report to superpowers/plans/"
```

Commit body:
```
Private merge report from 2026-06-06 upstream sync.
Internalized under superpowers/ to keep public docs/ clean.
"私人合并记录归档到 superpowers/plans/，不再暴露在公开 docs/"
```

Run: `git log --oneline -2`
Expected: top two commits show Task 1 and Task 2 messages.

---

### Task 3: Remove Stale Root-Level Files

**Files:**
- Delete: `test.py` (637 B)
- Delete: `requirements.txt` (2 B, contains only `.`)

- [ ] **Step 1: Verify both files are tracked**

Run:
```bash
git ls-files test.py requirements.txt
```

Expected:
```
requirements.txt
test.py
```

- [ ] **Step 2: Delete both files**

Run:
```bash
git rm test.py requirements.txt
```

Expected:
```
rm 'requirements.txt'
rm 'test.py'
```

- [ ] **Step 3: Verify deletion**

Run: `git status --short | grep -E 'test\.py|requirements\.txt'`
Expected: 2 lines, both marked `D`

Run: `ls test.py requirements.txt 2>&1`
Expected: both `No such file or directory`

- [ ] **Step 4: Verify `main.py` is preserved**

Run: `git ls-files main.py`
Expected: `main.py` (still tracked)

Run: `ls main.py`
Expected: `main.py` (still on disk)

- [ ] **Step 5: Commit**

Run:
```bash
git commit -m "chore: remove stale root-level test.py and requirements.txt"
```

Commit body:
```
test.py: upstream leftover (hardcoded AAPL indicator test, no value)
requirements.txt: contains only '.', conflicts with uv workflow in CLAUDE.md
main.py: kept (minimal 19-line single-ticker demo)
"删除 upstream 遗留 test.py 和几乎为空的 requirements.txt，保留 main.py demo"
```

Run: `git log --oneline -3`
Expected: top three commits show Tasks 1, 2, 3.

---

### Task 4: Create `docs/README.md` Index

**Files:**
- Create: `docs/README.md`

- [ ] **Step 1: Verify `docs/README.md` does not already exist**

Run: `ls docs/README.md 2>&1`
Expected: `No such file or directory`

- [ ] **Step 2: Create the index file**

Write to `docs/README.md` (exact content):

```markdown
# 文档索引

本目录按读者类型分组列出项目文档。

## 集成与数据

- [akshare_integration.md](akshare_integration.md) — A 股数据层（AkShare 接入与 vendor 路由）
- [sensenova-deepseek-integration.md](sensenova-deepseek-integration.md) — SenseNova / DeepSeek 路由与 Token Plan 配置
- [financial_data_errors_report.md](financial_data_errors_report.md) — 已知数据问题与 workaround

## 工具使用

- [report_auditor_usage.md](report_auditor_usage.md) — 报告审计器（`scripts/report_auditor.py`）
- [llm-pipeline.md](llm-pipeline.md) — LLM 流水线架构（quick_think / deep_think 双 LLM 设计）

## 内部（开发用）

- [agents/](agents/) — Agent 操作规范（issue tracker、triage labels、domain model）
- [superpowers/](superpowers/) — 开发规划（`plans/`）与设计文档（`specs/`）
```

- [ ] **Step 3: Verify file is created with expected size**

Run: `wc -l docs/README.md`
Expected: ≤ 30 lines

Run: `grep -c '^##' docs/README.md`
Expected: 3 (three `##` sections)

- [ ] **Step 4: Verify all linked files exist**

Run:
```bash
cd docs && for f in akshare_integration.md sensenova-deepseek-integration.md \
               financial_data_errors_report.md report_auditor_usage.md \
               llm-pipeline.md; do
  test -f "$f" && echo "OK: $f" || echo "MISSING: $f"
done
cd ..
```

Expected: 5 lines, all `OK: <file>`

- [ ] **Step 5: Commit**

Run:
```bash
git add docs/README.md
git commit -m "docs: add docs/README.md as documentation index"
```

Commit body:
```
Groups docs/ by audience:
- 集成与数据 (integration & data)
- 工具使用 (tooling)
- 内部 (internal, for development)
"新增 docs/README.md 文档索引，按读者类型分组"
```

Run: `git log --oneline -4`
Expected: top four commits show Tasks 1, 2, 3, 4.

---

### Task 5: Rewrite `README.md` (A-Share-First Rebrand)

**Files:**
- Modify: `README.md` (full rewrite)

- [ ] **Step 1: Verify current README size**

Run: `wc -l README.md`
Expected: `345`

- [ ] **Step 2: Write the new README**

Write to `README.md` (exact content — 13 sections, bilingual hero, Chinese body):

```markdown
<p align="center">
  <img src="assets/TauricResearch.png" style="width: 60%; height: auto;">
</p>

# Trading-Agents-A-Share

## A 股多 Agent 交易决策框架 · A-Share Multi-Agent Trading Framework

> 基于 [TradingAgents](https://github.com/TauricResearch/TradingAgents)（[arXiv 2412.20138](https://arxiv.org/abs/2412.20138)）的 A 股一站式实现

[![A-Share](https://img.shields.io/badge/A--Share-Optimized-10B981?style=for-the-badge&logo=chinanet&logoColor=white)](#-a-股核心能力)
[![LangGraph](https://img.shields.io/badge/Orchestration-LangGraph-6366F1?style=for-the-badge)](https://github.com/langchain-ai/langgraph)
[![arXiv](https://img.shields.io/badge/arXiv-2412.20138-B31B1B?style=for-the-badge&logo=arxiv)](https://arxiv.org/abs/2412.20138)
[![License](https://img.shields.io/badge/License-Apache_2.0-374151?style=for-the-badge)](LICENSE)

---

## 简介

`Trading-Agents-A-Share` 是为 **中国 A 股市场**深度定制的多 Agent 交易决策框架。它以 TradingAgents 多 Agent 架构为基础，把通用 LLM 交易流水线换成 A 股原生的数据源、标的解析、报告输出与持久化方案。

它要解决的是：**让 A 股用户开箱即用 TradingAgents 的多 Agent 能力，而不必自己解决 yfinance 拿不到 A 股数据、中文公司名识别、人民币金额展示、A 股特色数据维度（龙虎榜/资金流/股权质押/融资融券）等工程问题。**

---

## A 股核心能力

| 维度 | 能力 |
|---|---|
| **数据源** | AkShare 直连东方财富（5 大 A 股特色数据维度 + 板块资金流 + 行业 + 宏观 + 研报） |
| **标的解析** | 中文公司名 → A 股代码自动识别（`平安银行` → `000001.SZ`） |
| **多市场** | A 股原生（`.SS` / `.SZ`），同时支持港股 / 日股 / 美股 / 伦敦 / 印度 / Crypto |
| **决策输出** | Trader 结构化输出建仓点 / 止损点 / 仓位比例（`TraderProposal` schema） |
| **持久化** | 决策日志（`~/.tradingagents/memory/`）+ LangGraph checkpoint resume（崩溃自动续跑） |
| **报告** | 中文报告输出 + 公司名消毒（防 LLM 幻觉国企/股票代码） |
| **LLM** | 国内 4 家（Qwen / GLM / DeepSeek / SenseNova）+ 国际 5 家（OpenAI / Anthropic / Google / xAI / OpenRouter） |
| **工具** | 中文 Rich TUI dashboard + 批量并发（默认 3 workers）+ watchlist / profile 持久化 |
| **测试** | 432 unit tests 全通（`uv run pytest -m unit`） |

---

## 架构

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
│ Analyst      │  │ (Entry/Stop/Size)      │  │ Analyst      │
└──────────────┘  └───────────┬────────────┘  └──────────────┘
                               ▼
                   ┌────────────────────────┐
                   │  Risk Debate → PM      │
                   │ (Aggr/Neutral/Conserv) │
                   └────────────────────────┘
```

执行流程：6 个 Analyst 并行跑 → 多空 Research Manager 辩论 → Trader 出结构化下单建议 → 风控三方辩论 → Portfolio Manager 拍板。完整 graph 见 `tradingagents/graph/setup.py`。

---

## A 股专属 Analyst 团队

| Analyst | 输入 | 输出 |
|---|---|---|
| **Market Analyst** | 行情 + 技术指标 + 板块资金流 | 趋势、支撑/阻力、动量信号 |
| **Industry Analyst** | 行业分类 + 宏观指标（CPI/PMI） | 行业景气度、宏观背景 |
| **Governance Analyst** | 股东户数 + 股权质押 + 融资融券 + 龙虎榜 + 大宗交易 | 治理风险、解禁压力、资金异动 |
| **News Analyst** | 全球新闻 + 公司公告 + 研报 | 事件影响、信息面 |
| **Fundamentals Analyst** | 财务三表 + 分红历史 + 业绩预告 | 财务健康、盈利预期 |
| **Sentiment Analyst** | 资金流向 + 北向持股 | 主力意图、市场情绪 |

6 个 Analyst 跑完后进入 Bull vs Bear 多空辩论（默认 2 轮），由 Research Manager 给出综合判断，Trader 据此输出 `TraderProposal`（建仓点 / 止损点 / 仓位比例），最后由 Portfolio Manager 拍板。

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

非 A 股市场走 yfinance，A 股走 AkShare 走东方财富；路由在 `tradingagents/dataflows/interface.py` 自动完成。

---

## 快速上手

```bash
# 1. 克隆（使用 uv 管理依赖，详见 CLAUDE.md）
git clone https://github.com/Golden0Voyager/Trading-Agents-A-Share.git
cd Trading-Agents-A-Share
uv venv && source .venv/bin/activate
uv pip install -e .

# 2. 配置 API key
cp .env.example .env
# 编辑 .env，填入至少一个 LLM provider 的 key

# 3. 跑单 ticker 分析
tradingagents
# 按提示选 ticker（如 "平安银行" 或 "000001.SZ"）、日期、LLM provider

# 4. 跑批量回测
tradingagents analyze --watchlist my-list --workers 3
# 报告输出到 reports/batch_YYYYMMDD_HHMMSS/<ticker>/complete_report.md
```

---

## 持久化与回测

- **决策日志** `~/.tradingagents/memory/trading_memory.md`：每次跑完自动追加决策；下次跑同 ticker 时 PM 提示词会带历史决策 + 实际收益 + 反思
- **Checkpoint resume**：长跑任务崩溃后自动从上次成功的节点续跑（`--checkpoint` 开启）
- **批量并发**：`--workers N` 控制并行度（默认 3），进度条 + 中文 dashboard
- **报告审计**：`python scripts/report_auditor.py reports/batch_20260606_120000` 跨报告交叉验证

---

## A 股回测与表现

> 这一段是**占位**：我们正在积累回测数据。已有的批跑结果：

| Ticker | 公司 | 分析日期 | 决策 | 7 日实际收益 | 报告 |
|---|---|---|---|---|---|
| `000001.SZ` | 平安银行 | 2026-06-06 | 待补 | 待补 | [报告](reports/20260606_batch_my/000001.SZ/complete_report.md) |
| `600519.SS` | 贵州茅台 | — | — | — | — |
| `300750.SZ` | 宁德时代 | — | — | — | — |

**重要说明**：LLM 框架非保证收益工具。回测表现受模型、温度、时间范围、数据质量、采样随机性影响。结果仅供多 Agent 分析研究，不构成投资建议。详见 TradingAgents 原始 README 的 Reproducibility 章节。

---

## 安装与配置

### uv（推荐，匹配 `CLAUDE.md`）

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
```

### 国际 LLM（任选其一）

```bash
export OPENAI_API_KEY=...         # OpenAI
export ANTHROPIC_API_KEY=...      # Anthropic Claude
export GOOGLE_API_KEY=...         # Google Gemini
export XAI_API_KEY=...            # xAI Grok
export OPENROUTER_API_KEY=...     # OpenRouter（聚合）
```

### 本地模型

```bash
export OLLAMA_BASE_URL=http://localhost:11434/v1   # 默认；改远程地址即可
```

### 高级

```bash
# 覆盖 DEFAULT_CONFIG 任意字段
export TRADINGAGENTS_LLM_PROVIDER=qwen
export TRADINGAGENTS_DEEP_THINK_LM=qwen3-max
export TRADINGAGENTS_MAX_DEBATE_ROUNDS=3

# 国内/国际 endpoint 切换
export DASHSCOPE_CN_API_KEY=...    # Qwen 国内
export ZHIPU_CN_API_KEY=...        # GLM 国内
```

---

## 问题反馈

遇到 bug 或有改进建议：

- 提 Issue：<https://github.com/Golden0Voyager/Trading-Agents-A-Share/issues>
- 跑 `python scripts/report_auditor.py reports/<batch_dir>` 自检报告

详细集成文档见 [`docs/`](docs/)。

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
```

- [ ] **Step 3: Verify new README size and key sections**

Run: `wc -l README.md`
Expected: between 280 and 360 lines (target 300-400)

Run: `grep -c '^## ' README.md`
Expected: 13 (top-level sections)

Run: `grep -E '^# Trading-Agents-A-Share' README.md`
Expected: matches one line (the H1 title)

- [ ] **Step 4: Verify forbidden content is removed**

Run: `grep -i "we welcome contributions to further enhance A-Share localizations" README.md`
Expected: (empty — phrase removed)

Run: `grep -E "TradingAgents v0\.2\.[0-5] released" README.md`
Expected: (empty — upstream News block removed)

- [ ] **Step 5: Verify required content is present**

Run: `grep -E "平安银行|000001\.SZ" README.md`
Expected: at least 2 matches (中文公司名 + A 股代码)

Run: `grep -E "AkShare|akshare" README.md`
Expected: at least 2 matches (mentioned in data + market sections)

Run: `grep -E "432 unit tests" README.md`
Expected: 1 match (test count in core capabilities table)

- [ ] **Step 6: Verify bilingual hero**

Run: `head -20 README.md`
Expected: First 20 lines include both Chinese and English text, the arXiv badge, and the License badge.

- [ ] **Step 7: Commit**

Run:
```bash
git add README.md
git status --short
```

Expected: 1 line, marked `M` on `README.md`.

Run:
```bash
git commit -m "docs(readme): rebrand as A-share-first framework with bilingual hero"
```

Commit body:
```
- Bilingual hero (Chinese title + English subtitle)
- 8-row A-share core capabilities table
- A-share-first analyst team section (Market/Industry/Governance/News/Fundamentals/Sentiment)
- A-share-first markets section with Chinese company name resolution
- New A-share backtest results placeholder section
- Remove upstream v0.2.0-v0.2.5 News block (decoupled from reverse-contribution goal)
- Remove duplicate LLM API list
- Remove "we welcome contributions to further enhance A-Share localizations"

"README 重新定位为 A 股一站式框架：中英双语顶部 / 8 行能力矩阵 /
6 个 A 股特色 Analyst / 标的解析示例 / 回测占位段 /
删除 upstream News 段与 fork 身份文案"
```

Run: `git log --oneline -5`
Expected: top 5 commits show Tasks 1, 2, 3, 4, 5.

---

## Final Verification (all 5 tasks complete)

- [ ] **Step 1: Confirm all cleanup actions succeeded**

Run:
```bash
git ls-files test.py requirements.txt \
              docs/llm-pipeline.html \
              docs/llm-pipeline_images/ \
              docs/merge-upstream-2026-06-06.md
```

Expected: (empty — none of these are tracked)

Run: `git ls-files docs/superpowers/plans/2026-06-06-merge-upstream.md docs/README.md README.md main.py`
Expected: 4 files all tracked

- [ ] **Step 2: Confirm local files preserved where intended**

Run: `ls docs/llm-pipeline_images/ | wc -l`
Expected: `13` (local files still exist)

Run: `ls main.py`
Expected: `main.py` (kept on disk)

- [ ] **Step 3: Confirm 5 conventional commits**

Run: `git log --oneline -5`
Expected: 5 conventional commits, in order:
1. `chore(docs): untrack 55MB llm-pipeline images and remove HTML version`
2. `chore(docs): archive upstream merge report to superpowers/plans/`
3. `chore: remove stale root-level test.py and requirements.txt`
4. `docs: add docs/README.md as documentation index`
5. `docs(readme): rebrand as A-share-first framework with bilingual hero`

- [ ] **Step 4: Pause for user review before push**

**Do not push.** Show the user `git log --oneline -5` and `git status` and wait for approval.
