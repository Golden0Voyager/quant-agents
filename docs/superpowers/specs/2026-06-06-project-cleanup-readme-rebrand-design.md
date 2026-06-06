# Design Spec: Project Cleanup & README Rebrand

## Date: 2026-06-06
## Status: Draft for Review

---

## 1. Overview

将项目从"TradingAgents 的 A 股 fork"重新定位为"A 股一站式多 Agent 交易决策框架（基于 TradingAgents）"，并清理根目录和 docs/ 目录的不规整文件。

**范围**：
- Part A：README.md 全文重写（中英双语顶部，正文中文）
- Part B：项目清理（5 项删除/迁移）
- Part C：新增 docs/README.md 文档索引
- Part D：commit 拆分（5 个 conventional commits）

**非范围**（明确不做）：
- 不改 tradingagents/、cli/ 任何代码
- 不动 .gitignore 已有规则
- 不动 scripts/、tests/、profiles/、watchlists/、audit_reports/、reports/
- 不动 .env.example、.env.enterprise.example、.env（已正确 gitignore）
- 不动 CHANGELOG.md（用户已维护）
- 不动 LICENSE、CLAUDE.md、Dockerfile、docker-compose.yml
- 不删本地 llm-pipeline_images/ 文件（仅 git rm --cached）
- 不发 GitHub Release（留给用户手动）

---

## 2. Part A: README 重写

### 2.1 目标定位

| 维度 | 旧 | 新 |
|---|---|---|
| 身份 | "fork of TradingAgents" | "A 股一站式多 Agent 决策框架，基于 TradingAgents" |
| 受众 | 通用多市场用户 | A 股用户（中文为主，国际为辅） |
| 卖点 | "5 项 Enhancement" | 8 行 A 股能力矩阵 |
| upstream News 段 | 保留 v0.2.0-v0.2.5 changelog | 删除（已与反向贡献目标脱钩） |
| 回测证据 | 无 | 新增占位段（链接 reports/） |
| 标的展示 | 多市场并列 | A 股优先 + 中文公司名解析 |
| Contributing | "we welcome contributions to further enhance A-Share localizations" | 改为"问题反馈"为主 |

### 2.2 新结构（13 段）

| # | 段 | 长度 | 关键内容 |
|---|---|---|---|
| 1 | Hero | ~10 行 | 中英双标题 + 一句话定位 + 4 个徽章（A 股 / LangGraph / arXiv / License） |
| 2 | 核心定位 | ~3 行 | 解决 A 股用户痛点的项目身份声明 |
| 3 | A 股核心能力矩阵 | ~15 行 | 8 行表格（数据/标的/决策/持久化/报告/LLM/工具/测试） |
| 4 | 架构图 | ~20 行 | 简化 LangGraph StateGraph ASCII |
| 5 | A 股专属 Analyst 团队 | ~25 行 | 6 个 Analyst 简介 + 流程图 |
| 6 | 标的与市场 | ~15 行 | A 股代码 + 中文名解析示例 + 多市场为辅 |
| 7 | 快速上手 | ~10 行 | 5 步（克隆→uv→.env→CLI→报告） |
| 8 | 持久化与回测 | ~15 行 | decision log + checkpoint + batch |
| 9 | A 股回测与表现 | ~15 行 | 占位段（链接 reports/batch_*/complete_report.md） |
| 10 | 安装与配置 | ~40 行 | uv 优先（匹配 CLAUDE.md）+ 国内 4 LLM + 国际 5 LLM |
| 11 | 贡献 | ~5 行 | 弱化为"问题反馈" |
| 12 | 引用 | ~10 行 | TradingAgents arXiv bibtex |
| 13 | 致谢 | ~3 行 | TauricResearch + contributors |

### 2.3 关键删除清单

- 第 22-27 行 upstream v0.2.0-v0.2.5 News 段（已与反向贡献目标脱钩）
- 旧"Key A-Share & Production Enhancements"5 项列表
- 重复的 LLM API 列表（第 184-192 行与 156-173 行内容重复）
- 部分 upstream 原版图（researcher.png、risk.png、analyst.png 三个原图，保留 schema.png）
- "we welcome contributions to further enhance A-Share localizations" 文案
- main.py 的代码块引用（保留 main.py 文件，但 README 不再提）

### 2.4 双语处理策略

- **顶部标题**：中英双行（英文 subtitle + 中文 title）
- **徽章**：英文
- **正文段标题**：中文（每段后附 1 行英文原术语作锚点）
- **代码块**：保留英文命令和英文 LLM key 名
- **表格内容**：中文

---

## 3. Part B: 项目清理

### 3.1 删除/迁移清单

| 操作 | 路径 | 原因 |
|---|---|---|
| `git rm` | `test.py` (637 B) | upstream 遗留的硬编码 AAPL 测试，无价值 |
| `rm` | `requirements.txt` (2 B) | 只写了 `.`，CLAUDE.md 强制 uv，误导 |
| `git rm` | `docs/llm-pipeline.html` (72.5 KB) | 与 .md 双版本重复，HTML 嵌图片 base64 体积大 |
| `git rm --cached` | `docs/llm-pipeline_images/*.png` (13 个，约 55 MB) | 仓库膨胀，不删本地文件 |
| `git mv` | `docs/merge-upstream-2026-06-06.md` → `docs/superpowers/plans/2026-06-06-merge-upstream.md` | 私人合并记录归档 |
| **保留** | `main.py` (756 B) | 单 ticker demo，结构性保留 |
| **保留** | `docs/llm-pipeline.md` (13.1 KB) | 文档的唯一定稿版本 |

### 3.2 保留根目录的逻辑

