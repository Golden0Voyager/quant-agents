# Data Coverage and Explainability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve A-share data coverage by repairing existing local-data adapters, adding optional CNINFO/Tushare fallbacks, and exposing structured coverage reasons in reports without making new providers mandatory.

**Architecture:** Keep `route_to_vendor()` payload-compatible. Add a typed route diagnostic alongside the payload, use local SQLite first, and make CNINFO/Tushare providers opt-in through configuration. Batch reports aggregate diagnostics into a per-ticker coverage section; missing optional data degrades the report instead of aborting analysis.

**Tech Stack:** Python 3, pandas, SQLite, pytest, existing AkShare/Typer/Rich/LangGraph stack.

## Global Constraints

- Package installation uses `uv pip install <pkg>`; never `pip` or `python -m pip`.
- Scripts run with `uv run python <script>.py`.
- Tushare remains optional: no import or token is required for the default run.
- All file I/O uses `encoding="utf-8"`.
- Preserve the existing payload-only `route_to_vendor()` interface.
- Do not fabricate data when a provider returns no usable rows.

---

### Task 1: Repair local earnings, pledge, and snapshot adapters

**Files:**
- Modify: `tradingagents/dataflows/smartmoney_vendor.py:1196-1198,1744-1790`
- Test: `tests/test_smartmoney_vendor.py` (extend existing tests if present; otherwise create it)

**Interfaces:**
- Consumes: `_to_smartmoney_symbol()`, `_df_from_sql()`, existing SQLite schemas.
- Produces: `get_earnings_estimates(symbol, curr_date=None)` and `get_pledge_ratio(symbol)` returning the existing formatted text contract or raising `NoMarketDataError`.

- [ ] **Step 1: Write failing tests for existing-table coverage.**

  Add tests that patch `_df_from_sql` with rows matching the real schemas and assert:

  ```python
  def test_get_earnings_estimates_reads_local_forecast_rows():
      out = get_earnings_estimates("002241.SZ", curr_date="2026-08-11")
      assert "Earnings Forecast" in out
      assert "2026-06-30" in out

  def test_get_pledge_ratio_reads_stock_pledge_schema():
      out = get_pledge_ratio("002241.SZ")
      assert "Pledge Ratio" in out
      assert "质押数量" in out
  ```

  Make the SQL mock assert that `earnings_forecast` and `stock_pledge` are queried, not `stock_gpzy`.

- [ ] **Step 2: Run the focused tests and verify they fail for the intended reason.**

  Run:

  ```bash
  rtk uv run python -m pytest tests/test_smartmoney_vendor.py -k "earnings_estimates or pledge_ratio" -m unit -q
  ```

  Expected: failures showing the current hard-coded earnings exception and the nonexistent `stock_gpzy` query.

- [ ] **Step 3: Implement the smallest adapter corrections.**

  Use `earnings_forecast` columns `name`, `end_date`, `forecast_type`, `net_profit_change`, and `previous_profit`; filter by `end_date <= curr_date` when supplied and raise `NoMarketDataError` for an empty result. Use `stock_pledge` columns `stock_code`, `trade_date`, `pledger`, `pledge_amount`, `pledge_ratio`, and `pledge_org`, filtering the normalized bare code and ordering by `trade_date DESC`.

- [ ] **Step 4: Run the focused tests and the relevant smartmoney tests.**

  Run the focused command again, then:

  ```bash
  rtk uv run python -m pytest tests/test_smartmoney_vendor.py -m unit -q
  ```

  Expected: all selected tests pass and no test writes to the real `quant_core.db`.

### Task 2: Add optional CNINFO and Tushare vendor contracts

**Files:**
- Modify: `tradingagents/dataflows/errors.py`
- Modify: `tradingagents/dataflows/interface.py`
- Create: `tradingagents/dataflows/tushare_vendor.py`
- Modify: `tradingagents/dataflows/config.py`
- Modify: `tradingagents/default_config.py`
- Test: `tests/test_vendor_coverage_sources.py`

