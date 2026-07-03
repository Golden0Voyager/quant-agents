# Data Missing Prevention Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent data fabrication by adding explicit missing-data handling prompts to 5 analyst agents, a lightweight report quality validation gate, and a data quality summary injected into downstream (debate/research-manager/trader/PM) prompts.

**Architecture:** Three-layer fix: (A) Prompt patch — standardized "Missing Data Protocol" paragraph appended to the `system_message` of 5 analyst files. (B) Infrastructure — `validate_report_quality()` function in `analyst_execution.py` to tag reports as reliable/unreliable/sparse, stored in a new `data_quality_summary` field on `AgentState`. (C) Downstream injection — data quality context passed into bull/bear, research manager, trader, and PM prompts.

**Tech Stack:** Python 3.10+, LangChain/LangGraph, pytest (unittest-style), ruff linter.

## Global Constraints

- All 432 existing `unit`-marked tests must continue passing.
- No `# type: ignore[any]` suppression — fix types properly.
- No refactoring beyond scope (no file splits, no renaming, no architectural changes).
- Every code change must follow the existing pattern in its file.
- All 5 prompt patches must use the same standardized paragraph text (only the analyst-role prefix differs per file).
- Use `uv run pytest -m unit -x` for test runs.
- Run `ruff check .` before each commit.

---

## File Structure

### Files to Modify

| File | What Changes |
|------|-------------|
| `tradingagents/agents/utils/agent_states.py:79` | Add `data_quality_summary: str` field to `AgentState` |
| `tradingagents/agents/analysts/market_analyst.py:65-68` | Append Missing Data Protocol to `system_message` |
| `tradingagents/agents/analysts/news_analyst.py:37-39` | Append Missing Data Protocol to `system_message` |
| `tradingagents/agents/analysts/fundamentals_analyst.py:31-38` | Append Missing Data Protocol to `system_message` |
| `tradingagents/agents/analysts/governance_analyst.py:47-67` | Append Missing Data Protocol to `system_message` |
| `tradingagents/agents/analysts/industry_analyst.py:25-35` | Append Missing Data Protocol to `system_message` |
| `tradingagents/graph/analyst_execution.py:1-4` | Add imports for `re`, `Mapping`, `Any` |
| `tradingagents/graph/analyst_execution.py` (after line 154) | Add `validate_report_quality()` and `build_data_quality_summary()` |
| `tradingagents/agents/utils/agent_utils.py` (after line 222) | Add `get_or_build_data_quality_summary()` helper |
| `tradingagents/agents/researchers/bull_researcher.py:51` | Inject `data_quality_summary` into prompt |
| `tradingagents/agents/researchers/bear_researcher.py:53` | Inject `data_quality_summary` into prompt |
| `tradingagents/agents/managers/research_manager.py:45` | Inject `data_quality_summary` into prompt |
| `tradingagents/agents/trader/trader.py:158` | Inject `data_quality_summary` into prompt |
| `tradingagents/agents/managers/portfolio_manager.py:158` | Inject `data_quality_summary` into prompt |
| `tests/test_analyst_prompts_missing_data.py` | NEW — unit tests for prompt content |
| `tests/test_report_quality_validation.py` | NEW — unit tests for `validate_report_quality()` |

---

## Task Dependency Graph

| Task | Depends On | Reason |
|------|------------|--------|
| Task 1 | None | Starting point — add state field first |
| Task 2 | Task 1 | Prompt patches are independent of state field but establishing protocol text first makes tests self-consistent |
| Task 3 | Task 1 | Validation function needs the `data_quality_summary` state field |
| Task 4 | Task 3 | Downstream injection needs the validation logic to produce the summary |
| Task 5 | Task 2, Task 3, Task 4 | Final verification of all changes together |

---

## Parallel Execution Graph

**Wave 1 (start immediately — no dependencies):**
- Task 1: Add `data_quality_summary` field to AgentState

**Wave 2 (after Task 1 — all independent):**
- Task 2: Prompt Patch — 5 analyst files (5 sub-sub-tasks, can be parallelized)
- Task 3: Add `validate_report_quality()` to `analyst_execution.py`

**Wave 3 (after Task 3):**
- Task 4: Wire `data_quality_summary` into downstream prompts

