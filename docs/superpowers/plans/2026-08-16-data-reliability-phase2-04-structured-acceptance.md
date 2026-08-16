# Structured Output Reliability and Acceptance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce malformed decision outputs with capability-aware JSON methods and one bounded repair step, expose generation reliability separately from data reliability, and verify the complete Phase 2 change against the fixed original 18-report manifest.

**Architecture:** Model capabilities choose `json_schema`, `json_mode`, `function_calling`, or explicit free text. Structured invocation validates the native response, attempts deterministic local extraction/alias cleanup, permits at most one schema-only repair request, then falls back to marked free text. Context-local diagnostics flow through LangGraph state into per-ticker reports, batch JSON, and the auditor independently of vendor diagnostics.

**Tech Stack:** Python 3.10+, LangChain OpenAI-compatible clients, Pydantic v2, ContextVar, pytest, Ruff, existing ResearchPlan/TraderProposal/PortfolioDecision schemas.

## Global Constraints

- Implement in an isolated `quant_agents` feature branch/worktree, not `main`.
- Use `rtk`; run Python via `rtk uv run python ...`.
- Preserve existing decision schemas and rendered Markdown contracts unless a failing test proves an alias is needed.
- Never fabricate prices, entry points, stops, position sizes, ratings, or confidence during local/LLM repair.
- Missing numeric values remain `null`; repair may normalize only syntax and known aliases.
- Permit at most one repair-model invocation per decision node.
- A repair/fallback failure must not crash the batch.
- Keep `structured_fallback_agents` as explicit graph state for backward compatibility; do not mutate the input state with private flags.
- Data reliability and decision-generation reliability must use separate state fields and report sections.
- The `<=2/18` Structured fallback target is a live acceptance target, not a unit-test guarantee.

---

### Task 1: Select structured method from verified model capabilities

**Files:**
- Modify: `tradingagents/llm_clients/capabilities.py`
- Modify: `tradingagents/llm_clients/openai_client.py`
- Modify: `tests/test_capabilities.py`
- Modify: `tests/test_openai_client.py`
- Modify: `tests/test_structured_bind.py`

**Interfaces:**
- Produces ordered method choice `json_schema -> json_mode -> function_calling -> free_text` based on actual capability flags.
- DeepSeek V4 family selects `json_mode` instead of function calling.

- [ ] **Step 1: Add failing capability table tests.**

  Cover SenseNova selected profile model, DeepSeek V4 Flash/Pro patterns, DeepSeek reasoner, MiniMax, a model with strict JSON schema, a function-calling-only model, and an unknown model. Assert unsupported methods are never attempted.

- [ ] **Step 2: Confirm current DeepSeek expectation fails.**

  ```bash
  rtk uv run python -m pytest tests/test_capabilities.py tests/test_openai_client.py tests/test_structured_bind.py -m unit -q
  ```

- [ ] **Step 3: Make method choice capability-driven.**

  Add a pure helper:

  ```python
  def structured_method(caps: ModelCapabilities) -> Literal[
      "json_schema", "json_mode", "function_calling", "free_text"
  ]: ...
  ```

  `NormalizedChatOpenAI.with_structured_output()` passes only supported method values. Keep DeepSeek reasoning sidecar behavior intact.

- [ ] **Step 4: Run tests and commit.**

  ```bash
  rtk uv run python -m pytest tests/test_capabilities.py tests/test_openai_client.py tests/test_structured_bind.py -m unit -q
  rtk git add tradingagents/llm_clients/capabilities.py tradingagents/llm_clients/openai_client.py tests/test_capabilities.py tests/test_openai_client.py tests/test_structured_bind.py
  rtk git commit -m "fix: choose supported structured output method"
  ```

### Task 2: Add deterministic JSON extraction and alias normalization

**Files:**
- Create: `tradingagents/agents/utils/structured_repair.py`
- Modify: `tradingagents/agents/schemas.py`
- Create: `tests/test_structured_repair.py`
- Modify: `tests/test_structured_bind.py`

**Interfaces:**
- Produces `extract_json_object(text) -> dict[str, Any]` using a quote/escape-aware scanner.
- Produces `normalize_known_aliases(payload, schema) -> dict[str, Any]`.
- Produces `validate_repaired_payload(payload, schema) -> BaseModel`.

- [ ] **Step 1: Write failing hostile-input tests.**

  Include fenced JSON, prose before/after JSON, braces inside strings, escaped quotes, multiple JSON objects, arrays instead of objects, unknown fields, Chinese/legacy field aliases, invalid enum, and omitted required investment values.