**Interfaces:**
- Consumes: vendor category configuration and `TUSHARE_TOKEN`.
- Produces: optional `cninfo` and `tushare` entries in `VENDOR_METHODS`; missing dependency/token raises `VendorNotConfiguredError`; empty results raise `NoMarketDataError`.

- [ ] **Step 1: Write failing tests for optional-provider registration and behavior.**

  Cover these cases:

  ```python
  def test_tushare_is_not_required_when_token_is_missing(monkeypatch):
      monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
      with pytest.raises(VendorNotConfiguredError):
          tushare_vendor.get_company_announcements("002241.SZ", "2026-08-01", "2026-08-11")

  def test_cninfo_announcement_rows_are_normalized(monkeypatch):
      monkeypatch.setattr(ak, "stock_zh_a_disclosure_report_cninfo", lambda **_: frame)
      out = akshare_vendor.get_company_announcements_cninfo("002241.SZ", "2026-08-01", "2026-08-11")
      assert "公告标题" in out and "公告链接" in out
  ```

  Also assert the default configuration does not include Tushare unless explicitly enabled, while an explicit `tool_vendors` chain can select it.

- [ ] **Step 2: Run the focused tests and verify the new interfaces are missing.**

  Run:

  ```bash
  rtk uv run python -m pytest tests/test_vendor_coverage_sources.py -m unit -q
  ```

- [ ] **Step 3: Implement CNINFO normalization and optional Tushare adapters.**

  Add a CNINFO implementation beside the existing AkShare announcement implementation. Normalize code/date/category/title/link and reject empty frames. In `tushare_vendor.py`, lazy-import `tushare`, read `TUSHARE_TOKEN`, call only the documented methods needed by the first release (`anns_d`, `forecast`, `margin`, `pledge_stat`/`pledge_detail`, `moneyflow`), and convert provider/config/permission errors into `VendorNotConfiguredError` or `NoMarketDataError` without importing Tushare at module import time.

- [ ] **Step 4: Register the providers without changing the default hard dependency surface.**

  Register CNINFO for `get_company_announcements` as an explicit fallback after the existing local source. Register Tushare only when configured in `tool_vendors` or a dedicated `TUSHARE_ENABLED=1` setting. Keep `data_vendors` defaults unchanged unless the provider is enabled.

- [ ] **Step 5: Run focused tests and config tests.**

  Run:

  ```bash
  rtk uv run python -m pytest tests/test_vendor_coverage_sources.py tests/test_dataflows_config.py tests/test_default_config.py -m unit -q
  ```

### Task 3: Add route diagnostics and bounded provider failure handling

**Files:**
- Modify: `tradingagents/dataflows/interface.py`
- Modify: `tradingagents/dataflows/akshare_common.py`
- Test: `tests/test_vendor_routing.py` (extend existing routing tests if present)

**Interfaces:**
- Consumes: `VendorError` subclasses and provider implementations.
- Produces: `VendorRouteResult` with payload-compatible `data`, selected `vendor`, and a diagnostic record; a per-context diagnostic collector; bounded retries for transient AkShare failures.

- [ ] **Step 1: Write failing tests for diagnostic states.**

  Assert that a successful fallback records attempted vendors, a clean empty result records `no_data`, and a network failure records `failed` without treating it as clean no-data.

- [ ] **Step 2: Run the routing tests and verify the diagnostic assertions fail.**

  Run:

  ```bash
  rtk uv run python -m pytest tests/test_vendor_routing.py -m unit -q
  ```

- [ ] **Step 3: Add a typed diagnostic dataclass and context-local collector.**

  Keep `route_to_vendor()` returning only `.data`. `route_to_vendor_with_source()` returns the enriched `VendorRouteResult`; the collector stores method, status, attempted vendors, selected source, `as_of`, and a concise reason. Use context-local state so concurrent batch workers cannot mix ticker diagnostics.