**Wave 4 (after Tasks 1–4):**
- Task 5: Final verification

**Critical Path:** Task 1 → Task 3 → Task 4 → Task 5

---

## Tasks

### Task 1: Add `data_quality_summary` field to AgentState

**Files:**
- Modify: `tradingagents/agents/utils/agent_states.py:79`

**Interfaces:**
- Produces: `AgentState` now has `data_quality_summary: Annotated[str, "..."]` field

- [ ] **Step 1: Add the field**

Add after line 79 of `tradingagents/agents/utils/agent_states.py`:

```python
    data_quality_summary: Annotated[str, "Report of data availability and reliability per analyst"]
```

- [ ] **Step 2: Verify existing tests still pass**

Run: `uv run pytest -m unit -x`

- [ ] **Step 3: Commit**

```bash
git add tradingagents/agents/utils/agent_states.py
git commit -m "feat: add data_quality_summary field to AgentState

为 AgentState 新增 data_quality_summary 字段，用于存储各分析师报告的数据质量汇总"
```

---

### Task 2: Prompt Patch — Add Missing Data Protocol to 5 analyst files

**Files:**
- Modify: `tradingagents/agents/analysts/market_analyst.py:65-68`
- Modify: `tradingagents/agents/analysts/news_analyst.py:37-39`
- Modify: `tradingagents/agents/analysts/fundamentals_analyst.py:31-38`
- Modify: `tradingagents/agents/analysts/governance_analyst.py:47-67`
- Modify: `tradingagents/agents/analysts/industry_analyst.py:25-35`
- Reference: `tradingagents/agents/analysts/sentiment_analyst.py:168-195`
- Create: `tests/test_analyst_prompts_missing_data.py`

**Interfaces:**
- Consumes: `get_language_instruction()` — already called in each file
- Produces: Each `system_message` now has a "Missing Data Protocol" paragraph appended before `get_language_instruction()`

**Common pattern text** (same for all 5 files):

```python
            + (
                "\n\n## Missing Data Protocol\n"
                "If any tool call returns NO_DATA_AVAILABLE or an empty result, "
                "explicitly state that the data dimension is unavailable. "
                "Never fabricate numbers, infer missing values, or extrapolate "
                "from partial data. "
                "Set your confidence level to low when data is insufficient "
                "(fewer than 3 data points or empty returns). "
                "Add a data_availability marker per data dimension in your "
                "report: ✅ (data available), ⚠️ (data partial/sparse), "
                "❌ (data unavailable)."
            )
```

- [ ] **Step 1: Write the failing test**

```python
# tests/test_analyst_prompts_missing_data.py

import inspect
import pytest

from tradingagents.agents.analysts.market_analyst import create_market_analyst
from tradingagents.agents.analysts.news_analyst import create_news_analyst
from tradingagents.agents.analysts.fundamentals_analyst import create_fundamentals_analyst
from tradingagents.agents.analysts.governance_analyst import create_governance_analyst
from tradingagents.agents.analysts.industry_analyst import create_industry_analyst


MISSING_DATA_PROTOCOL_MARKERS = [
    "Missing Data Protocol",
    "NO_DATA_AVAILABLE",
    "data_availability",
    "Never fabricate",
]


@pytest.mark.unit
class TestMissingDataProtocolInPrompts:

    _ANALYST_FACTORIES = {
        "market": create_market_analyst,
        "news": create_news_analyst,
        "fundamentals": create_fundamentals_analyst,
        "governance": create_governance_analyst,
        "industry": create_industry_analyst,
    }

    def _get_source(self, factory_name: str) -> str:
        return inspect.getsource(self._ANALYST_FACTORIES[factory_name])

    def test_market_analyst_has_missing_data_protocol(self):
        source = self._get_source("market")
        for marker in MISSING_DATA_PROTOCOL_MARKERS:
            assert marker in source, f"market_analyst missing '{marker}'"

    def test_news_analyst_has_missing_data_protocol(self):
        source = self._get_source("news")
        for marker in MISSING_DATA_PROTOCOL_MARKERS:
            assert marker in source, f"news_analyst missing '{marker}'"

    def test_fundamentals_analyst_has_missing_data_protocol(self):
        source = self._get_source("fundamentals")
        for marker in MISSING_DATA_PROTOCOL_MARKERS:
            assert marker in source, f"fundamentals_analyst missing '{marker}'"

    def test_governance_analyst_has_missing_data_protocol(self):
        source = self._get_source("governance")
        for marker in MISSING_DATA_PROTOCOL_MARKERS:
            assert marker in source, f"governance_analyst missing '{marker}'"

    def test_industry_analyst_has_missing_data_protocol(self):
        source = self._get_source("industry")
        for marker in MISSING_DATA_PROTOCOL_MARKERS:
            assert marker in source, f"industry_analyst missing '{marker}'"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_analyst_prompts_missing_data.py -v -x`
