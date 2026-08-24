# HKEX and Cross-Repository Evidence Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Archive HKEX announcement metadata and let `quant_agents` resolve A-share/HK company evidence from local SQLite coverage plus only the uncovered online window.

**Architecture:** `quant_pipeline` adds a source-specific HKEX adapter to the generic evidence archive from Plan 2. `quant_agents` adds a read-only `EvidenceStore` query adapter and an evidence resolver that merges local and online segments rather than stopping at the first partial source. Market policy chooses CNINFO for mainland announcements and HKEX for Hong Kong announcements.

**Tech Stack:** Python 3.10/3.11, requests/JSF session handling, SQLite read-only URI, existing vendor router, pytest, Ruff.

## Global Constraints

- Prerequisites: Plan 1 merged in `quant_agents`; Plan 2 merged in `quant_pipeline`.
- Use isolated feature worktrees in both repositories; never edit `main` directly.
- Use `rtk`; run Python with `rtk uv run python ...`.
- `quant_pipeline` obeys one-file-one-commit and bilingual commit subjects.
- HKEX implementation may reuse protocol ideas from `skxox/hkex-announcement-sync`, but copied/derived logic must document source, license, and local changes.
- Query announcement metadata and official links only; do not download PDFs.
- Keep English and Traditional Chinese titles when available without creating duplicate evidence records.
- `quant_agents` opens SQLite with `mode=ro`, `query_only=ON`, and bounded busy timeout.
- If evidence tables are absent or migration 012 is unavailable, continue through the pre-existing vendor chain.
- Never send an HK ticker to CNINFO/AkShare A-share announcement endpoints.

---

## Part A: `quant_pipeline` HKEX ingestion

### Task 1: Build a bounded HKEX metadata client

**Files (`/Users/hainingyu/Code/quant_pipeline`):**
- Create: `sources/hkex_evidence.py`
- Create: `tests/test_hkex_evidence.py`

**Interfaces:**
- Produces `fetch_hkex_evidence(tickers, start_date, end_date, *, language="both", request_budget=...) -> EvidenceBatch`.
- Resolves canonical `1810.HK` to the HKEX stock identifier and preserves official announcement URL/ID.

- [ ] **Step 1: Add captured-response tests before implementation.**

  Include fixture HTML/JSON for prefix lookup, JSF hidden fields, paginated results, English and Traditional Chinese titles, no-result page, session expiry, malformed page, and rate limit. Tests must patch every HTTP request.

- [ ] **Step 2: Verify tests fail due to the missing adapter.**

  ```bash
  rtk uv run python -m pytest tests/test_hkex_evidence.py -q
  ```

- [ ] **Step 3: Implement session, pagination, and bilingual merge.**

  Use one session per ingestion call. Resolve stock ID, GET the search page, submit the form with current JSF state, fetch paginated JSON, and merge bilingual rows by HKEX announcement ID/date rather than title. Retry at most three transient failures with bounded backoff. Mark exhausted pagination/request budget `partial`; a successfully exhausted no-result query is `complete` with zero rows.

- [ ] **Step 4: Normalize output.**

  Emit `announcement` or `regulatory_filing`, market `XHKG`, relation `explicit`, confidence `1.0`, and official HKEX source URL. Store bilingual title as one deterministic title plus the alternate title in source metadata/summary only when source-provided; never synthesize a translation.

- [ ] **Step 5: Run tests, Ruff the file, and commit one file at a time.**

  ```bash
  rtk uv run python -m pytest tests/test_hkex_evidence.py -q
  rtk uv run ruff check sources/hkex_evidence.py tests/test_hkex_evidence.py
  rtk git add sources/hkex_evidence.py && rtk git commit -m "feat: fetch HKEX announcement metadata / 功能：抓取港交所公告元数据"
  rtk git add tests/test_hkex_evidence.py && rtk git commit -m "test: cover HKEX evidence adapter / 测试：覆盖港交所证据适配器"
  ```

### Task 2: Extend the generic evidence task with HKEX

**Files (`/Users/hainingyu/Code/quant_pipeline`):**
- Modify: `tasks/evidence.py`
- Modify: `core/task_registry.py`
- Modify: `daily_pipeline.py`
- Modify: `tests/test_evidence_task.py`
- Modify: `tests/test_task_registry.py`
- Modify: `tests/test_daily_pipeline.py`

**Interfaces:**
- Adds source name `hkex` and routes only `.HK` symbols to it.
- Keeps `cninfo` mainland-only and CLS association-based.