- [ ] **Step 2: Implement deterministic extraction without regex-only brace matching.**

  Accept exactly one top-level JSON object. If multiple plausible objects exist, return an explicit ambiguity error. Strip Markdown fences only around the selected object.

- [ ] **Step 3: Normalize only an explicit alias map.**

  Put aliases beside schemas or in one schema-keyed map. Reject unknown semantic rewrites. Pydantic validation decides whether nulls/required fields are acceptable; repair code must not supply defaults that imply market facts.

- [ ] **Step 4: Run tests and commit.**

  ```bash
  rtk uv run python -m pytest tests/test_structured_repair.py tests/test_structured_bind.py -m unit -q
  rtk git add tradingagents/agents/utils/structured_repair.py tradingagents/agents/schemas.py tests/test_structured_repair.py tests/test_structured_bind.py
  rtk git commit -m "feat: repair structured JSON deterministically"
  ```

### Task 3: Implement one bounded repair call and diagnostics

**Files:**
- Modify: `tradingagents/agents/utils/structured.py`
- Create: `tradingagents/agents/utils/structured_diagnostics.py`
- Modify: `tradingagents/agents/managers/research_manager.py`
- Modify: `tradingagents/agents/trader/trader.py`
- Modify: `tradingagents/agents/managers/portfolio_manager.py`
- Modify: `tradingagents/agents/analysts/sentiment_analyst.py`
- Modify: `tests/test_structured_bind.py`
- Modify: `tests/test_structured_agents.py`
- Modify: `tests/test_trader_edge_cases.py`
- Modify: `tests/test_portfolio_manager.py`

**Interfaces:**
- Produces `StructuredInvocationDiagnostic(agent_name, model, structured_method, attempt_count, failure_kind, repair_used, fallback_used, validation_summary)`.
- Produces context manager `collect_structured_diagnostics()` analogous to route diagnostics.
- Extends `invoke_structured_or_freetext()` with an injected repair callable/model and no hidden extra retries.

- [ ] **Step 1: Add a complete failing transition matrix.**

  Test native success; native validation failure then local success; local failure then repair success; repair failure then free-text fallback; unsupported structured mode; transport failure; empty result. Assert exact LLM call counts and diagnostic fields.

- [ ] **Step 2: Implement the bounded invocation state machine.**

  Required order:

  1. invoke provider-native structured output;
  2. if raw text exists, deterministic local extraction/validation;
  3. invoke one repair prompt containing only schema, invalid output, and instruction not to invent values;
  4. invoke/retain marked free text if still invalid.

  No branch may call the repair model twice. Network/timeout errors may skip repair when there is no malformed payload to repair.

- [ ] **Step 3: Collect diagnostics without changing rendered decision contracts.**

  Existing callers still receive Markdown strings. Append `FALLBACK_MARKER` only on final free-text fallback, not after successful local/LLM repair.

- [ ] **Step 4: Update all current structured callers.**

  Pass stable agent names and model/method metadata for Research Manager, Trader, Portfolio Manager, and Sentiment Analyst. Preserve existing confidence/rating parsing behavior.

- [ ] **Step 5: Run focused tests and commit.**

  ```bash
  rtk uv run python -m pytest tests/test_structured_repair.py tests/test_structured_bind.py tests/test_structured_agents.py tests/test_trader_edge_cases.py tests/test_portfolio_manager.py -m unit -q
  rtk git add tradingagents/agents/utils/structured.py tradingagents/agents/utils/structured_diagnostics.py tradingagents/agents/managers/research_manager.py tradingagents/agents/trader/trader.py tradingagents/agents/managers/portfolio_manager.py tradingagents/agents/analysts/sentiment_analyst.py tests/test_structured_bind.py tests/test_structured_agents.py tests/test_trader_edge_cases.py tests/test_portfolio_manager.py
  rtk git commit -m "feat: add bounded structured output repair"
  ```

### Task 4: Propagate decision reliability through graph state

**Files:**
- Modify: `tradingagents/agents/utils/agent_states.py`
- Modify: `tradingagents/graph/propagation.py`
- Modify: `tradingagents/graph/trading_graph.py`
- Modify: `cli/main.py`
- Modify: `tests/test_propagation.py`
- Modify: `tests/test_trading_graph.py`
- Modify: `tests/cli/test_main.py`

**Interfaces:**
- Adds `decision_reliability: list[dict]` to state with an empty default.
- Keeps `structured_fallback_agents` populated only for final fallback.

