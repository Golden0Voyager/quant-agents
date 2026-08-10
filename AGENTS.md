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

- `quick_think_llm` — Analysts + debaters (parallel, tool-heavy)
- `deep_think_llm` — Research Manager, Trader, Portfolio Manager (serial decisions)

### Data Vendors (`tradingagents/dataflows/`)

Routing via `interface.py` → yfinance / alpha_vantage / akshare (A-share Eastmoney).
A-share: `akshare_vendor.py` + `akshare_common.py` (`format_money_cn`, `to_akshare_symbol`, `no_proxy`).
Local-first archive: `smartmoney_vendor.py` reads `~/Code/quant_data/quant_core.db` (env `QUANT_DB_PATH`) — A-shares via `daily_bars` (vendor `smartmoney_db`), US stocks / crypto via `global_assets_bars` (vendor `quant_db_global`, registered separately because the router skips the `smartmoney_db` name for non-A-share tickers). Stale local OHLCV (latest row lags > `MAX_OHLCV_STALE_DAYS`) falls back to online vendors.
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
- Chinese company names → akshare fuzzy match + JSON cache
- `company_name` injected into all prompts via `build_instrument_context()`

### Persistence

- **Checkpoint** (default off): `SqliteSaver` per ticker at `~/.tradingagents/cache/checkpoints/<TICKER>.db`. Crashed runs auto-resume when enabled. Clear with `--clear-checkpoints`.
- **Memory log**: `~/.tradingagents/memory/trading_memory.md` (decisions + realized returns, injected into PM prompt as `past_context`)

### Batch Output

`reports/YYYYMMDD_batch_<list>/`:
- `<ticker>/complete_report.md` + `<ticker>/1_analysts/` + `<ticker>/2_research/`
- `batch_summary.md` + `batch_summary.json`
- `failures.log`

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
