# A-Share Evidence Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an idempotent, audited evidence archive to `quant_pipeline` and populate it from public CLS and CNINFO sources without storing copyrighted article bodies or announcement PDFs.

**Architecture:** Source adapters return normalized evidence batches plus explicit coverage. A small `EvidenceRepository` owns all SQLite writes in one transaction. A registered pipeline task orchestrates bounded source calls and records complete/partial/failed windows. Migration 012 only adds tables and indexes, allowing existing readers to ignore the feature.

**Tech Stack:** Python 3.11+, SQLite WAL, AkShare/CNINFO, `levistock`, requests, dataclasses, pytest, Ruff, existing `safe_task` and `ingestion_runs` infrastructure.

## Global Constraints

- Implement in `/Users/hainingyu/Code/quant_pipeline` on a feature worktree/branch, never `main`.
- Read and obey that repository's `AGENTS.md` before execution.
- Use `rtk`; run Python through `rtk uv run python ...`.
- Obey one-file-one-commit. Commit messages are bilingual with English first and Chinese second.
- Apply migrations only through `core.migrations`; never edit the production SQLite schema manually.
- Migration 012 is additive and must be idempotent.
- Persist title, public summary/flash text, timestamps, official links, identifiers, and hashes only. Do not persist full news articles or download PDFs.
- Every network call has a timeout, bounded retry count, and batch budget.
- Empty windows are successful only when the adapter proves complete coverage.
- A failed run must not delete or supersede prior successful evidence.

---

### Task 1: Add the evidence archive migration

**Files:**
- Create: `migrations/012_evidence_archive.sql`
- Modify: `tests/test_migrations.py`

**Interfaces:**
- Produces `evidence_items`, `evidence_symbols`, and `evidence_coverage` tables from the approved design.
- Uses `ingestion_runs(run_id)` as nullable audit parent where the existing schema permits it.

- [ ] **Step 1: Add failing migration contract tests to `tests/test_migrations.py`.**

  Assert exact columns, unique constraints, foreign keys, and these indexes:

  ```sql
  CREATE INDEX idx_evidence_items_kind_time
    ON evidence_items(evidence_kind, published_at DESC);
  CREATE INDEX idx_evidence_symbols_ticker
    ON evidence_symbols(ticker, evidence_id);
  CREATE INDEX idx_evidence_coverage_scope_window
    ON evidence_coverage(source, scope_key, window_start, window_end);
  ```

  Test a second `run_migrations()` call applies zero migrations and preserves inserted rows.

- [ ] **Step 2: Run the migration tests and confirm version 012 is absent.**

  ```bash
  rtk uv run python -m pytest tests/test_migrations.py -k evidence -q
  ```

- [ ] **Step 3: Create migration 012.**

  Enforce `UNIQUE(source, source_record_key)` and `UNIQUE(evidence_id, ticker)`. Add `CHECK` constraints for evidence kind, record status, relation type, match confidence range, and coverage status. Use cascading deletes only from `evidence_items` to `evidence_symbols`; do not cascade from ingestion audit rows.

- [ ] **Step 4: Verify migration integrity.**

  ```bash
  rtk uv run python -m pytest tests/test_migrations.py -q
  rtk uv run python - <<'PY'
  import sqlite3, tempfile
  from core.migrations import run_migrations
  with tempfile.NamedTemporaryFile(suffix='.db') as f:
      run_migrations(f.name)
      with sqlite3.connect(f.name) as conn:
          assert conn.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
          assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
  PY
  ```

- [ ] **Step 5: Commit each changed file separately after tests pass.**

  ```bash
  rtk git add migrations/012_evidence_archive.sql
  rtk git commit -m "feat: add evidence archive schema / 功能：新增证据归档表结构"
  rtk git add tests/test_migrations.py
  rtk git commit -m "test: verify evidence migration / 测试：验证证据迁移"
  ```

### Task 2: Define normalized evidence contracts

**Files:**
- Create: `core/evidence.py`
- Create: `tests/test_evidence_contracts.py`

**Interfaces:**
- Produces `EvidenceItem`, `EvidenceSymbol`, `CoverageWindow`, and `EvidenceBatch` dataclasses.
- Produces deterministic `evidence_id` and `content_hash` helpers.

- [ ] **Step 1: Write failing contract tests.**

  Cover timezone normalization to ISO-8601, ticker normalization (`002413` -> `002413.SZ` when source market is mainland), stable SHA-256 hashes, enum validation, and rejection of blank title/source keys.

- [ ] **Step 2: Implement immutable contracts.**

  Required shape:

  ```python
  @dataclass(frozen=True)
  class EvidenceBatch:
      source: str
      scope_key: str
      window_start: str
      window_end: str
      status: Literal["complete", "partial", "failed"]
      items: tuple[EvidenceItem, ...]
      symbols: tuple[EvidenceSymbol, ...]
      reason: str = ""
  ```

  Derive `evidence_id` from `source + source_record_key`; derive `content_hash` from normalized title, summary, publish time, and URL. Do not use Python's process-randomized `hash()`.

