# Roadmap Archive — Completed Milestones

> **Last updated**: 2026-07-23
> This document consolidates all completed feature milestones from `docs/superpowers/plans/` and `docs/superpowers/specs/`. Individual planning documents have been archived here.

---

## Milestone 1: A-Share Data Dimension Integration

| Field | Value |
|---|---|
| **Date** | 2026-05-15 |
| **Status** | ✅ **Completed** |
| **Spec** | `docs/superpowers/specs/2026-05-15-ashare-data-integration-design.md` |

### Summary
Integrated 6 A-share-specific data dimensions into the TradingAgents pipeline via 2 new analyst nodes (Sentiment, Industry) and enhancements to 2 existing nodes (Fundamentals, Governance).

### Key Deliverables
- **7 new data fetchers** in `akshare_vendor.py`: `get_fund_flow`, `get_northbound_holdings`, `get_restricted_release`, `get_industry_valuation`, `get_macro_indicators`, `get_analyst_forecast`, `get_institution_holdings`
- **New tool modules**: `sentiment_data_tools.py`, `industry_data_tools.py`
- **New analyst nodes**: `sentiment_analyst.py`, `industry_analyst.py`
- **Vendor routing** in `interface.py` with AkShare → yfinance fallback chain
- **Unit tests** for all new data fetchers and tool wrappers
- **Graph integration**: ToolNodes, conditional logic, CLI model enum

---

## Milestone 2: Batch Pipeline & Profile/Watchlist System

| Field | Value |
|---|---|
| **Date** | 2026-05-10 |
| **Status** | ✅ **Completed** |
| **Spec** | `docs/superpowers/specs/2026-05-10-batch-pipeline-design.md` |

### Summary
Added a profile system to save analysis preferences, a watchlist system to manage stock lists, and a batch pipeline with auto-save, auto-translation, and a batch dashboard for unattended multi-stock analysis.

### Key Deliverables
- `cli/profiles.py` — Profile CRUD (save, load, list, delete)
- `cli/watchlists.py` — Watchlist CRUD (save, load, list, parse)
- `cli/batch_dashboard.py` — Rich Live layout for batch monitoring
- `cli/batch_runner.py` — Multi-stock orchestrator with failure handling
- CLI mode selection: batch watchlist scan vs custom ticker query
- `batch_summary.md` + `batch_summary.json` per batch
- Auto-translation for Chinese output
- 11 unit tests across all new modules

---

## Milestone 3: Upstream Merge (TauricResearch/TradingAgents)

| Field | Value |
|---|---|
| **Date** | 2026-06-06 |
| **Status** | ✅ **Completed** |

### Summary
Merged 53 upstream commits, resolved 28 file conflicts (197 conflict markers), fixed 35 test failures, and added 3 new LLM providers.

### Key Deliverables
- Merged upstream features: Crypto analysis mode, Sentiment Analyst, MiniMax LLM, model catalog refresh (GPT-5.5, Claude Opus 4.7, Grok 4.20), analyst execution timing, market data validator
- Preserved all A-share customizations (AkShare vendor, smartmoney DB, ticker resolver, portfolio module, report auditor, CLI batch modules)
- Added 3 new LLM providers: Agnes AI (free tier), ModelScope (daily 2000 calls), NVIDIA NIM (H200/B200 optimized)
- Fixed CLI timestamp `NameError` bug
- All 432 unit tests passing

---

## Milestone 4: Project Cleanup & README Rebrand

| Field | Value |
|---|---|
| **Date** | 2026-06-06 |
| **Status** | ✅ **Completed** |

### Summary
Cleaned up project structure, rebranded README for A-share focus, removed stale documentation.

### Key Deliverables
- .gitignore updates
- README.md rewritten with A-share focus, data hub integration, Chinese documentation
- Asset images added for architecture diagrams
- Docker support
- Fixed remaining test issues post-merge

---

## Milestone 5: Data Missing Prevention

| Field | Value |
|---|---|
| **Date** | 2026-07-02 |
| **Status** | ✅ **Completed** |

### Summary
Prevented data fabrication by adding explicit missing-data handling prompts to 5 analyst agents, a lightweight report quality validation gate, and data quality summary injection into downstream prompts.

### Key Deliverables
- **Prompt patches**: Standardized "Missing Data Protocol" appended to `system_message` in 5 analyst files (market, news, fundamentals, governance, industry)
- **`validate_report_quality()`** — Tags reports as reliable/unreliable/sparse
- **`data_quality_summary`** field on `AgentState` — Passed into bull/bear, research manager, trader, and PM prompts
- **`build_data_quality_summary()`** — Aggregates per-node data availability
- Unit tests: `test_analyst_prompts_missing_data.py`, `test_report_quality_validation.py`

---

## Milestone 6: Data Readiness Pre-check

| Field | Value |
|---|---|
| **Date** | 2026-07-03 |
| **Status** | ✅ **Completed** |

### Summary
Added a data readiness pre-check step in the CLI interaction flow, showing cache/availability status before analysis begins.

### Key Deliverables
- `tradingagents/agents/utils/data_readiness.py` — `check_data_readiness()` function, `ReadinessReport`/`ReadinessItem` dataclasses, `ANALYST_DATA_REQUIREMENTS` mapping
- Rich Panel + Table display with color-coded status (✅ cached, ✅ available, ❌ unavailable)
- Cache checks for OHLCV, fund flow, financial statements via smartmoney_db
- CLI integration: Step 3.5 in `get_user_selections()` after analyst selection
- Optional cancellation prompt when data is missing
- 6 unit tests

---

## Milestone 7: Batch UX Improvements

