# Data Reliability Phase 2 Execution Index Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Coordinate four independently releasable workstreams that improve market-date correctness, data coverage semantics, local evidence coverage, Hong Kong capability handling, structured-output reliability, and report explainability across `quant_agents` and `quant_pipeline`.

**Architecture:** `quant_agents` owns analysis-time policy, read-only evidence resolution, request deduplication, and reporting. `quant_pipeline` owns additive SQLite migrations, source adapters, idempotent evidence ingestion, and coverage audit. The repositories communicate only through the versioned `quant_core.db` schema and can be deployed separately.

**Tech Stack:** Python 3.10/3.11, LangGraph, Pydantic, SQLite WAL, pytest, Ruff, AkShare, `exchange_calendars`, `levistock`, existing Rich/Typer CLIs.

## Global Constraints

- Approved design: `docs/superpowers/specs/2026-08-16-data-reliability-phase2-design.md`.
- Fixed 18-report acceptance scope: `docs/superpowers/fixtures/2026-08-16-data-reliability-baseline.json`.
- Use `rtk` for repository shell commands. Run Python with `rtk uv run python ...`; never bare `python`, `pip`, or `python -m pip`.
- Implement each repository in a feature worktree/branch; do not develop on `main`.
- Keep `quant_agents` read-only against `~/Code/quant_data/quant_core.db`.
- Database changes are additive only. Do not rename, drop, or reinterpret existing tables.
- Preserve `route_to_vendor()`'s payload-only interface and old-database behavior.
- Default sources must remain free/public. Paid sources are optional and disabled by default.
- Do not persist copyrighted news articles or download announcement PDFs by default.
- Do not change analyst order, investment rating weights, or portfolio strategy.
- Keep retries bounded and record incomplete coverage instead of retrying indefinitely.
- In `quant_pipeline`, obey one-file-one-commit and bilingual commit subjects; run tests before creating each file-scoped commit.

---

## Workstream Order

### Plan 1: Agent runtime correctness

Document: `docs/superpowers/plans/2026-08-16-data-reliability-phase2-01-agent-runtime.md`

Delivers:

- centralized `DataPolicyRegistry`;
- XSHG/XHKG-aware market dates;
- richer route statuses and `VendorPayload`;
- per-ticker `RequestMemo` single-flight;
- pre-call market capability filtering;
- corrected data-reliability summaries and audit rules.

Exit gate: Sunday `2026-08-16` resolves to A-share market date `2026-08-14`; `1810.HK` makes zero A-share-only calls; duplicated logical requests invoke each vendor at most once.

### Plan 2: A-share evidence ingestion

Document: `docs/superpowers/plans/2026-08-16-data-reliability-phase2-02-evidence-ingestion.md`

Delivers:

- migration `012_evidence_archive.sql`;
- evidence contracts and SQLite repository;
- CLS and CNINFO adapters;
- idempotent daily/on-demand ingestion task;
- coverage-window and ingestion-run audit.

Exit gate: repeated ingestion does not duplicate records; confirmed empty windows are persisted; a failed refresh preserves earlier successful evidence.

### Plan 3: HKEX and cross-repository evidence resolution

Document: `docs/superpowers/plans/2026-08-16-data-reliability-phase2-03-hkex-integration.md`

Prerequisites: Plans 1 and 2 merged in their respective repositories.

Delivers:

- HKEX metadata adapter and ingestion;
- read-only `EvidenceStore` adapter in `quant_agents`;
- partial-window local/online merge;
- source-specific A-share/HK announcement chains;
- old-schema fallback behavior.

Exit gate: `1810.HK` receives HKEX announcements or `valid_empty`, never CNINFO/A-share governance fallbacks; missing evidence tables do not break the old vendor chain.

### Plan 4: Structured output and end-to-end acceptance

Document: `docs/superpowers/plans/2026-08-16-data-reliability-phase2-04-structured-acceptance.md`

Prerequisite: Plan 1 merged. Plans 2 and 3 are required only for full network acceptance.

Delivers:

- capability-driven structured method selection;
- deterministic JSON cleanup and one bounded repair call;
- explicit decision-generation diagnostics;
- separate data and decision reliability sections;
- baseline comparison tooling and live smoke protocol.

Exit gate: no node performs more than one repair call; Structured fallback is at most `2/18` in the agreed batch acceptance; report audits exclude `valid_empty`, `not_applicable`, and `ok_fallback` from missing-data counts.

---

## Integration Sequence

- [ ] **Step 1: Create isolated worktrees.**

  Use the repository's `git-feature` flow for `quant_agents`. For `quant_pipeline`, create a separate feature worktree and confirm `.worktrees` is ignored before editing.

- [ ] **Step 2: Execute Plan 1 and merge `quant_agents`.**

  This release remains compatible with old databases and immediately fixes false missing-data classifications and invalid market calls.

- [ ] **Step 3: Execute Plan 2 and merge `quant_pipeline`.**

  Apply migration 012 through the normal migration runner; do not hand-edit the production database.

- [ ] **Step 4: Execute Plan 3 in both repositories.**

  Merge the pipeline writer before enabling the agent reader in production, although the reader must tolerate absent tables.

- [ ] **Step 5: Execute Plan 4 and run the full acceptance matrix.**

  Preserve the original baseline directory and use the fixed 18-ticker manifest. Write all candidate reports to a new output directory.

- [ ] **Step 6: Publish release evidence.**

  Attach focused tests, full unit/Ruff output, migration integrity checks, smoke commands, baseline/candidate JSON comparison, residual source gaps, and workspace side effects to the PRs.

## Final Verification Matrix

| Requirement | Automated gate | Live gate |
|---|---|---|
| Market-session date | resolver unit tests | `002413` on weekend/as-of replay |
| Request deduplication | memo concurrency tests | route diagnostic `call_count` |
| HK capability filter | analyst/tool tests | `1810.HK` report |
| Evidence idempotency | migration/repository tests | rerun same ingestion window |
| Failure preservation | transaction rollback tests | forced adapter failure |
| Structured reliability | repair-chain tests | 18-ticker comparison |
| Backward compatibility | absent-table tests | agent run before pipeline deployment |

## Completion Rule

Do not call Phase 2 complete merely because unit tests pass. Completion requires all four plan exit gates, SQLite `quick_check`, no foreign-key violations, authoritative Ruff/unit suites in both repositories, and evidence from the three live smokes (`002413`, `1810.HK`, and `my` with three workers).