- [ ] **Step 1: Add failing market-routing tests.**

  Assert `1810.HK` invokes HKEX once and CNINFO zero times; `002413.SZ` invokes CNINFO and HKEX zero times; mixed symbol lists split correctly; HKEX complete empty remains a successful task result.

- [ ] **Step 2: Implement source dispatch without changing storage contracts.**

  Reuse `EvidenceBatch` and `EvidenceStore`. Do not add HK-specific columns or tables.

- [ ] **Step 3: Run focused tests and commit every modified file separately.**

  ```bash
  rtk uv run python -m pytest tests/test_evidence_task.py tests/test_task_registry.py tests/test_daily_pipeline.py -q
  ```

  Use one bilingual commit per file after the suite passes.

### Task 3: Run pipeline verification and a narrow HKEX smoke

- [ ] **Step 1: Run authoritative checks.**

  ```bash
  rtk uv run ruff check .
  rtk uv run python -m pytest
  rtk git -c core.fsmonitor=false diff --check
  ```

- [ ] **Step 2: Ingest one bounded `1810.HK` window twice.**

  ```bash
  rtk uv run python daily_pipeline.py --task update_company_evidence --symbols 1810.HK --start-date 2026-08-10 --end-date 2026-08-16 --sources hkex
  ```

  Run this exact command twice. Confirm the second run does not duplicate `evidence_items` or `evidence_symbols`, and coverage reflects the exact requested window.

- [ ] **Step 3: Verify DB integrity.**

  Run `PRAGMA quick_check` and `PRAGMA foreign_key_check`; inspect only HKEX rows and official links. Record request count and runtime.

---

## Part B: `quant_agents` read-only evidence resolution

### Task 4: Add a backward-compatible read-only EvidenceStore adapter

**Files (`/Users/hainingyu/Code/quant_agents`):**
- Create: `tradingagents/dataflows/evidence_store.py`
- Modify: `tradingagents/dataflows/smartmoney_vendor.py`
- Modify: `tests/test_smartmoney_vendor.py`
- Create: `tests/test_evidence_store.py`

**Interfaces:**
- Produces `query_company_evidence(ticker, start_date, end_date, kinds) -> VendorPayload`.
- Produces `EvidenceQueryResult(items, covered_windows, missing_windows)` internally.
- Replaces the current hard failure in `smartmoney_vendor.get_news()` when evidence tables exist.

- [ ] **Step 1: Add failing query-contract tests.**

  Create temporary databases for: absent migration, fully covered rows, fully covered empty window, partial coverage, stale evidence, inactive records, low-confidence name match, and HK ticker. Assert all connections are read-only and no table is created as a side effect.

- [ ] **Step 2: Implement schema detection and read-only connection.**

  Open `f"file:{db_path}?mode=ro"` with `uri=True`; set `PRAGMA query_only=ON`. If any required table is absent, raise the existing local-data unavailable exception so the router continues to online vendors. Never call migrations from `quant_agents`.

- [ ] **Step 3: Implement query and safe rendering.**

  Select active evidence linked by explicit relation or configured name-match confidence. Include source, publish time, title, summary when legally stored, and source URL. Return `valid_empty` only when coverage proves the entire requested window complete; return `partial` with missing segments otherwise.

- [ ] **Step 4: Wire local news and announcement methods.**

  `get_news()` queries `company_news` and `market_flash`; `get_company_announcements()` queries `announcement` and `regulatory_filing`. Keep all existing methods and return-text formats usable by current tools.

- [ ] **Step 5: Run focused tests and commit.**

  ```bash
  rtk uv run python -m pytest tests/test_evidence_store.py tests/test_smartmoney_vendor.py -m unit -q
  rtk git add tradingagents/dataflows/evidence_store.py tradingagents/dataflows/smartmoney_vendor.py tests/test_evidence_store.py tests/test_smartmoney_vendor.py
  rtk git commit -m "feat: read archived company evidence"
  ```

### Task 5: Merge local coverage with only the online gap

**Files (`/Users/hainingyu/Code/quant_agents`):**
- Create: `tradingagents/dataflows/evidence_resolver.py`
- Modify: `tradingagents/dataflows/interface.py`
- Modify: `tradingagents/dataflows/data_policy.py`
- Modify: `tests/test_interface_routing.py`
- Create: `tests/test_evidence_resolver.py`