- [ ] **Step 3: Run and commit one file at a time.**

  ```bash
  rtk uv run python -m pytest tests/test_evidence_contracts.py -q
  rtk git add core/evidence.py && rtk git commit -m "feat: define evidence contracts / 功能：定义证据数据契约"
  rtk git add tests/test_evidence_contracts.py && rtk git commit -m "test: cover evidence contracts / 测试：覆盖证据契约"
  ```

### Task 3: Add a transactional evidence repository

**Files:**
- Create: `core/evidence_store.py`
- Modify: `providers.py`
- Create: `tests/test_evidence_store.py`
- Modify: `tests/test_providers.py`

**Interfaces:**
- Produces `EvidenceStore.upsert_batch(batch, ingestion_run_id) -> EvidenceWriteResult`.
- Produces provider method `save_evidence_batch(batch, ingestion_run_id)`.
- Guarantees item/symbol/coverage atomicity under the existing provider write lock.

- [ ] **Step 1: Add failing idempotency and rollback tests.**

  Assert first write inserts one item/link/coverage row; same source key updates mutable fields without duplication; duplicate content with a different source key remains traceable; a symbol constraint failure rolls back item and coverage writes; an older failed window cannot overwrite a newer complete window.

- [ ] **Step 2: Implement `EvidenceStore` with an injected connection.**

  Keep SQL out of adapters. Use `INSERT ... ON CONFLICT ... DO UPDATE`, but never turn an existing `active` record into `cancelled/superseded` unless the incoming source explicitly supplies that status. Record coverage independently from row count, including complete zero-row windows.

- [ ] **Step 3: Add a narrow provider seam.**

  Under `SmartMoneyDBProvider._write_lock`, pass `_get_write_conn()` to `EvidenceStore`; commit once. Return counts for inserted/updated items, links, and coverage, not only `total_changes`.

- [ ] **Step 4: Run tests and commit each file separately.**

  ```bash
  rtk uv run python -m pytest tests/test_evidence_store.py tests/test_providers.py -q
  rtk git add core/evidence_store.py && rtk git commit -m "feat: add transactional evidence store / 功能：新增事务化证据存储"
  rtk git add providers.py && rtk git commit -m "feat: expose evidence persistence / 功能：开放证据持久化接口"
  rtk git add tests/test_evidence_store.py && rtk git commit -m "test: verify evidence persistence / 测试：验证证据持久化"
  rtk git add tests/test_providers.py && rtk git commit -m "test: cover evidence provider seam / 测试：覆盖证据提供器接口"
  ```

### Task 4: Add the bounded CLS adapter using `levistock`

**Files:**
- Modify: `pyproject.toml`
- Regenerate: `uv.lock`
- Create: `sources/cls_evidence.py`
- Create: `sources/__init__.py`
- Create: `tests/test_cls_evidence.py`

**Interfaces:**
- Consumes `levistock`'s public CLS telegraph endpoint wrapper.
- Produces `EvidenceBatch` for `market_flash` and explicitly related `company_news`.

- [ ] **Step 1: Pin and document the dependency boundary.**

  Add `levistock` as a direct dependency. In the adapter module docstring, record upstream project, MIT license, APIs used, and local responsibilities (timeout, normalization, matching, coverage, audit). Do not copy the whole project or its storage layer.

- [ ] **Step 2: Write failing adapter tests with captured fixtures.**

  Mock the upstream library and cover pagination, `all/important/company` categories, inclusive date filtering, stable source key, timeout, malformed rows, partial pagination, and complete zero-row windows. No test may access the network.

- [ ] **Step 3: Implement bounded fetch and conservative ticker matching.**

  Stop when the oldest returned item predates `window_start`, the endpoint signals end, or the configured page/request budget is exhausted. Mark budget exhaustion `partial`. Use explicit source stock codes first; company-name matching must emit `name_match` with confidence and avoid assigning sector-only flashes to an individual ticker.

- [ ] **Step 4: Run dependency and adapter checks.**

  ```bash
  rtk uv lock --check
  rtk uv run python -m pytest tests/test_cls_evidence.py -q
  ```

- [ ] **Step 5: Commit one file at a time.**

  Commit `pyproject.toml`, `uv.lock`, `sources/__init__.py`, `sources/cls_evidence.py`, and `tests/test_cls_evidence.py` separately with bilingual subjects.

### Task 5: Add the CNINFO announcement adapter

**Files:**
- Create: `sources/cninfo_evidence.py`
- Create: `tests/test_cninfo_evidence.py`