- [ ] **Step 4: Classify errors without broad semantic loss.**

  Treat `NoMarketDataError` as `no_data`, `VendorNotConfiguredError` as `unavailable`, `VendorRateLimitError` and transient transport errors as `failed` after the bounded retry/fallback path, and schema/auth/permission errors as provider-unavailable with a batch-local circuit-breaker key `(provider, method)`.

- [ ] **Step 5: Add regression tests for retry and circuit-breaker budgets.**

  Verify a transient provider is retried at most the configured limit, a clean empty response is not retried indefinitely, and a provider disabled by circuit breaker is skipped for later calls in the same context.

- [ ] **Step 6: Run routing and AkShare utility tests.**

  ```bash
  rtk uv run python -m pytest tests/test_vendor_routing.py tests/test_akshare_common.py -m unit -q
  ```

### Task 4: Surface coverage diagnostics in per-ticker reports

**Files:**
- Modify: `tradingagents/graph/propagation.py`
- Modify: `tradingagents/graph/analyst_execution.py`
- Modify: `cli/batch_runner.py`
- Modify: report rendering helper used by `cli.main.save_report_to_disk`
- Test: `tests/test_report_coverage.py`

**Interfaces:**
- Consumes: route diagnostics collected during one ticker run.
- Produces: a stable `data_coverage` state field and a Markdown “数据覆盖与限制” section in each completed report and batch summary.

- [ ] **Step 1: Write failing report tests.**

  Build a minimal final state containing available, no-data, failed, and stale entries and assert the generated Markdown includes status, source, latest date, attempted vendors, reason, and impact level.

- [ ] **Step 2: Run the report tests and verify the section is absent.**

  ```bash
  rtk uv run python -m pytest tests/test_report_coverage.py -m unit -q
  ```

- [ ] **Step 3: Add `data_coverage` to graph state with backward-compatible defaults.**

  Merge per-node diagnostics into a list keyed by logical data item; do not fail when older checkpoints lack the field.

- [ ] **Step 4: Render the coverage section and impact levels.**

  Use `低` for optional sentiment enrichment, `中` for news/governance gaps, and `高` for missing verified price/fundamental inputs. Explicitly distinguish `no_data`, `failed`, `stale`, and `unavailable`.

- [ ] **Step 5: Add batch-summary aggregation.**

  Add per-ticker counts of degraded data items and the top missing categories without changing the existing rating/entry/stop/size columns.

- [ ] **Step 6: Run focused report and batch tests.**

  ```bash
  rtk uv run python -m pytest tests/test_report_coverage.py tests/test_batch_runner.py tests/test_report_auditor_crossval.py -m unit -q
  ```

### Task 5: Full verification and live-safe acceptance checks

**Files:**
- Modify: `docs/superpowers/specs/2026-08-11-data-coverage-design.md` only if implementation decisions materially change the approved design.
- Test: all affected tests from Tasks 1-4.

- [ ] **Step 1: Run focused regression suites.**

  ```bash
  rtk uv run python -m pytest tests/test_smartmoney_vendor.py tests/test_vendor_coverage_sources.py tests/test_vendor_routing.py tests/test_report_coverage.py -m unit -q
  ```

- [ ] **Step 2: Run Ruff.**

  ```bash
  rtk uv run ruff check .
  ```

- [ ] **Step 3: Run the authoritative unit suite.**

  ```bash
  rtk uv run python -m pytest -m unit
  ```

- [ ] **Step 4: Run side-effect and diff checks.**

  Confirm no real `quant_core.db` writes occurred, no debug markers remain, no temporary files were added beyond intentional docs/tests, and:

  ```bash
  git -c core.fsmonitor=false diff --check
  git -c core.fsmonitor=false status --short
  ```

- [ ] **Step 5: Verify the default path without optional credentials.**

  Run a mocked/no-network route test with `TUSHARE_TOKEN` unset and confirm the process still returns a valid fallback or explicit coverage record rather than an import/config crash.
