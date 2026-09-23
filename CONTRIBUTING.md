# 贡献指南 · Contributing to quant-agents

欢迎贡献!无论是修 bug、加数据源、改进提示词,还是完善文档,都欢迎提交 PR。

> **必读**:开工前请先读 [`AGENTS.md`](AGENTS.md)(仓库环境约束与架构)、[`docs/contributing/git-workflow.md`](docs/contributing/git-workflow.md)(git 纪律,硬性要求)和 [`docs/contributing/atomic-commits.md`](docs/contributing/atomic-commits.md)(提交拆分)。

---

## 环境约束(强制)

- **包管理器**:一律使用 `uv`——`uv pip install <pkg>`、`uv run python <script>.py`。**禁止** `pip` / `python -m pip`。
- **Python**:`>=3.10`(CI 用 3.12 跑 lint / typecheck / test)。
- **文件编码**:所有文件 I/O 使用 `utf-8`。
- 运行入口:`uv run tradingagents`(CLI)、`uv run python -m cli.main ...`(batch)。

```bash
git clone https://github.com/Golden0Voyager/quant-agents.git
cd quant-agents
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
```

---

## 从哪开始

不熟悉代码库?看这些:

- `AGENTS.md` — 架构总览、Graph 管线、双 LLM、数据厂商路由
- `docs/` — 集成文档(akshare / hithink / llm-pipeline / report_auditor)
- `tradingagents/graph/setup.py` — StateGraph 完整定义
- `tradingagents/dataflows/interface.py` — 数据厂商路由入口
- 找活干:看 GitHub Issues 中的 `good first issue` / `needs-triage` 标签

---

## 开发工作流

### 1. Fork + 分支

本仓库对提交纪律有硬性要求(见 `docs/contributing/git-workflow.md`,特别是 **upstream 永远禁止 force-push**):

```bash
git remote add upstream https://github.com/TauricResearch/TradingAgents.git  # 仅只读同步用
git fetch upstream
git checkout -b feat/your-change origin/main    # 基于你的 origin/main 开分支
```

分支命名:`feat/`、`fix/`、`refactor/`、`docs/`、`test/`、`chore/` 前缀 + 短描述。

### 2. 提交规范

- **Conventional Commits**:`feat:`, `fix:`, `docs:`, `refactor:`, `test:` 等。
- **中英双语**:英文块在前,中文在后(项目全局约定)。
- **原子提交**:一个逻辑变更一个提交,相互独立、可单独回滚。大改动用 `git add -p` 按主题拆分,见 `atomic-commits.md`(注意"测试滞后"陷阱:测试应与实现同提交,不要滞后)。

```bash
git commit -m "feat: add bloomberg vendor fallback
为港股增加 Bloomberg 数据回退"  # 示例——中文留空则省略
```

### 3. 本地验证(PR 前必须全绿)

| 门禁 | 命令 |
|---|---|
| Lint | `uv run ruff check .` |
| Typecheck | `uv run mypy tradingagents cli --ignore-missing-imports` |
| Unit tests | `uv run pytest -m unit`(全量 ~3300+,diag 时可用 `-m "unit and not slow"`) |
| 覆盖率 | 单测覆盖率必须 **≥ 90%**(`coverage report --fail-under=90`,CI 强制) |
| 测试标记 | 每个测试**必须**带 `unit` / `integration` / `smoke` 标记(`--strict-markers`,CI 会拒绝未标记的测试) |

新增功能建议 TDD:`uv run pytest -m unit` 先红后绿。

### 4. 提 PR

```bash
git push origin feat/your-change
gh pr create --base origin:main --head origin:feat/your-change
```

PR 描述请填写 `.github/PULL_REQUEST_TEMPLATE.md` 中的清单:

- 变更内容与动机
- 测试结果(`uv run pytest -m unit` 是否通过,新增测试数)
- 是否影响数据路由 / LLM 配置 / 报告格式(注明)
- 关联 issue 编号

CI 会自动跑 lint / typecheck / unit + coverage;integration 测试需要 secret `OPENAI_API_KEY`,外部贡献者 PR 中若未配置会被 skip(本地可用 `uv run pytest -m integration` 验证)。

---

## Issue 跟踪

- **外部贡献者**:直接开 GitHub Issue。用 `.github/ISSUE_TEMPLATE/` 里的模板(或 `bug_report` / `feature_request`),维护者会打 `needs-triage` 等标签。
- **内部开发**:本仓库有本地 markdown issue tracker(`.scratch/<feature>/`,见 `docs/agents/issue-tracker.md`),用 GitHub Issue 跟踪外部的,用 `.scratch/` 跟踪实施细节。

---

## 数据 / LLM 相关改动特别注意

- **数据路由**:改 `tradingagents/dataflows/` 前先读 `docs/akshare_integration.md` 与 `docs/tonghuashun_api.md`;新 vendor 必须参与 `interface.py` 的 fallback 链,并保证 `NoMarketDataError` 语义(空壳结果会阻断下游 fallback)。
- **LLM 客户端**:改 `tradingagents/llm_clients/` 前先读 `docs/llm-pipeline.md`。注意 `ChatPromptTemplate` 会剥离 `additional_kwargs`(DeepSeek `reasoning_content` 侧车缓存的已知坑),以及 `deepseek-reasoner` 不支持 `tool_choice`。
- **配置**:新增 env var 时必须同步更新 `.env.example`、`tradingagents/default_config.py` 与 README 的安装章节。
- **API key**:**永远不要**把 key 提交进仓库(见 `SECURITY.md`)。`.env`、`reports/` 均在 `.gitignore` 中。

---

## 文档

- 用户向文档(集成、使用)放 `docs/`;开发向约定放 `AGENTS.md` 或 `docs/agents/`。
- 改了 README 命令/配置,记得同步 `docs/README.md` 索引。
- 新功能建议先写 PRD 到 `.scratch/<feature>/PRD.md` 再动手(内部约定)。

---

## 行为准则

参与本项目即视为同意我们的行为准则:请阅读并遵守 [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md)。

感谢贡献 ❤️