**Interfaces:**
- Consumes AkShare's CNINFO disclosure API first and a direct CNINFO request only when the wrapper cannot expose required pagination.
- Produces `announcement`/`regulatory_filing` evidence with official URLs and explicit ticker links.

- [ ] **Step 1: Write failing normalization and coverage tests.**

  Cover six-digit codes, category mapping, publish time, title, official URL, pagination, duplicate announcements, cancelled/corrected announcements, complete empty response, malformed response, and interrupted pagination.

- [ ] **Step 2: Implement source normalization and bounded pagination.**

  Prefer official announcement ID as `source_record_key`. If absent, derive a stable key from code, publish time, title, and URL. Save only source-provided public summary text; otherwise leave `summary_text` null.

- [ ] **Step 3: Run and commit.**

  ```bash
  rtk uv run python -m pytest tests/test_cninfo_evidence.py -q
  rtk git add sources/cninfo_evidence.py && rtk git commit -m "feat: ingest CNINFO evidence / 功能：采集巨潮公告证据"
  rtk git add tests/test_cninfo_evidence.py && rtk git commit -m "test: cover CNINFO evidence / 测试：覆盖巨潮公告证据"
  ```

### Task 6: Register and orchestrate the evidence ingestion task

**Files:**
- Create: `tasks/evidence.py`
- Modify: `tasks/__init__.py`
- Modify: `core/task_registry.py`
- Modify: `daily_pipeline.py`
- Create: `tests/test_evidence_task.py`
- Modify: `tests/test_task_registry.py`
- Modify: `tests/test_daily_pipeline.py`

**Interfaces:**
- Produces task `update_company_evidence(db, start_date=None, end_date=None, symbols=None, sources=None)`.
- Registers tables `evidence_items`, `evidence_symbols`, `evidence_coverage`, cadence `DAILY`, empty policy `ALLOW`, primary `cninfo`, fallback/parallel source `cls`.
- Returns an explicit `TaskResult` with source-level counts and reasons.
- Adds CLI options `--start-date YYYY-MM-DD`, `--end-date YYYY-MM-DD`, and comma-separated `--sources`; all three are forwarded only to `update_company_evidence`.

- [ ] **Step 1: Add failing task, registry, and CLI-dispatch tests.**

  Assert exact argument forwarding, source isolation, zero-row complete success, partial status propagation, no write on all-source failure, and correct `ingestion_run_id` injection through `safe_task`.

- [ ] **Step 2: Implement the orchestrator.**

  Fetch each source independently so one failure does not erase another source's complete batch. Persist each source batch transactionally. Return failed only when every requested source fails; return partial when at least one source is partial/failed and another succeeds.

- [ ] **Step 3: Register the task and dispatch arguments.**

  Add the callable to `_TASK_CALLABLES`; add a dedicated `_run_registry_task` branch to forward `symbols` and date/source arguments. Do not hide missing registration behind the default branch.

- [ ] **Step 4: Run focused tests and commit every file separately.**

  ```bash
  rtk uv run python -m pytest tests/test_evidence_task.py tests/test_task_registry.py tests/test_daily_pipeline.py -q
  ```

  Commit `tasks/evidence.py`, `tasks/__init__.py`, `core/task_registry.py`, `daily_pipeline.py`, and each test file separately with bilingual subjects.

### Task 7: Verify the pipeline slice and perform a bounded live smoke

**Files:**
- No production file changes unless a verified defect is found.

- [ ] **Step 1: Run authoritative repository checks.**

  ```bash
  rtk uv run ruff check .
  rtk uv run python -m pytest
  rtk git -c core.fsmonitor=false diff --check
  ```

- [ ] **Step 2: Back up and inspect the real DB before migration.**

  Use SQLite Backup API through the repository's established backup workflow. Confirm the target path exactly and run `PRAGMA quick_check` before continuing.

- [ ] **Step 3: Run a narrow live ingestion window.**

  ```bash
  rtk uv run python daily_pipeline.py --task update_company_evidence --symbols 002413.SZ --start-date 2026-08-10 --end-date 2026-08-16 --sources cls,cninfo
  ```

  This exact seven-day window is the bounded first smoke. Do not run an unbounded historical backfill in this phase.

- [ ] **Step 4: Verify persisted evidence and idempotency read-only.**

  Re-run the same command once. Query counts grouped by source/source key and confirm no duplicate unique keys, complete/partial coverage rows are accurate, old evidence remains after a forced adapter failure, and:

  ```sql
  PRAGMA quick_check;
  PRAGMA foreign_key_check;
  ```

- [ ] **Step 5: Record source limitations.**

  Capture date window, request counts, row counts, partial/failed sources, runtime, and whether any upstream fields lacked official ticker associations. Do not report the workstream complete without this evidence.