**Interfaces:**
- Produces `resolve_evidence(method, ticker, start_date, end_date, vendors) -> VendorRouteResult`.
- Merges records by `(source, source_record_key)` and cross-source `content_hash` while retaining provenance.

- [ ] **Step 1: Add failing interval and merge tests.**

  Cover full local coverage (zero online calls), no local schema (old chain unchanged), a middle covered segment with two online gaps, overlapping source records, cross-source same-content duplicates, one online gap failure, and complete local empty coverage.

- [ ] **Step 2: Implement closed-date interval arithmetic.**

  Normalize coverage to non-overlapping inclusive intervals, subtract from the requested interval, and call online adapters only for missing intervals. Combine source diagnostics; return `ok`, `ok_fallback`, `valid_empty`, or `partial` based on total window coverage—not merely whether any row exists.

- [ ] **Step 3: Route only evidence methods through the resolver.**

  `get_news` and `get_company_announcements` use merge semantics. Price, fundamentals, governance, and all other methods keep existing first-success fallback behavior.

- [ ] **Step 4: Make chains market-specific in `DataPolicyRegistry`.**

  Mainland announcements: local evidence -> CNINFO/AkShare -> optional configured source. Hong Kong announcements: local evidence -> HKEX online. Hong Kong must not receive CNINFO or A-share announcement vendors.

- [ ] **Step 5: Run focused tests and commit.**

  ```bash
  rtk uv run python -m pytest tests/test_evidence_resolver.py tests/test_interface_routing.py tests/test_data_policy.py -m unit -q
  rtk git add tradingagents/dataflows/evidence_resolver.py tradingagents/dataflows/interface.py tradingagents/dataflows/data_policy.py tests/test_evidence_resolver.py tests/test_interface_routing.py
  rtk git commit -m "feat: merge archived and live evidence windows"
  ```

### Task 6: Explain evidence provenance in reports

**Files (`/Users/hainingyu/Code/quant_agents`):**
- Modify: `tradingagents/reporting.py`
- Modify: `cli/batch_runner.py`
- Modify: `scripts/report_auditor.py`
- Modify: `tests/test_report_coverage.py`
- Modify: `tests/test_batch_runner.py`
- Modify: `tests/test_report_auditor_crossval.py`

**Interfaces:**
- Shows local/online covered ranges, missing ranges, selected sources, item count, and original links.
- Counts a partially covered evidence window once as `partial`, not once per failed provider.

- [ ] **Step 1: Add failing report fixtures for A-share and HK evidence.**

  Verify original URLs are rendered safely, duplicate route attempts aggregate, source links do not turn normal empty windows into missing data, and market-specific denominators differ correctly.

- [ ] **Step 2: Extend the shared reliability aggregation from Plan 1.**

  Do not create a second status mapping. Add evidence-window metadata to diagnostic details while keeping current report columns backward-compatible.

- [ ] **Step 3: Run focused and full verification.**

  ```bash
  rtk uv run python -m pytest tests/test_report_coverage.py tests/test_batch_runner.py tests/test_report_auditor_crossval.py -m unit -q
  rtk uv run ruff check .
  rtk uv run python -m pytest -m unit
  rtk git -c core.fsmonitor=false diff --check
  ```

- [ ] **Step 4: Commit.**

  ```bash
  rtk git add tradingagents/reporting.py cli/batch_runner.py scripts/report_auditor.py tests/test_report_coverage.py tests/test_batch_runner.py tests/test_report_auditor_crossval.py
  rtk git commit -m "feat: explain evidence window coverage"
  ```

### Task 7: Cross-repository compatibility acceptance

- [ ] **Step 1: Verify reader before writer deployment.**

  Point `QUANT_DB_PATH` to a pre-migration fixture and confirm `002413` and `1810.HK` use the legacy online chain without a schema exception.

- [ ] **Step 2: Verify reader after writer deployment.**

  Point to a migration-012 fixture and confirm full local coverage suppresses online calls, partial coverage queries only gaps, and complete empty coverage becomes `valid_empty`.

- [ ] **Step 3: Run live smokes to new output directories.**

  Run one `002413` analysis and one `1810.HK` analysis. Confirm `1810.HK` has zero CNINFO/A-share-only calls, HKEX evidence includes official links or a proven `valid_empty`, and no production DB writes originate from `quant_agents`.

- [ ] **Step 4: Record unresolved source gaps.**

  List missing dates/sources, partial windows, matching confidence, upstream errors, and request counts. Do not describe a partial window as complete coverage.