| Field | Value |
|---|---|
| **Date** | 2026-07-08 |
| **Status** | ✅ **Completed** |

### Summary
Eliminated yfinance "Failed download" stdout noise and added per-ticker real-time stage status in concurrent batch dashboard.

### Key Deliverables
- **`_silent_yf_download()`** — Thread-safe stdout suppression for yfinance downloads
- **`per_ticker_meta`** field on `BatchDashboard` — Tracks `{ticker: {stage, progress, agent}}`
- **Per-ticker status table** in concurrent dashboard — Shows live stage per worker
- Thread-safe meta updates via `self._lock`

---

## Milestone 8: P1 Closure

| Field | Value |
|---|---|
| **Date** | 2026-07-18 |
| **Status** | ✅ **Completed** |

### Summary
Closed remaining P1 gaps: rate-limit failure preservation, structured fallback propagation through LangGraph, and fundamentals snapshot correctness with signed/source-aware metrics.

### Key Deliverables
#### Task 1: Rate-limit retention
- `VendorRateLimitError` typed exception preserved through `route_to_vendor()` chain
- `_safe_prefetch()` wrapper catches exceptions and returns `DATA_UNAVAILABLE` placeholders
- Sentiment analyst gracefully degrades when news API rate-limited

#### Task 2: Structured fallback propagation
- `structured_fallback_agents: Annotated[list[str], operator.add]` field on AgentState
- Research Manager, Trader, Portfolio Manager return fallback agent names when unstructured output used
- `FALLBACK_MARKER` detection in batch summary rendering

#### Task 3: Fundamentals snapshots
- `VendorRouteResult` dataclass — Source-aware routing with `symbol`, `as_of`, `vendor`
- `route_to_vendor_with_source()` — Source-aware variant of `route_to_vendor()`
- Signed-number regex for all numeric patterns
- Error-only snapshots never render verified-table headings

---

## Milestone 9: Batch Model Usage Tracking

| Field | Value |
|---|---|
| **Date** | 2026-07-12 |
| **Status** | ✅ **Completed** |

### Summary
Extended batch output to show per-model LLM call counts and token usage in both `batch_summary.md` and `batch_summary.json`.

### Key Deliverables
- `StatsCallbackHandler.llm_calls_by_model` — Per-model call tracking
- `## Batch Usage` table in `batch_summary.md` (Model, Calls, Tokens In, Tokens Out, Cost)
- `batch_summary.json` totals include `llm_calls`, `calls_by_model`, `tokens_by_model`
- Per-ticker JSON entries include per-model breakdown
- Live dashboard footer updated with rolled-up batch totals

---

## Milestone 10: v2.0 Robustness & Optimization Package

| Field | Value |
|---|---|
| **Date** | 2026-07-22 → 2026-07-23 |
| **Status** | ✅ **Completed** (PR #40) |
| **Spec** | `docs/superpowers/specs/2026-07-22-quant-data-agent-enhancement-design.md` |

### Summary
Integrated high-alpha data from `quant_data` database into multi-agent architecture with 4 robustness guards: data freshness, tool consolidation, sample size validation, sector-relative valuation.

### Key Deliverables
#### Data Layer
- **`get_chip_distribution`** — Profit ratio, avg cost, concentration, price-to-cost bias
- **`get_historical_valuation`** — 3-year PE/PB percentiles, sector-relative discount, cyclical stock trap detection
- **`get_institutional_intelligence`** — Merged institutional holdings + survey (replaced separate tools)
- **`get_earnings_forecast`** — Pre-announcement type and YoY growth
- **`get_earnings_estimates`** — Analyst consensus estimates
- **`get_shareholder_count`** — Shareholder count trends
- **`get_dividend_history`** — Dividend payment history
- **`get_concept_board`** — Concept theme membership and heat scores

#### Robustness Guards
1. **Data Freshness Guard** — Warning when data lags >2 days behind current date
2. **Tool Consolidation** — Merged `get_institutional_intelligence` reducing governance ToolNode size
3. **Sample Size Guard** — Warning when historical records < 120 (sub-new stock protection)
4. **Cyclical Trap Guard** — PE < 8 warning for cyclical sectors (steel, coal, shipping, chemicals)

#### Analyst Registration
- **MarketAnalyst**: `get_chip_distribution`, `get_limit_up_down`, `get_index_daily`, `get_verified_market_snapshot`
- **FundamentalsAnalyst**: `get_historical_valuation`, `get_earnings_forecast`, `get_earnings_estimates`, `get_shareholder_count`, `get_dividend_history`
- **GovernanceAnalyst**: `get_institutional_intelligence`, `get_block_trade`, `get_margin_trading`, `get_pledge_ratio`, `get_dragon_tiger`
- **IndustryAnalyst**: `get_concept_board`, `get_macro_indicators`, `get_sector_fund_flow`
- **SentimentAnalyst**: Eastmoney hot keywords (`fetch_eastmoney_hot_keywords`)

#### Cailianpress Integration
- New `cailianpress_vendor.py` module for 财联社 flash news
- `get_cailianpress_telegrams` tool registered in news_analyst
- 27 unit tests + smoke test script
- API endpoint fix: `nodeapi/updateTelegraphList` → `v1/roll/get_roll_list`

---

## Future / In Planning

| Item | Status | Notes |
|---|---|---|
| **Quant Hunter Upgrade** | 🔄 Design Phase | `docs/superpowers/specs/2026-07-22-quant-hunter-upgrade-plan.md` — Not yet implemented. Aims to add chip distribution, valuation percentile, institutional survey, and earnings momentum filters to the A-share screener engine. |
