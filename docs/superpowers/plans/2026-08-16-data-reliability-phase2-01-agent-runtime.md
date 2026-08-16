# Agent Runtime Data Correctness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every agent data request market-aware, semantically classified, deduplicated within one ticker run, and filtered by asset capability before any external vendor call.

**Architecture:** Add one runtime context containing canonical ticker, market, and resolved dates. A centralized `DataPolicyRegistry` normalizes requests and tool applicability. The existing vendor router consumes normalized requests, returns backward-compatible text plus richer diagnostics, and delegates same-run single-flight behavior to `RequestMemo`. Reports aggregate logical requests rather than raw retries.

**Tech Stack:** Python 3.10+, `exchange_calendars`, ContextVar, threading primitives, LangChain tools, LangGraph state, pytest, Ruff.

## Global Constraints

- Work in an isolated `quant_agents` feature branch/worktree, not `main`.
- Use `rtk`; run scripts via `rtk uv run python ...`.
- Preserve `route_to_vendor()` return values and all current public tool signatures.
- Do not use the local database's latest row to define exchange sessions.
- Cache only stable outcomes; never cache transient `failed` outcomes.
- Do not add prompt-local applicability rules. `DataPolicyRegistry` is the sole source of policy truth.
- Keep old checkpoints readable by defaulting new state fields.
- Do not change analyst execution order or investment logic.

---

### Task 1: Add canonical market identity and exchange-session dates

**Files:**
- Modify: `pyproject.toml`
- Regenerate: `uv.lock`
- Create: `tradingagents/market_context.py`
- Modify: `tradingagents/ticker_resolver.py`
- Create: `tests/test_market_context.py`
- Test: `tests/test_ticker_resolver.py`

**Interfaces:**
- Produces `Market = Literal["XSHG", "XHKG", "XNYS", "CRYPTO", "UNKNOWN"]`.
- Produces `AnalysisDates(analysis_date, market_as_of_date, evidence_window_end)`.
- Produces `resolve_analysis_dates(ticker, analysis_date) -> AnalysisDates`.

- [ ] **Step 1: Add failing session-resolution tests.**

  Cover at least:

  ```python
  assert infer_market("002413.SZ") == "XSHG"
  assert infer_market("1810.HK") == "XHKG"
  assert resolve_analysis_dates("002413.SZ", "2026-08-16") == AnalysisDates(
      analysis_date="2026-08-16",
      market_as_of_date="2026-08-14",
      evidence_window_end="2026-08-16",
  )
  ```

  Add a mainland holiday and an XHKG-only holiday case so weekday subtraction cannot satisfy the tests.

- [ ] **Step 2: Verify the tests fail because the new module is absent.**

  ```bash
  rtk uv run python -m pytest tests/test_market_context.py tests/test_ticker_resolver.py -m unit -q
  ```

- [ ] **Step 3: Add `exchange-calendars` and implement the resolver.**

  Add an explicit compatible dependency to `pyproject.toml`, regenerate `uv.lock`, and use `exchange_calendars.get_calendar("XSHG"/"XHKG")`. Normalize timestamps before calling `date_to_session(..., direction="previous")`. Keep US/crypto behavior explicit: XNYS uses its calendar; crypto uses the analysis date.

- [ ] **Step 4: Run focused tests and dependency integrity.**

  ```bash
  rtk uv lock --check
  rtk uv run python -m pytest tests/test_market_context.py tests/test_ticker_resolver.py -m unit -q
  ```

- [ ] **Step 5: Commit the market/date slice.**

  ```bash
  rtk git add pyproject.toml uv.lock tradingagents/market_context.py tradingagents/ticker_resolver.py tests/test_market_context.py tests/test_ticker_resolver.py
  rtk git commit -m "feat: resolve analysis dates by exchange session"
  ```

### Task 2: Centralize data policies and runtime context

