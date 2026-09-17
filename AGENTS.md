## ⚠️ 环境约束（强制）

- **包管理器**：`uv pip install <pkg>`（禁止 `pip` / `python -m pip`）
- **运行脚本**：`uv run python <script>.py`（禁止直接 `python`）

---

# TradingAgents

Multi-agent LLM trading framework on LangGraph. Simulates a trading firm: analysts → research debate → research manager → trader → risk debate → portfolio manager.

## Commands

```bash
uv run tradingagents                        # Interactive CLI
uv run python -m cli.main batch my-list     # Batch run (Rich TUI)
uv run python -m cli.main batch my-list --output-dir ./reports
uv run python -m pytest -m unit             # Unit tests (~2500 total)
uv run python -m pytest -m integration      # Needs API keys
uv run python scripts/doctor.py             # Environment self-check (add --network for akshare probe)
```

## Architecture

- `tradingagents/` — Core framework
- `cli/` — Rich + Typer UI
- `tests/` — pytest (markers: unit, integration, smoke)

### Graph Pipeline (StateGraph in `graph/setup.py`)

1. **Analysts** (serial, each has tool loop + Msg Clear node): Market (indicators + OHLCV), Sentiment (StockTwits + Reddit), News (global + insider), Fundamentals (financials), Governance (shareholders / pledge / institutional / northbound), Industry (sector momentum / macro)
2. **Research Team** — Bull vs Bear debate (`max_debate_rounds`)
3. **Research Manager** — Structured `ResearchPlan` (rating + rationale + actions)
4. **Trader** — Structured `TraderProposal` (action + entry/stop/sizing)
5. **Risk Management** — Aggressive / Neutral / Conservative debate (`max_risk_discuss_rounds`)
6. **Portfolio Manager** — Structured `PortfolioDecision` (Buy/Overweight/Hold/Underweight/Sell)

Analyst execution timing via `tradingagents/graph/analyst_execution.py`.

### Dual-LLM

- `quick_think_llm` — Analysts (parallel, tool-heavy)
- `deep_think_llm` — Bull/Bear researchers, risk debaters, and the default for the serial decision roles
- `deep_think_llm_roles` — Optional per-role model overrides for `research_manager` / `trader` / `portfolio_manager` and the debaters (`bull_researcher` / `bear_researcher` / `aggressive_debater` / `neutral_debater` / `conservative_debater`); roles not listed share the base `deep_think_llm` chain
- Primary tier comes from `*_think_llm` + `llm_provider` + `backend_url`; `*_think_fallback` entries only add fallback tiers (entries duplicating the primary provider+model are skipped)

### Data Vendors (`tradingagents/dataflows/`)

Routing via `interface.py` → yfinance / alpha_vantage / akshare (A-share Eastmoney).
A-share: `akshare_vendor.py` + `akshare_common.py` (`format_money_cn`, `to_akshare_symbol`, `no_proxy`).
HiThink (同花顺官方 API): `hithink_vendor.py` + `hithink_common.py` (env `HITHINK_FINANCE_API_KEY`, see `docs/tonghuashun_api.md`) — sits between `smartmoney_db` and `akshare` for income/balance/cashflow/financial-indicators/hot-rank/dragon-tiger/limit-up-down; anomaly-reason (hithink-only) is prefetched into the Sentiment prompt; valuation-snapshot anchors PE/PB/PS/PCF in `market_data_validator`; auction-snapshot/short-term-benchmark (hithink-only) are prefetched into the Market Analyst prompt (not LLM tools); backoff on business code 4001/5xxx, thscode uses `.SH` (converted via `ticker_to_thscode`).
Local-first archive: `smartmoney_vendor.py` reads `~/Code/quant_data/quant_core.db` (env `QUANT_DB_PATH`) — A-shares via `daily_bars` (vendor `smartmoney_db`), US stocks / crypto via `global_assets_bars` (vendor `quant_db_global`, registered separately because the router skips the `smartmoney_db` name for non-A-share tickers). Stale local OHLCV (latest row lags > `MAX_OHLCV_STALE_DAYS`) falls back to online vendors. `quarterly_financials` rows whose metric columns are all NULL raise `NoMarketDataError` (empty-shell results would otherwise block the hithink/akshare fallback chain).
Market data validation via `market_data_validator.py` (grounding numerical claims).

### LLM Clients (`tradingagents/llm_clients/`)