Expected: 5 failures.

- [ ] **Step 3–7: Modify each of the 5 analyst files**

Each file's `system_message` gets the protocol text appended just before `get_language_instruction()`.

For **market_analyst.py** (lines 65-68), change from:
```python
            + get_language_instruction()
        )
```
to:
```python
            + (
                "\n\n## Missing Data Protocol\n"
                "If any tool call returns NO_DATA_AVAILABLE or an empty result, "
                "explicitly state that the data dimension is unavailable. "
                "Never fabricate numbers, infer missing values, or extrapolate "
                "from partial data. "
                "Set your confidence level to low when data is insufficient "
                "(fewer than 3 data points or empty returns). "
                "Add a data_availability marker per data dimension in your "
                "report: ✅ (data available), ⚠️ (data partial/sparse), "
                "❌ (data unavailable)."
            ) + get_language_instruction()
        )
```

Same pattern for the other 4 files (news_analyst.py:39, fundamentals_analyst.py:38, governance_analyst.py:67, industry_analyst.py:35).

- [ ] **Step 8: Run test to verify it passes**

Run: `uv run pytest tests/test_analyst_prompts_missing_data.py -v -x`
Expected: 5 tests PASS.

- [ ] **Step 9: Run full unit test suite**

Run: `uv run pytest -m unit -x`

- [ ] **Step 10: Commit**

```bash
git add tradingagents/agents/analysts/market_analyst.py \
       tradingagents/agents/analysts/news_analyst.py \
       tradingagents/agents/analysts/fundamentals_analyst.py \
       tradingagents/agents/analysts/governance_analyst.py \
       tradingagents/agents/analysts/industry_analyst.py \
       tests/test_analyst_prompts_missing_data.py
git commit -m "feat: add Missing Data Protocol prompt to 5 analyst agents

为 Market/News/Fundamentals/Governance/Industry 五名分析师添加标准化缺失数据处理提示"
```

---

### Task 3: Add report quality validation in analyst_execution.py

**Files:**
- Modify: `tradingagents/graph/analyst_execution.py` — add imports + 2 new functions
- Create: `tests/test_report_quality_validation.py`

**Interfaces:**
- Produces: `validate_report_quality(report_key: str, report_text: str) -> str`
- Produces: `build_data_quality_summary(state, specs) -> str`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_report_quality_validation.py

import pytest
from tradingagents.graph.analyst_execution import (
    validate_report_quality,
    build_data_quality_summary,
    ANALYST_NODE_SPECS,
)


@pytest.mark.unit
class TestValidateReportQuality:

    def test_reliable_report_marked_reliable(self):
        report = (
            "## Market Analysis\n"
            "The stock showed strong momentum with volume increasing 20%.\n"
            "| Indicator | Value |\n"
            "|-----------|-------|\n"
            "| RSI | 65 |\n"
            "| MACD | Bullish |\n"
        )
        assert validate_report_quality("market_report", report) == "reliable"

    def test_empty_report_marked_no_data(self):
        assert validate_report_quality("market_report", "") == "no_data"

    def test_report_with_no_data_sentinel_marked_no_data(self):
        report = (
            "## Market Analysis\n"
            "NO_DATA_AVAILABLE: No market data found for 'FAKE'.\n"
        )
        assert validate_report_quality("market_report", report) == "no_data"

    def test_very_short_report_marked_sparse(self):
        assert validate_report_quality("news_report", "Very little data.") == "sparse"

    def test_50_word_report_marked_reliable(self):
        report = "word " * 51
        assert validate_report_quality("sentiment_report", report) == "reliable"

    def test_49_word_report_marked_sparse(self):
        report = "word " * 49
        assert validate_report_quality("sentiment_report", report) == "sparse"