- [ ] **Step 1: Write failing context/state tests.**

  Assert concurrent ticker runs cannot mix diagnostics, old checkpoint states default to an empty list, and a repaired success does not appear in `structured_fallback_agents`.

- [ ] **Step 2: Wrap both graph execution paths with the diagnostic collector.**

  Collect beside `collect_route_diagnostics()` in `TradingAgentsGraph` and `cli/main.py`; convert dataclasses with `asdict()` after execution. Ensure exception paths still preserve completed diagnostics when a report is written.

- [ ] **Step 3: Run graph/CLI tests and commit.**

  ```bash
  rtk uv run python -m pytest tests/test_propagation.py tests/test_trading_graph.py tests/cli/test_main.py -m unit -q
  rtk git add tradingagents/agents/utils/agent_states.py tradingagents/graph/propagation.py tradingagents/graph/trading_graph.py cli/main.py tests/test_propagation.py tests/test_trading_graph.py tests/cli/test_main.py
  rtk git commit -m "feat: propagate decision reliability diagnostics"
  ```

### Task 5: Separate decision reliability from data reliability in reports

**Files:**
- Modify: `tradingagents/reporting.py`
- Modify: `cli/batch_runner.py`
- Modify: `scripts/report_auditor.py`
- Modify: `tests/test_report_coverage.py`
- Modify: `tests/test_batch_runner.py`
- Modify: `tests/test_report_auditor_crossval.py`

**Interfaces:**
- Produces a “决策生成可靠性” report section.
- Adds `decision_reliability` to each `batch_summary.json` ticker row.
- Preserves separate `data_reliability` counts from Plan 1.

- [ ] **Step 1: Add failing report and JSON tests.**

  Assert native structured success, local repair, LLM repair, and final fallback render distinctly. Assert a provider fallback never sets a structured fallback field, and a structured fallback never increments data missing counts.

- [ ] **Step 2: Implement one shared decision-reliability aggregator.**

  Expected JSON shape:

  ```json
  {
    "structured_success": true,
    "repair_used": false,
    "fallback_agents": [],
    "agents": [{"agent_name": "Research Manager", "structured_method": "json_mode"}]
  }
  ```

  Keep existing top-level `fallback` temporarily for backward compatibility, but define it from final Structured fallback only and mark it deprecated in code comments/tests.

- [ ] **Step 3: Extend auditor findings.**

  Add rule IDs for repair usage and final structured fallback. The auditor should aggregate by agent/model/method and report exact denominator; repairs are warnings/telemetry, not automatic report failures.

- [ ] **Step 4: Run and commit.**

  ```bash
  rtk uv run python -m pytest tests/test_report_coverage.py tests/test_batch_runner.py tests/test_report_auditor_crossval.py -m unit -q
  rtk git add tradingagents/reporting.py cli/batch_runner.py scripts/report_auditor.py tests/test_report_coverage.py tests/test_batch_runner.py tests/test_report_auditor_crossval.py
  rtk git commit -m "feat: report decision generation reliability"
  ```

### Task 6: Add a reproducible baseline comparison tool

**Files:**
- Create: `scripts/compare_reliability.py`
- Create: `tests/test_compare_reliability.py`

**Interfaces:**
- Consumes baseline and candidate batch directories.
- Produces deterministic JSON/Markdown metrics without invoking LLMs or vendors.
- Supports `--scope-manifest PATH`; the committed manifest is the explicit 18-report acceptance denominator.

- [ ] **Step 1: Add failing fixture-based comparison tests.**

  Calculate ticker counts, applicable logical requests, confirmed/fallback/valid-empty/not-applicable/partial/missing counts, duplicate call counts, source failures, structured success/repair/fallback by agent, failed tickers, runtime, and report presence.

- [ ] **Step 2: Implement strict input validation.**

  Fail with a clear message if either batch lacks `batch_summary.json`, the manifest count disagrees with its ticker list, or either batch lacks a report in the manifest scope. Never silently compare different ticker sets. Report candidate-only tickers but exclude them from manifest-scoped metrics; without `--scope-manifest`, require `--allow-ticker-diff` to continue.

- [ ] **Step 3: Add threshold flags for CI/manual acceptance.**

  Support `--max-structured-fallback 2`, `--max-failed-tickers 0`, and `--require-zero-hk-ashare-calls`. Exit nonzero on threshold breach while still writing the comparison report.