**Files:**
- Create: `tradingagents/dataflows/data_policy.py`
- Create: `tradingagents/dataflows/runtime_context.py`
- Modify: `tradingagents/graph/trading_graph.py`
- Modify: `cli/main.py`
- Modify: `tradingagents/graph/propagation.py`
- Modify: `tradingagents/agents/utils/agent_states.py`
- Create: `tests/test_data_policy.py`
- Create: `tests/test_runtime_data_context.py`

**Interfaces:**
- Produces immutable `ToolPolicy` with markets, date policy, empty semantics, impact, and allowed vendors.
- Produces `RuntimeDataContext(ticker, market, dates, policy_version)` and `use_runtime_data_context(...)`.
- Consumes the ticker/date passed once at graph entry.

- [ ] **Step 1: Write failing registry and context-isolation tests.**

  Assert policies for `get_limit_up_down`, `get_restricted_release`, `get_news`, `get_company_announcements`, `get_northbound_hold`, and `get_pledge_ratio`. Assert two threads/context copies cannot see each other's ticker or dates.

- [ ] **Step 2: Run the tests and confirm missing interfaces.**

  ```bash
  rtk uv run python -m pytest tests/test_data_policy.py tests/test_runtime_data_context.py -m unit -q
  ```

- [ ] **Step 3: Implement the registry without fallback defaults that hide omissions.**

  Expose:

  ```python
  def policy_for(method: str) -> ToolPolicy: ...
  def is_applicable(method: str, market: Market) -> bool: ...
  def normalized_dates(method: str, context: RuntimeDataContext) -> tuple[str, str | None]: ...
  ```

  Unknown methods must raise a policy error in tests and log a compatibility warning only through an explicit legacy policy in production.

- [ ] **Step 4: Establish the context at both graph entry paths.**

  Wrap `TradingAgentsGraph.propagate()`'s `_run_graph()` and the streaming block in `cli/main.py` with the same context manager. Add `analysis_dates` and `market` to initial state with backward-compatible defaults.

- [ ] **Step 5: Run graph/state regressions and commit.**

  ```bash
  rtk uv run python -m pytest tests/test_data_policy.py tests/test_runtime_data_context.py tests/test_trading_graph.py tests/test_propagation.py -m unit -q
  rtk git add tradingagents/dataflows/data_policy.py tradingagents/dataflows/runtime_context.py tradingagents/graph/trading_graph.py cli/main.py tradingagents/graph/propagation.py tradingagents/agents/utils/agent_states.py tests/test_data_policy.py tests/test_runtime_data_context.py
  rtk git commit -m "feat: centralize data request policy"
  ```

### Task 3: Introduce explicit vendor payload and route semantics

**Files:**
- Modify: `tradingagents/dataflows/interface.py`
- Modify: `tradingagents/reporting.py`
- Modify: `tests/test_interface_routing.py`
- Modify: `tests/test_report_coverage.py`

**Interfaces:**
- Produces `VendorPayload(data, status, as_of, reason)` for new adapters.
- Extends diagnostic status to `ok`, `ok_fallback`, `valid_empty`, `not_applicable`, `partial`, `stale`, `no_data`, `unavailable`, `failed`.
- Keeps legacy adapter strings equivalent to `VendorPayload(..., status="ok")`.

- [ ] **Step 1: Add a status matrix as failing parameterized tests.**

  Include: primary success, fallback success, covered empty event window, unsupported HK tool, partial evidence, stale snapshot, clean no-data, unconfigured source, and provider exception.

- [ ] **Step 2: Confirm old routing misclassifies fallback and valid empty.**

  ```bash
  rtk uv run python -m pytest tests/test_interface_routing.py tests/test_report_coverage.py -m unit -q
  ```

- [ ] **Step 3: Normalize policy and payload before/after vendor calls.**

  Before building a chain, return a `not_applicable` diagnostic when policy excludes the current market. Rewrite market-session date arguments using the runtime context. After calls, unwrap `VendorPayload`; set `ok_fallback` whenever a non-primary vendor succeeds. Do not infer `valid_empty` from an arbitrary empty string—only an adapter with complete coverage may declare it.