@pytest.mark.unit
class TestBuildDataQualitySummary:

    def test_summary_includes_all_analysts(self):
        state = {
            "market_report": "## Detailed market analysis\n" * 20,
            "sentiment_report": "",
            "news_report": "NO_DATA_AVAILABLE for ticker",
            "fundamentals_report": "",
            "governance_report": "Some governance data",
            "industry_report": "Industry analysis with data\n" * 10,
        }
        specs = list(ANALYST_NODE_SPECS.values())
        summary = build_data_quality_summary(state, specs)

        assert "market" in summary.lower() or "Market" in summary
        assert "sentiment" in summary.lower() or "Sentiment" in summary
        assert "news" in summary.lower()
        assert "fundamentals" in summary.lower()
        assert "governance" in summary.lower()
        assert "industry" in summary.lower()

    def test_summary_highlights_unavailable_reports(self):
        state = {
            "market_report": "",
            "sentiment_report": "",
            "news_report": "",
            "fundamentals_report": "",
            "governance_report": "",
            "industry_report": "",
        }
        specs = list(ANALYST_NODE_SPECS.values())
        summary = build_data_quality_summary(state, specs)
        assert "❌" in summary or "no_data" in summary.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_report_quality_validation.py -v -x`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement the functions**

Add to `tradingagents/graph/analyst_execution.py` (after line 154):

```python
import re
from typing import Any
from collections.abc import Mapping


_REPORT_QUALITY_RELIABLE = "reliable"
_REPORT_QUALITY_NO_DATA = "no_data"
_REPORT_QUALITY_SPARSE = "sparse"


def validate_report_quality(report_key: str, report_text: str) -> str:
    """Classify an analyst report's data quality.

    Returns one of:
    - ``"reliable"`` — report has >= 50 words and no NO_DATA_AVAILABLE sentinel.
    - ``"no_data"`` — report is empty or contains the no-data sentinel.
    - ``"sparse"`` — report exists but has fewer than 50 words.
    """
    if not report_text or not report_text.strip():
        return _REPORT_QUALITY_NO_DATA

    if "NO_DATA_AVAILABLE" in report_text:
        return _REPORT_QUALITY_NO_DATA

    word_count = len(report_text.split())
    if word_count < 50:
        return _REPORT_QUALITY_SPARSE

    return _REPORT_QUALITY_RELIABLE


def build_data_quality_summary(
    state: Mapping[str, Any],
    specs: list[AnalystNodeSpec],
) -> str:
    """Build a data quality summary block from all analyst reports in state."""
    lines: list[str] = ["## Data Quality Summary", ""]
    lines.append("| Analyst | Quality |")
    lines.append("|---------|---------|")

    unreliable_count = 0
    for spec in specs:
        report_text = state.get(spec.report_key, "")
        quality = validate_report_quality(spec.report_key, report_text)
        label = spec.agent_node
        emoji = {"reliable": "✅", "no_data": "❌", "sparse": "⚠️"}.get(quality, "❓")
        lines.append(f"| {label} | {emoji} {quality} |")
        if quality != "reliable":
            unreliable_count += 1

    lines.append("")
    if unreliable_count > 0:
        lines.append(
            f"**{unreliable_count} of {len(specs)} analyst reports have data "
            "quality issues. Treat conclusions from affected reports with caution.**"
        )
    else:
        lines.append("**All analyst reports have reliable data.**")

    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_report_quality_validation.py -v -x`
Expected: all tests PASS.

- [ ] **Step 5: Run full unit test suite**

Run: `uv run pytest -m unit -x`

- [ ] **Step 6: Commit**

```bash
git add tradingagents/graph/analyst_execution.py \
       tests/test_report_quality_validation.py
git commit -m "feat: add report quality validation in analyst_execution.py