- `factory.py` — Lazy-import routing
- `openai_client.py` — OpenAI-compatible (OpenAI, xAI, DeepSeek, Qwen, GLM, OpenRouter, Ollama, SenseNova, Agnes AI, ModelScope, NVIDIA NIM, MiniMax)
  - `NormalizedChatOpenAI` — Responses API normalization
  - `DeepSeekChatOpenAI` — `reasoning_content` sidecar cache keyed by `message.id`
  - `MiniMaxChatOpenAI` — `reasoning_content` via `reasoning_split`
- Provider-specific: `anthropic_client.py`, `google_client.py`, `azure_client.py`

### Structured Output (`tradingagents/agents/schemas.py`)

- `ResearchPlan`, `TraderProposal`, `PortfolioDecision`
- `agents/utils/structured.py` — `bind_structured()` + `invoke_structured_or_freetext()` fallback

### A-Share Tickers (`tradingagents/ticker_resolver.py`)

- Numeric codes → auto-append `.SS`/`.SZ`/`.BJ` by prefix rules
- Chinese company names → HiThink `tickers/search` (when key configured) → akshare fuzzy match + JSON cache
- `company_name` injected into all prompts via `build_instrument_context()`

### Persistence

- **Checkpoint** (default off): `SqliteSaver` per ticker at `~/.tradingagents/cache/checkpoints/<TICKER>.db`. Crashed runs auto-resume when enabled. Clear with `--clear-checkpoints`.
- **Memory log**: `~/.tradingagents/memory/trading_memory.md` (decisions + realized returns, injected into PM prompt as `past_context`)

### Batch Output

`reports/YYYYMMDD_batch_<list>/`:
- `<ticker>/complete_report.md` + `<ticker>/1_analysts/` + `<ticker>/2_research/`
- `batch_summary.md` + `batch_summary.json`
- `failures.log`
- `reports/portfolio_comparison.md` — agent 推荐 vs 实盘操作对比（`uv run python scripts/portfolio_backtest.py`）

Audit: `uv run python scripts/report_auditor.py reports/YYYYMMDD_batch_<list>`

## Critical Implementation Notes

- `ChatPromptTemplate` strips `additional_kwargs` (including `reasoning_content`). DeepSeek sidecar cache keys on `message.id` to survive template recreation.
- `deepseek-reasoner` does not support `tool_choice`; `with_structured_output` raises `NotImplementedError` → falls back to free text.
- MiniMax M2.x uses `reasoning_split` for `reasoning_content` extraction (not sidecar pattern).
- `TRADINGAGENTS_*` env vars (e.g. `TRADINGAGENTS_LLM_PROVIDER`) override the default config. Use `default_config()` in code to obtain a fresh deep copy with overrides applied; `DEFAULT_CONFIG` is kept as a backward-compatible import-time reference.
- `OLLAMA_BASE_URL` env var for remote Ollama endpoints.
- All file I/O uses `encoding="utf-8"`.
- Env: `.env` (copied from `.env.example`) + optional `.env.enterprise`.
- New providers: `AGNES_API_KEY`, `MODELSCOPE_API_KEY`, `NVIDIA_API_KEY` in `.env`.

## Agent skills

### Issue tracker

Issues are tracked as local markdown files under `.scratch/<feature>/` in this repo. See `docs/agents/issue-tracker.md`.

### Triage labels

The canonical triage labels use their default names: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context layout — one `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.

### Git workflow

- **New Feature 流程**: 开发新功能 (new feature) 时，建议走 `/git-feature` 流程（使用 `/git-feature start` 创建分支，完成开发后使用 `/git-feature done` 完成推送/PR/合入/清理全流程）。
- **推送准则**: Three-tier rule for `origin` / `upstream` / local refs with `--force-with-lease` only on `origin`. **Never force-push `upstream`.** See `docs/contributing/git-workflow.md`.

<!-- rtk-instructions v2 -->
# RTK (Rust Token Killer) - Token-Optimized Commands

## Golden Rule

**Always prefix commands with `rtk`**. If RTK has a dedicated filter, it uses it. If not, it passes through unchanged. This means RTK is always safe to use.

**Important**: Even in command chains with `&&`, use `rtk`:
```bash
# ❌ Wrong
git add . && git commit -m "msg" && git push