- [ ] **Step 4: Render neutral statuses separately from degraded statuses.**

  In `render_data_coverage_section()`, label `ok_fallback`, `valid_empty`, and `not_applicable` as explained/non-missing. Count only `partial`, `stale`, `no_data`, `unavailable`, and `failed` as degraded.

- [ ] **Step 5: Run routing/report tests and commit.**

  ```bash
  rtk uv run python -m pytest tests/test_interface_routing.py tests/test_report_coverage.py tests/test_batch_runner.py -m unit -q
  rtk git add tradingagents/dataflows/interface.py tradingagents/reporting.py tests/test_interface_routing.py tests/test_report_coverage.py
  rtk git commit -m "feat: classify vendor outcomes precisely"
  ```

### Task 4: Deduplicate logical requests with per-ticker single-flight

**Files:**
- Create: `tradingagents/dataflows/request_memo.py`
- Modify: `tradingagents/dataflows/interface.py`
- Modify: `tradingagents/dataflows/runtime_context.py`
- Create: `tests/test_request_memo.py`
- Modify: `tests/test_interface_routing.py`

**Interfaces:**
- Produces `RequestKey(method, ticker, start_date, end_date, frozen_kwargs, policy_version)`.
- Produces a context-scoped `RequestMemo.resolve(key, callable) -> VendorRouteResult`.
- Exposes diagnostic `call_count` while retaining one logical coverage row.

- [ ] **Step 1: Write failing cache and concurrency tests.**

  Verify two equivalent date formats normalize to one key, five concurrent callers execute the resolver once, successful fallback is cached, and a failed first call is retried on the second request.

- [ ] **Step 2: Run tests and verify repeated calls execute multiple times.**

  ```bash
  rtk uv run python -m pytest tests/test_request_memo.py tests/test_interface_routing.py -m unit -q
  ```

- [ ] **Step 3: Implement thread-safe single-flight.**

  Use a lock plus per-key event/future. Cache `ok`, `ok_fallback`, `valid_empty`, and `not_applicable`; do not cache `failed`. Preserve exception identity for waiting callers and remove failed in-flight entries.

- [ ] **Step 4: Place memoization around the full vendor chain.**

  Build the key only after ticker/date normalization and before `_build_vendor_chain()`. This guarantees each vendor is called at most once for one logical request, even if multiple analysts ask for it.

- [ ] **Step 5: Run concurrency/routing tests and commit.**

  ```bash
  rtk uv run python -m pytest tests/test_request_memo.py tests/test_interface_routing.py -m unit -q
  rtk git add tradingagents/dataflows/request_memo.py tradingagents/dataflows/interface.py tradingagents/dataflows/runtime_context.py tests/test_request_memo.py tests/test_interface_routing.py
  rtk git commit -m "feat: deduplicate ticker data requests"
  ```

### Task 5: Filter tools by market before LLM binding and prefetch

**Files:**
- Modify: `tradingagents/graph/setup.py`
- Modify: `tradingagents/agents/analysts/market_analyst.py`
- Modify: `tradingagents/agents/analysts/news_analyst.py`
- Modify: `tradingagents/agents/analysts/sentiment_analyst.py`
- Modify: `tradingagents/agents/analysts/governance_analyst.py`
- Modify: `tradingagents/agents/analysts/industry_analyst.py`
- Modify: `tradingagents/agents/analysts/fundamentals_analyst.py`
- Create: `tradingagents/agents/utils/tool_capabilities.py`
- Create: `tests/test_tool_capabilities.py`
- Modify: `tests/test_news_analyst.py`
- Modify: `tests/test_structured_agents.py`