新增 validate_report_quality() 和 build_data_quality_summary() 函数，根据报告内容判定数据质量"
```

---

### Task 4: Wire data_quality_summary into downstream prompts

**Files:**
- Modify: `tradingagents/agents/utils/agent_utils.py` — add `get_or_build_data_quality_summary()` helper
- Modify: `tradingagents/agents/researchers/bull_researcher.py` — import + inject
- Modify: `tradingagents/agents/researchers/bear_researcher.py` — import + inject
- Modify: `tradingagents/agents/managers/research_manager.py` — import + inject
- Modify: `tradingagents/agents/trader/trader.py` — import + inject
- Modify: `tradingagents/agents/managers/portfolio_manager.py` — import + inject

**Design:** Lazy computation. Each downstream agent reads `state.get("data_quality_summary")` and falls back to calling `build_data_quality_summary()` from the state if empty. This avoids any graph edge changes.

- [ ] **Step 1: Add helper to agent_utils.py**

Add after line 222 in `tradingagents/agents/utils/agent_utils.py`:

```python
def get_or_build_data_quality_summary(state: Mapping[str, Any]) -> str:
    """Return existing data_quality_summary, or build it from analyst reports in state."""
    existing = state.get("data_quality_summary")
    if isinstance(existing, str) and existing.strip():
        return existing

    from tradingagents.graph.analyst_execution import ANALYST_NODE_SPECS, build_data_quality_summary
    specs = list(ANALYST_NODE_SPECS.values())
    return build_data_quality_summary(state, specs)
```

- [ ] **Step 2–6: Inject into each downstream agent**

For each downstream agent file:
1. Import `get_or_build_data_quality_summary`
2. Call it from state
3. Insert the block into the f-string prompt

**bull_researcher.py** — add after line 18 (`fundamentals_report = ...`):
```python
        data_quality_summary = get_or_build_data_quality_summary(state)
```
Insert into prompt before `Conversation history` (after the fundamentals line):
```

Data quality summary:
{data_quality_summary}

```

**bear_researcher.py** — same pattern as bull.

**research_manager.py** — add after line 25:
```python
        data_quality_summary = get_or_build_data_quality_summary(state)
```
Insert after `{instrument_context}\n` line:
```

**Data Quality of Analyst Reports:**
{data_quality_summary}

---
```

**trader.py** — add after line 119:
```python
        data_quality_summary = get_or_build_data_quality_summary(state)
```
Insert into user content after `{snapshot_block}\n\n`:
```

---
**Data Quality of Analyst Reports:**
{data_quality_summary}

---
```

**portfolio_manager.py** — add after line 103:
```python
        data_quality_summary = get_or_build_data_quality_summary(state)
```
Insert into prompt before `**Decision Requirements:**`:
```

**Data Quality of Analyst Reports:**
{data_quality_summary}

---
```

- [ ] **Step 7: Run full unit test suite**

Run: `uv run pytest -m unit -x`

- [ ] **Step 8: Run ruff linter**

Run: `ruff check .`

- [ ] **Step 9: Commit**

```bash
git add tradingagents/agents/utils/agent_utils.py \
       tradingagents/agents/researchers/bull_researcher.py \
       tradingagents/agents/researchers/bear_researcher.py \
       tradingagents/agents/managers/research_manager.py \
       tradingagents/agents/trader/trader.py \
       tradingagents/agents/managers/portfolio_manager.py
git commit -m "feat: inject data_quality_summary into downstream agent prompts

在 Bull/Bear 辩论者、Research Manager、Trader、Portfolio Manager 提示词中注入数据质量摘要"
```

---

## Commit Strategy

| # | Message | Key Files |
|---|---------|-----------|
| 1 | `feat: add data_quality_summary field to AgentState` | `agent_states.py` |
| 2 | `feat: add Missing Data Protocol prompt to 5 analyst agents` | 5 analyst `.py` + test file |
| 3 | `feat: add report quality validation in analyst_execution.py` | `analyst_execution.py` + test file |
| 4 | `feat: inject data_quality_summary into downstream agent prompts` | 6 downstream files |

---

## Success Criteria

1. All 432 existing unit tests pass + new test files pass.
2. `ruff check .` shows zero violations.
3. Each of the 5 modified analyst `system_message` strings contains the Missing Data Protocol text.
4. `validate_report_quality()` correctly classifies reports.
5. `build_data_quality_summary()` produces a formatted table with all 6 analysts.
6. The data quality summary is visible in the prompt context of bull/bear researchers, research manager, trader, and PM.