# ✅ Correct
rtk git add . && rtk git commit -m "msg" && rtk git push
```

## RTK Commands by Workflow

### Build & Compile (80-90% savings)
```bash
rtk cargo build         # Cargo build output
rtk cargo check         # Cargo check output
rtk cargo clippy        # Clippy warnings grouped by file (80%)
rtk tsc                 # TypeScript errors grouped by file/code (83%)
rtk lint                # ESLint/Biome violations grouped (84%)
rtk prettier --check    # Files needing format only (70%)
rtk next build          # Next.js build with route metrics (87%)
```

### Test (60-99% savings)
```bash
rtk cargo test          # Cargo test failures only (90%)
rtk go test             # Go test failures only (90%)
rtk jest                # Jest failures only (99.5%)
rtk vitest              # Vitest failures only (99.5%)
rtk playwright test     # Playwright failures only (94%)
rtk pytest              # Python test failures only (90%)
rtk rake test           # Ruby test failures only (90%)
rtk rspec               # RSpec test failures only (60%)
rtk test <cmd>          # Generic test wrapper - failures only
```

### Git (59-80% savings)
```bash
rtk git status          # Compact status
rtk git log             # Compact log (works with all git flags)
rtk git diff            # Compact diff (80%)
rtk git show            # Compact show (80%)
rtk git add             # Ultra-compact confirmations (59%)
rtk git commit          # Ultra-compact confirmations (59%)
rtk git push            # Ultra-compact confirmations
rtk git pull            # Ultra-compact confirmations
rtk git branch          # Compact branch list
rtk git fetch           # Compact fetch
rtk git stash           # Compact stash
rtk git worktree        # Compact worktree
```

Note: Git passthrough works for ALL subcommands, even those not explicitly listed.

### GitHub (26-87% savings)
```bash
rtk gh pr view <num>    # Compact PR view (87%)
rtk gh pr checks        # Compact PR checks (79%)
rtk gh run list         # Compact workflow runs (82%)
rtk gh issue list       # Compact issue list (80%)
rtk gh api              # Compact API responses (26%)
```

### JavaScript/TypeScript Tooling (70-90% savings)
```bash
rtk pnpm list           # Compact dependency tree (70%)
rtk pnpm outdated       # Compact outdated packages (80%)
rtk pnpm install        # Compact install output (90%)
rtk npm run <script>    # Compact npm script output
rtk npx <cmd>           # Compact npx command output
rtk prisma              # Prisma without ASCII art (88%)
rtk uv run <cmd>        # Compact uv project command output
```

### Files & Search (60-75% savings)
```bash
rtk ls <path>           # Tree format, compact (65%)
rtk read <file>         # Code reading with filtering (60%)
rtk grep <pattern>      # Search grouped by file (75%). Format flags (-c, -l, -L, -o, -Z) run raw.
rtk find <pattern>      # Find grouped by directory (70%)
```

### Analysis & Debug (70-90% savings)
```bash
rtk err <cmd>           # Filter errors only from any command
rtk log <file>          # Deduplicated logs with counts
rtk json <file>         # JSON structure without values
rtk deps                # Dependency overview
rtk env                 # Environment variables compact
rtk summary <cmd>       # Smart summary of command output
rtk diff                # Ultra-compact diffs
```

### Infrastructure (85% savings)
```bash
rtk docker ps           # Compact container list
rtk docker images       # Compact image list
rtk docker logs <c>     # Deduplicated logs
rtk kubectl get         # Compact resource list
rtk kubectl logs        # Deduplicated pod logs
```

### Network (65-70% savings)
```bash
rtk curl <url>          # Compact HTTP responses (70%)
rtk wget <url>          # Compact download output (65%)
```

### Meta Commands
```bash
rtk gain                # View token savings statistics
rtk gain --history      # View command history with savings
rtk discover            # Analyze Claude Code sessions for missed RTK usage
rtk proxy <cmd>         # Run command without filtering (for debugging)
rtk init                # Add RTK instructions to CLAUDE.md
rtk init --global       # Add RTK to ~/.claude/CLAUDE.md
```

## Token Savings Overview

| Category | Commands | Typical Savings |
|----------|----------|-----------------|
| Tests | vitest, playwright, cargo test | 90-99% |
| Build | next, tsc, lint, prettier | 70-87% |
| Git | status, log, diff, add, commit | 59-80% |
| GitHub | gh pr, gh run, gh issue | 26-87% |
| Package Managers | pnpm, npm, npx | 70-90% |
| Files | ls, read, grep, find | 60-75% |
| Infrastructure | docker, kubectl | 85% |
| Network | curl, wget | 65-70% |

Overall average: **60-90% token reduction** on common development operations.
<!-- /rtk-instructions -->