**Interfaces:**
- Produces `tools_for_market(tools, market) -> list[BaseTool]` backed only by `DataPolicyRegistry`.
- Analyst factories consume state-time market identity before `bind_tools()` and before manual prefetch.

- [ ] **Step 1: Write failing A-share/HK tool-set tests.**

  Assert HK excludes limit-up/down, northbound, dragon-tiger, A-share margin, CNINFO, and A-share pledge; A-share still receives them. Patch every excluded tool and assert zero calls during `1810.HK` analyst nodes.

- [ ] **Step 2: Run focused analyst tests.**

  ```bash
  rtk uv run python -m pytest tests/test_tool_capabilities.py tests/test_news_analyst.py tests/test_structured_agents.py -m unit -q
  ```

- [ ] **Step 3: Move binding to state-aware node execution.**

  Keep graph topology static, but build/cache the bound LLM per market inside each analyst callable. Apply the same filter to sentiment prefetch functions so hidden Python calls cannot bypass tool filtering.

- [ ] **Step 4: Preserve unsupported direct calls as `not_applicable`.**

  The router guard from Task 3 remains mandatory for programmatic callers even after the LLM tool list is filtered.

- [ ] **Step 5: Run analyst/graph tests and commit.**

  ```bash
  rtk uv run python -m pytest tests/test_tool_capabilities.py tests/test_news_analyst.py tests/test_structured_agents.py tests/test_graph_setup.py -m unit -q
  rtk git add tradingagents/graph/setup.py tradingagents/agents/analysts tradingagents/agents/utils/tool_capabilities.py tests/test_tool_capabilities.py tests/test_news_analyst.py tests/test_structured_agents.py
  rtk git commit -m "feat: bind market-capable analyst tools"
  ```

### Task 6: Aggregate logical data reliability and audit false positives

**Files:**
- Modify: `cli/batch_runner.py`
- Modify: `scripts/report_auditor.py`
- Modify: `tradingagents/reporting.py`
- Modify: `tests/test_batch_runner.py`
- Modify: `tests/test_report_auditor_crossval.py`
- Modify: `tests/test_report_coverage.py`

**Interfaces:**
- Produces `data_reliability` JSON counts: `confirmed`, `fallback_success`, `valid_empty`, `not_applicable`, `partial`, `missing`.
- Deduplicates by normalized logical request while preserving `call_count`.

- [ ] **Step 1: Add failing summary/audit fixtures.**

  Build coverage rows containing duplicate requests and every status. Assert neutral states do not increase missing counts and A-share/HK denominators include only applicable policies.

- [ ] **Step 2: Implement one aggregation helper shared by report and batch code.**

  Create a pure helper in `reporting.py`; do not duplicate status sets in `batch_runner.py` and `report_auditor.py`.

- [ ] **Step 3: Extend auditor JSON and Markdown.**

  Add coverage health findings and logical request counts without removing existing financial consistency checks.

- [ ] **Step 4: Run focused and authoritative verification.**

  ```bash
  rtk uv run python -m pytest tests/test_batch_runner.py tests/test_report_auditor_crossval.py tests/test_report_coverage.py -m unit -q
  rtk uv run ruff check .
  rtk uv run python -m pytest -m unit
  rtk git -c core.fsmonitor=false diff --check
  ```

- [ ] **Step 5: Commit and run the deterministic acceptance fixture.**

  ```bash
  rtk git add cli/batch_runner.py scripts/report_auditor.py tradingagents/reporting.py tests/test_batch_runner.py tests/test_report_auditor_crossval.py tests/test_report_coverage.py
  rtk git commit -m "feat: report logical data reliability"
  rtk uv run python -m pytest tests/test_market_context.py tests/test_request_memo.py tests/test_tool_capabilities.py tests/test_interface_routing.py -m unit -q
  ```

  Expected: Sunday A-share request uses `2026-08-14`; duplicate requests resolve once; HK A-share-only call count is zero; all status totals match the fixture.