- [ ] **Step 4: Run and commit.**

  ```bash
  rtk uv run python -m pytest tests/test_compare_reliability.py -m unit -q
  rtk git add scripts/compare_reliability.py tests/test_compare_reliability.py
  rtk git commit -m "test: compare batch reliability baselines"
  ```

### Task 7: Run authoritative verification and offline acceptance

- [ ] **Step 1: Run focused regression suites.**

  ```bash
  rtk uv run python -m pytest tests/test_capabilities.py tests/test_openai_client.py tests/test_structured_repair.py tests/test_structured_bind.py tests/test_structured_agents.py tests/test_report_coverage.py tests/test_batch_runner.py tests/test_compare_reliability.py -m unit -q
  ```

- [ ] **Step 2: Run authoritative repository checks.**

  ```bash
  rtk uv run ruff check .
  rtk uv run python -m pytest -m unit
  rtk git -c core.fsmonitor=false diff --check
  rtk git -c core.fsmonitor=false status --short
  ```

- [ ] **Step 3: Run deterministic no-network acceptance fixtures.**

  Confirm: weekend market date normalization; same request resolves once; HK A-share-only calls are zero; old evidence schema falls back; partial windows query only gaps; one repair maximum; final reports contain separate reliability sections.

### Task 8: Run live smoke and 18-report acceptance

- [ ] **Step 1: Preserve baseline and isolate outputs.**

  Use `reports/20260816_batch_my` as the read-only baseline and `docs/superpowers/fixtures/2026-08-16-data-reliability-baseline.json` as the fixed 18-ticker scope. The directory has since accumulated more reports, so never derive the acceptance denominator from its current directory count. Do not modify or reuse it. Record current commit IDs, profiles, model IDs, environment overrides, DB schema version, data timestamp, and network status. Create an isolated checkpoint/cache directory without clearing the user's normal cache:

  ```bash
  rtk mkdir -p /tmp/tradingagents-phase2-20260816
  ```

- [ ] **Step 2: Run `002413` A-share smoke.**

  ```bash
  TRADINGAGENTS_CACHE_DIR=/tmp/tradingagents-phase2-20260816/002413 rtk uv run python -m cli.main analyze --profile "SenseNova Token Plan" --tickers 002413 --workers 1 --force --output-dir reports/20260816_phase2_002413
  ```

  Verify current market date, evidence provenance, duplicate request counts, all decision nodes, and report auditor output.

- [ ] **Step 3: Run `1810.HK` smoke.**

  ```bash
  TRADINGAGENTS_CACHE_DIR=/tmp/tradingagents-phase2-20260816/1810HK rtk uv run python -m cli.main analyze --profile "SenseNova Token Plan" --tickers 1810.HK --workers 1 --force --output-dir reports/20260816_phase2_1810HK
  ```

  Verify no A-share-only calls, HKEX/local announcement coverage, source links or `valid_empty`, and no writes from `quant_agents`.

- [ ] **Step 4: Run the `my` watchlist with three workers.**

  ```bash
  TRADINGAGENTS_CACHE_DIR=/tmp/tradingagents-phase2-20260816/my rtk uv run python -m cli.main analyze --profile "SenseNova Token Plan" --watchlist my --workers 3 --force --output-dir reports/20260816_phase2_my
  ```

  Do not delete prior reports or global checkpoints outside this isolated cache. Preserve local portfolio cache if Google Sheets sync partially fails.

- [ ] **Step 5: Audit and compare.**

  ```bash
  rtk uv run python scripts/report_auditor.py reports/20260816_phase2_my --output reports/20260816_phase2_my_audit
  rtk uv run python scripts/compare_reliability.py reports/20260816_batch_my reports/20260816_phase2_my --scope-manifest docs/superpowers/fixtures/2026-08-16-data-reliability-baseline.json --max-structured-fallback 2 --max-failed-tickers 0 --require-zero-hk-ashare-calls
  ```

- [ ] **Step 6: Apply the acceptance decision.**

  Pass only if:

  - every repeated logical request resolves once and each vendor is called at most once per normalized request;
  - no market-session tool queries a weekend/non-session date;
  - HK A-share-only calls equal zero;
  - Structured fallback is at most `2/18` for the agreed ticker set;
  - new evidence tables absent still permits the legacy chain;
  - no ticker fails and report/audit outputs are complete;
  - remaining `partial/no_data/unavailable/failed` entries are listed with source, window, reason, and impact.

  If a target fails, report the observed metric and evidence. Do not label the release green based only on unit tests.