- `main.py` 保留：仍是一个合法的最小 demo 入口（19 行，可读）
- `audit_reports/` `reports/` `profiles/` `watchlists/` 已在 `.gitignore`，无清理需求
- `.env` 已在 `.gitignore:151` 忽略，确认未跟踪

### 3.3 验证

清理前用 `git ls-files` 对比前后：
- 跟踪文件减少 ~14 个（1 test.py + 1 requirements.txt + 1 html + 13 png + 1 移动保留 1）
- 仓库体积减少 ~55 MB
- 工作区无变化（除移动和删除）

---

## 4. Part C: docs/README.md 索引

新增文件，结构：

```markdown
# 文档索引

按读者类型分组：

## 集成与数据
- [akshare_integration.md](akshare_integration.md) — A 股数据层（AkShare）
- [sensenova-deepseek-integration.md](sensenova-deepseek-integration.md) — SenseNova / DeepSeek 路由
- [financial_data_errors_report.md](financial_data_errors_report.md) — 已知数据问题与 workaround

## 工具使用
- [report_auditor_usage.md](report_auditor_usage.md) — 报告审计器
- [llm-pipeline.md](llm-pipeline.md) — LLM 流水线架构

## 内部（开发用）
- [agents/](agents/) — Agent 操作规范（issue tracker、triage labels、domain）
- [superpowers/](superpowers/) — 开发规划（plans）与设计文档（specs）
```

---

## 5. Part D: Commit 拆分

按语义和文件边界分 5 个 commit（CLAUDE.md 双语 conventional commits 风格）：

| # | Commit message | 涉及文件 |
|---|---|---|
| 1 | `chore(docs): untrack 55MB of llm-pipeline images and remove HTML version` | `docs/llm-pipeline_images/*.png` (cached rm) + `docs/llm-pipeline.html` |
| 2 | `chore(docs): archive upstream merge report to superpowers/plans/` | `docs/merge-upstream-2026-06-06.md` → `docs/superpowers/plans/2026-06-06-merge-upstream.md` |
| 3 | `chore: remove stale root-level test.py and requirements.txt` | `test.py` + `requirements.txt` |
| 4 | `docs: add docs/README.md as documentation index` | `docs/README.md` (新) |
| 5 | `docs(readme): rebrand as A-share-first framework with bilingual hero` | `README.md` (全文重写) |

**commit 顺序原因**：先清理基础（1-3），再加索引（4），最后重头戏 README（5）。便于回滚或 bisect。

---

## 6. 验收标准

### 6.1 项目清理验收
- [ ] `git ls-files` 中不再包含 `test.py` `requirements.txt` `docs/llm-pipeline.html` `docs/llm-pipeline_images/`
- [ ] `git ls-files` 中包含 `docs/superpowers/plans/2026-06-06-merge-upstream.md`
- [ ] 仓库总大小减少约 55 MB
- [ ] 本地 `docs/llm-pipeline_images/` 目录仍存在（仅退跟踪）
- [ ] 工作区状态干净（除预期的 5 个 commit）

### 6.2 README 验收
- [ ] 中英双语顶部
- [ ] 包含 8 行 A 股能力矩阵
- [ ] 包含 6 个 Analyst 简介
- [ ] 包含 A 股回测占位段
- [ ] 不含 upstream v0.2.0-v0.2.5 News 段
- [ ] 不含 "we welcome contributions to further enhance A-Share localizations"
- [ ] 长度控制在 300-400 行

### 6.3 docs/README.md 验收
- [ ] 包含 3 个分类（集成与数据 / 工具使用 / 内部）
- [ ] 每个文件至少出现一次
- [ ] 长度 < 30 行

### 6.4 Git 历史验收
- [ ] 5 个独立 commit，按 Part D 顺序
- [ ] 每个 commit 都有 conventional commits 前缀
- [ ] 推送前用户最终确认

---

## 7. 风险与回滚

| 风险 | 影响 | 回滚 |
|---|---|---|
| `git rm --cached` 误删 | 无 — 仅退跟踪，本地文件保留 | `git reset HEAD~1 && git checkout HEAD~1 -- <file>` |
| `git mv` 冲突 | 低 — 简单路径移动 | `git mv` 重做 |
| README 重写不满意 | 中 — 但可 git revert | `git revert <commit>` |
| `docs/README.md` 链接 404 | 低 — 索引页，只影响阅读体验 | 直接编辑 |

每个 commit 都是可独立 revert 的最小变更单位。

---

## 8. 不在本设计范围

- **发 GitHub Release**（llm-pipeline_images/ 后续如何托管）：用户手动
- **CHANGELOG.md 更新**：用户自己维护
- **CLAUDE.md 改动**：未授权
- **任何代码改动**：未授权
- **i18n / i18n 工具**：不做（README 中文为主，国际用户读代码）

---

## 9. 决策日志

| 决策 | 选项 | 选定 | 理由 |
|---|---|---|---|
| README 语言 | 中 / 英 / 双语 | 双语 | 顶部双语，正文中文 |
| A 股定位 | 弱化 / 并重 / 独立 | A 股一站式 | 重新定位主从关系 |
| 回测段 | 加 / 不加 | 加占位 | 强化护城河 |
| Analyst/Markets 段 | 改 A 股化 / 保留 / 删减 | 改 A 股化 | 突出 A 股特色 |
| llm-pipeline_images | rm/release/保留 | git rm + Release | 释放仓库体积 |
| llm-pipeline 双版本 | 留 md / 留 html / 保留 | 只留 md | 单一来源 |
| merge-upstream 报告 | 归档 / 删 / 保留 | 归档 | 内部化但保留记录 |
| 根目录文件 | 全删 / 删 2 留 1 / 保留 | 删 2 留 1 | main.py 仍有价值 |
| docs 索引 | 加 / 不加 | 加 | 文档可发现性 |
