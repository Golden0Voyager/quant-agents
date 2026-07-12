# Batch Model Usage Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add per-model LLM call counts and token usage to the batch summary markdown footer and JSON output.

**Architecture:** Extend `StatsCallbackHandler` to track calls per model, extend `BatchRunner._accumulate_stats` to roll those counts up, and render a new `Batch Usage` table in `batch_summary.md` plus structured `calls_by_model` / `tokens_by_model` fields in `batch_summary.json`.

**Tech Stack:** Python 3.11+, pytest, LangChain callbacks, existing `cli/batch_runner.py` and `cli/stats_handler.py`.

## Global Constraints

- All changes are additive; existing `batch_summary.json` keys must remain unchanged.
- Per-model keys in JSON use the raw model identifier returned by LangChain (e.g. `deepseek-v4-flash`).
- Thread-safety: concurrent batch mode (`workers > 1`) updates `self.batch_stats` under `self._lock`.
- Run scripts with `uv run python <script>.py` and tests with `uv run pytest` per project `AGENTS.md`.

---

### Task 1: Add per-model LLM call tracking in `StatsCallbackHandler`

**Files:**
- Modify: `cli/stats_handler.py`
- Create: `tests/cli/test_stats_handler.py`

**Interfaces:**
- Consumes: `_extract_model_name(serialized)` (already exists in `cli/stats_handler.py`).
- Produces: `StatsCallbackHandler.llm_calls_by_model: dict[str, int]` and `get_stats()["llm_calls_by_model"]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/cli/test_stats_handler.py
from unittest.mock import MagicMock

import pytest

from cli.stats_handler import StatsCallbackHandler

pytestmark = pytest.mark.unit


def _make_response(model: str, tokens_in: int, tokens_out: int):
    """Build a minimal LLMResult-like response for on_llm_end."""
    msg = MagicMock()
    msg.usage_metadata = {"input_tokens": tokens_in, "output_tokens": tokens_out}
    generation = MagicMock()
    generation.message = msg
    response = MagicMock()
    response.generations = [[generation]]
    return response


def test_llm_calls_by_model_tracks_chat_model_calls():
    handler = StatsCallbackHandler()
    serialized = {"kwargs": {"model_name": "deepseek-v4-flash"}}

    handler.on_chat_model_start(serialized, [])
    handler.on_llm_end(_make_response("deepseek-v4-flash", 100, 50))

    handler.on_chat_model_start(serialized, [])
    handler.on_llm_end(_make_response("deepseek-v4-flash", 200, 80))

    serialized2 = {"kwargs": {"model_name": "Qwen/Qwen3.5-397B-A17B"}}
    handler.on_chat_model_start(serialized2, [])
    handler.on_llm_end(_make_response("Qwen/Qwen3.5-397B-A17B", 10, 5))

    stats = handler.get_stats()
    assert stats["llm_calls"] == 3
    assert stats["llm_calls_by_model"] == {
        "deepseek-v4-flash": 2,
        "Qwen/Qwen3.5-397B-A17B": 1,
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
uv run pytest tests/cli/test_stats_handler.py::test_llm_calls_by_model_tracks_chat_model_calls -v
```

Expected: FAIL with `KeyError: 'llm_calls_by_model'` or assertion error because the dict is missing.

- [ ] **Step 3: Write minimal implementation**

In `cli/stats_handler.py`:

1. Add the new attribute in `__init__`:

```python
self.llm_calls_by_model: dict[str, int] = {}
```

2. Update `on_chat_model_start`:

```python
def on_chat_model_start(
    self,
    serialized: dict[str, Any],
    messages: list[list[Any]],
    **kwargs: Any,
) -> None:
    """Record the model name being called so on_llm_end can price it."""
    model_name = _extract_model_name(serialized)
    with self._lock:
        self.llm_calls += 1
        if model_name:
            self.llm_calls_by_model[model_name] = self.llm_calls_by_model.get(model_name, 0) + 1
        self._current_model = model_name
```

3. Update `on_llm_start` similarly for legacy text-completion paths:

```python
def on_llm_start(
    self,
    serialized: dict[str, Any],
    prompts: list[str],
    **kwargs: Any,
) -> None:
    """Counter for non-chat LLM invocations (legacy text completions)."""
    with self._lock:
        self.llm_calls += 1
        model_name = _extract_model_name(serialized)
        if model_name:
            self.llm_calls_by_model[model_name] = self.llm_calls_by_model.get(model_name, 0) + 1
        if self._current_model is None:
            self._current_model = model_name
```

4. Update `get_stats`:

```python
return {
    "llm_calls": self.llm_calls,
    "llm_calls_by_model": dict(self.llm_calls_by_model),
    "tool_calls": self.tool_calls,
    "tokens_in": self.tokens_in,
    "tokens_out": self.tokens_out,
    "cost": total_cost,
    "cost_by_model": dict(self.cost_by_model),
    "tokens_by_model": {
        k: {"in": v[0], "out": v[1]} for k, v in self.tokens_by_model.items()
    },
}
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
uv run pytest tests/cli/test_stats_handler.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add cli/stats_handler.py tests/cli/test_stats_handler.py
git commit -m "feat: track per-model LLM call counts in StatsCallbackHandler"
```

---

### Task 2: Roll up per-model calls and tokens in `BatchRunner._accumulate_stats`

**Files:**
- Modify: `cli/batch_runner.py:61-679`
- Test: `tests/cli/test_batch_runner.py`

**Interfaces:**
- Consumes: `stats_handler.get_stats()["llm_calls_by_model"]`, `stats_handler.get_stats()["tokens_by_model"]`.
- Produces: `self.batch_stats["llm_calls"]`, `self.batch_stats["calls_by_model"]`, `self.batch_stats["tokens_by_model"]`, and the same keys inside `self.batch_stats["per_ticker"][ticker]`.

- [ ] **Step 1: Update the existing test to expect the new fields**

Modify `tests/cli/test_batch_runner.py` function `_stats_handler_mock`:

```python
def _stats_handler_mock(tokens_in=1000, tokens_out=500, cost=0.01, cost_by_model=None):
    """Build a stub that quacks like StatsCallbackHandler.get_stats()."""
    handler = MagicMock()
    handler.get_stats.return_value = {
        "llm_calls": 4,
        "llm_calls_by_model": {"gpt-5.4": 3, "deepseek-v4-flash": 1},
        "tool_calls": 2,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost": cost,
        "cost_by_model": cost_by_model or {"gpt-5.4": cost},
        "tokens_by_model": {
            "gpt-5.4": {"in": tokens_in, "out": tokens_out},
        },
    }
    return handler
```

Add a new test below `test_accumulate_stats_sums_token_cost_per_model`:

```python
def test_accumulate_stats_sums_calls_and_tokens_by_model(tmp_path):
    """_accumulate_stats rolls up per-model calls and tokens."""
    runner = BatchRunner(
        tickers=["AAPL", "MSFT"],
        profile_config={"llm_provider": "openai"},
        output_dir=tmp_path / "reports",
    )
    h1 = _stats_handler_mock(tokens_in=1000, tokens_out=500, cost=0.01,
                             cost_by_model={"gpt-5.4": 0.01})
    h2 = _stats_handler_mock(tokens_in=2000, tokens_out=800, cost=0.02,
                             cost_by_model={"gpt-5.4": 0.015, "deepseek-v4-flash": 0.005})

    runner._accumulate_stats("AAPL", h1)
    runner._accumulate_stats("MSFT", h2)

    assert runner.batch_stats["llm_calls"] == 8
    assert runner.batch_stats["calls_by_model"] == {"gpt-5.4": 6, "deepseek-v4-flash": 2}
    assert runner.batch_stats["tokens_by_model"] == {
        "gpt-5.4": {"in": 3000, "out": 1300},
    }
    assert runner.batch_stats["per_ticker"]["AAPL"]["llm_calls"] == 4
    assert runner.batch_stats["per_ticker"]["AAPL"]["calls_by_model"] == {"gpt-5.4": 3, "deepseek-v4-flash": 1}
    assert runner.batch_stats["per_ticker"]["MSFT"]["calls_by_model"] == {"gpt-5.4": 3, "deepseek-v4-flash": 1}
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
uv run pytest tests/cli/test_batch_runner.py::test_accumulate_stats_sums_calls_and_tokens_by_model -v
```

Expected: FAIL with `KeyError` on `calls_by_model`.

- [ ] **Step 3: Implement the rollup in `_accumulate_stats`**

In `cli/batch_runner.py`:

1. Update the `batch_stats` initializer in `__init__`:

```python
self.batch_stats: dict = {
    "tokens_in": 0,
    "tokens_out": 0,
    "llm_calls": 0,
    "cost_by_model": {},
    "calls_by_model": {},
    "tokens_by_model": {},
    "per_ticker": {},
}
```

2. Inside `_accumulate_stats`, after the existing cost rollup, add:

```python
llm_calls = stats.get("llm_calls", 0)
calls_by_model = stats.get("llm_calls_by_model")
tokens_by_model = stats.get("tokens_by_model")
if not isinstance(llm_calls, (int, float)) or isinstance(llm_calls, bool):
    llm_calls = 0
if not isinstance(calls_by_model, dict):
    calls_by_model = {}
if not isinstance(tokens_by_model, dict):
    tokens_by_model = {}

with self._lock:
    self.batch_stats["tokens_in"] += in_tokens
    self.batch_stats["tokens_out"] += out_tokens
    self.batch_stats["llm_calls"] += llm_calls
    for model, count in calls_by_model.items():
        try:
            self.batch_stats["calls_by_model"][model] = self.batch_stats["calls_by_model"].get(model, 0) + int(count)
        except (TypeError, ValueError):
            continue
    for model, bucket in tokens_by_model.items():
        if not isinstance(bucket, dict):
            continue
        try:
            prev = self.batch_stats["tokens_by_model"].setdefault(model, {"in": 0, "out": 0})
            prev["in"] += int(bucket.get("in", 0))
            prev["out"] += int(bucket.get("out", 0))
        except (TypeError, ValueError):
            continue
    for model, cost in cost_by_model.items():
        try:
            self.batch_stats["cost_by_model"][model] = self.batch_stats["cost_by_model"].get(model, 0.0) + float(cost)
        except (TypeError, ValueError):
            continue
    self.batch_stats["per_ticker"][ticker] = {
        "tokens_in": in_tokens,
        "tokens_out": out_tokens,
        "llm_calls": llm_calls,
        "cost": stats.get("cost") if isinstance(stats.get("cost"), (int, float)) else None,
        "cost_by_model": dict(cost_by_model),
        "calls_by_model": dict(calls_by_model),
        "tokens_by_model": {k: {"in": int(v.get("in", 0)), "out": int(v.get("out", 0))}
                            for k, v in tokens_by_model.items() if isinstance(v, dict)},
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
uv run pytest tests/cli/test_batch_runner.py::test_accumulate_stats_sums_token_cost_per_model tests/cli/test_batch_runner.py::test_accumulate_stats_sums_calls_and_tokens_by_model -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add cli/batch_runner.py tests/cli/test_batch_runner.py
git commit -m "feat: roll up per-model calls and tokens in batch stats"
```

---

### Task 3: Render `Batch Usage` markdown table and JSON fields

**Files:**
- Modify: `cli/batch_runner.py:980-1092`
- Test: `tests/cli/test_batch_runner.py`

**Interfaces:**
- Consumes: `self.batch_stats["llm_calls"]`, `self.batch_stats["calls_by_model"]`, `self.batch_stats["tokens_by_model"]`, `self.batch_stats["cost_by_model"]`.
- Produces: New `## Batch Usage` markdown section and new keys in `batch_summary.json`.

- [ ] **Step 1: Update the existing test and add a new one**

Modify `tests/cli/test_batch_runner.py` function `test_generate_summary_includes_token_cost_columns` to also assert usage content:

```python
def test_generate_summary_includes_token_cost_columns(tmp_path):
    """Per-ticker Tokens/Cost columns + footer batch total in batch_summary.md."""
    runner = BatchRunner(
        tickers=["AAPL"],
        profile_config={"llm_provider": "openai"},
        output_dir=tmp_path / "reports",
    )
    runner.summaries = {
        "AAPL": {"rating": "Buy", "entry": "210", "stop": "200", "size": "5%", "company": "Apple"},
    }
    runner._accumulate_stats(
        "AAPL",
        _stats_handler_mock(tokens_in=1500, tokens_out=600, cost=0.012,
                             cost_by_model={"gpt-5.4": 0.012}),
    )

    path = runner.generate_summary()
    content = path.read_text(encoding="utf-8")

    assert "| Tokens |" in content, "Tokens column missing from header"
    assert "| Cost |" in content, "Cost column missing from header"
    assert "1.5k" in content and "600" in content, "per-ticker token numbers missing"
    assert "$0.0120" in content, "per-ticker cost missing"
    assert "## Batch Cost" in content
    assert "gpt-5.4" in content and "deepseek" not in content  # only gpt-5.4 in this run
    assert "$0.0120" in content[content.index("## Batch Cost"):]  # footer total present
    assert "## Batch Usage" in content
    assert "| Calls |" in content
    assert "| Tokens In |" in content
    assert "| Tokens Out |" in content
```

Add a new test for JSON output:

```python
def test_generate_summary_json_includes_usage_fields(tmp_path):
    """batch_summary.json totals and rows include calls/tokens by model."""
    runner = BatchRunner(
        tickers=["AAPL"],
        profile_config={"llm_provider": "openai"},
        output_dir=tmp_path / "reports",
    )
    runner.summaries = {
        "AAPL": {"rating": "Buy", "entry": "210", "stop": "200", "size": "5%", "company": "Apple"},
    }
    runner._accumulate_stats(
        "AAPL",
        _stats_handler_mock(tokens_in=1500, tokens_out=600, cost=0.012,
                             cost_by_model={"gpt-5.4": 0.012}),
    )

    runner.generate_summary()
    json_path = runner.output_dir / "batch_summary.json"
    import json
    data = json.loads(json_path.read_text(encoding="utf-8"))

    assert data["totals"]["llm_calls"] == 4
    assert data["totals"]["calls_by_model"] == {"gpt-5.4": 3, "deepseek-v4-flash": 1}
    assert data["totals"]["tokens_by_model"] == {"gpt-5.4": {"in": 1500, "out": 600}}
    row = data["rows"][0]
    assert row["llm_calls"] == 4
    assert row["calls_by_model"] == {"gpt-5.4": 3, "deepseek-v4-flash": 1}
    assert row["tokens_by_model"] == {"gpt-5.4": {"in": 1500, "out": 600}}
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
uv run pytest tests/cli/test_batch_runner.py::test_generate_summary_includes_token_cost_columns tests/cli/test_batch_runner.py::test_generate_summary_json_includes_usage_fields -v
```

Expected: FAIL — `## Batch Usage` and JSON keys missing.

- [ ] **Step 3: Implement markdown and JSON rendering**

In `cli/batch_runner.py` `generate_summary()`:

1. After the existing Batch Cost footer block, add a helper method (or inline) to render the usage table. Add a new static method near `_format_token_cost_cells`:

```python
@staticmethod
def _format_number(n: int) -> str:
    """Render an integer with k/M abbreviation."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)
```

2. In `generate_summary`, replace the Batch Cost footer block with this expanded version:

```python
# Footer with batch-wide token / cost rollup
if has_stats:
    lines.append("\n## Batch Cost\n")
    tin = self.batch_stats["tokens_in"]
    tout = self.batch_stats["tokens_out"]
    tin_str = self._format_number(tin)
    tout_str = self._format_number(tout)
    lines.append(f"- **Total tokens**: {tin_str}↑ {tout_str}↓")
    lines.append(f"- **Total LLM calls**: {self.batch_stats['llm_calls']}")
    cost_by_model = self.batch_stats["cost_by_model"]
    if cost_by_model:
        total_cost = sum(cost_by_model.values())
        cny_rate = get_usd_to_cny_rate()
        lines.append(f"- **Total cost**: ${total_cost:.4f}（¥{total_cost * cny_rate:.2f}）")
        lines.append("- **By model**:")
        for model, cost in sorted(cost_by_model.items(), key=lambda kv: -kv[1]):
            lines.append(f"  - {model}: ${cost:.4f}（¥{cost * cny_rate:.2f}）")
    else:
        lines.append("- **Total cost**: — (no priced models in this batch)")

    calls_by_model = self.batch_stats.get("calls_by_model", {})
    tokens_by_model = self.batch_stats.get("tokens_by_model", {})
    if calls_by_model or tokens_by_model:
        lines.append("\n## Batch Usage\n")
        lines.append("| Model | Calls | Tokens In | Tokens Out | Cost |")
        lines.append("|-------|-------|-----------|------------|------|")
        models = sorted(set(calls_by_model) | set(tokens_by_model) | set(cost_by_model))
        total_calls = 0
        total_tin = 0
        total_tout = 0
        total_cost = 0.0
        for model in models:
            calls = calls_by_model.get(model, 0)
            t_in = tokens_by_model.get(model, {}).get("in", 0)
            t_out = tokens_by_model.get(model, {}).get("out", 0)
            cost = cost_by_model.get(model, 0.0)
            total_calls += calls
            total_tin += t_in
            total_tout += t_out
            total_cost += cost
            lines.append(
                f"| {model} | {calls} | {self._format_number(t_in)} | "
                f"{self._format_number(t_out)} | ${cost:.4f} |"
            )
        lines.append(
            f"| **Total** | **{total_calls}** | **{self._format_number(total_tin)}↑** | "
            f"**{self._format_number(total_tout)}↓** | **${total_cost:.4f}** |"
        )
```

3. Update the JSON output to include the new fields in both `rows` and `totals`:

In the failure branch, append:

```python
json_rows.append(
    {
        "ticker": ticker,
        "company": None,
        "rating": None,
        "entry": None,
        "stop": None,
        "size": None,
        "llm_calls": None,
        "tokens_in": None,
        "tokens_out": None,
        "cost": None,
        "calls_by_model": {},
        "tokens_by_model": {},
        "cost_by_model": {},
        "status": "failed",
        "error": self.failures[ticker],
    }
)
```

In the success branch, append:

```python
json_rows.append(
    {
        "ticker": ticker,
        "company": s.get("company", ticker),
        "rating": s.get("rating"),
        "entry": s.get("entry"),
        "stop": s.get("stop"),
        "size": s.get("size"),
        "llm_calls": per_ticker_stats.get("llm_calls"),
        "tokens_in": per_ticker_stats.get("tokens_in"),
        "tokens_out": per_ticker_stats.get("tokens_out"),
        "cost": per_ticker_stats.get("cost"),
        "calls_by_model": dict(per_ticker_stats.get("calls_by_model", {})),
        "tokens_by_model": {
            k: {"in": v.get("in"), "out": v.get("out")}
            for k, v in (per_ticker_stats.get("tokens_by_model") or {}).items()
        },
        "cost_by_model": dict(per_ticker_stats.get("cost_by_model", {})),
        "status": "success",
        "error": None,
    }
)
```

And update `totals`:

```python
json_output = {
    "rows": json_rows,
    "totals": {
        "tokens_in": self.batch_stats.get("tokens_in", 0),
        "tokens_out": self.batch_stats.get("tokens_out", 0),
        "llm_calls": self.batch_stats.get("llm_calls", 0),
        "cost_by_model": dict(self.batch_stats.get("cost_by_model", {})),
        "calls_by_model": dict(self.batch_stats.get("calls_by_model", {})),
        "tokens_by_model": {
            k: {"in": v.get("in"), "out": v.get("out")}
            for k, v in self.batch_stats.get("tokens_by_model", {}).items()
        },
    },
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
uv run pytest tests/cli/test_batch_runner.py::test_generate_summary_includes_token_cost_columns tests/cli/test_batch_runner.py::test_generate_summary_json_includes_usage_fields tests/cli/test_batch_runner.py::test_generate_summary_no_stats_omits_optional_columns -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add cli/batch_runner.py tests/cli/test_batch_runner.py
git commit -m "feat: render per-model usage table in batch summary and JSON"
```

---

### Task 4: Update live dashboard adapter with running totals

**Files:**
- Modify: `cli/batch_runner.py:1096-1126`
- Test: `tests/cli/test_batch_runner.py`

**Interfaces:**
- Consumes: `self.batch_stats["llm_calls"]`, `self.batch_stats["calls_by_model"]`, `self.batch_stats["tokens_by_model"]`.
- Produces: `_batch_stats_adapter().get_stats()["llm_calls"]`, `["calls_by_model"]`, `["tokens_by_model"]`.

- [ ] **Step 1: Update the existing test**

Modify `tests/cli/test_batch_runner.py` function `test_batch_stats_adapter_exposes_running_totals` to assert the new fields:

```python
    snap = adapter.get_stats()
    assert snap["tokens_in"] == 1000
    assert snap["tokens_out"] == 400
    assert snap["llm_calls"] == 4
    assert snap["cost"] == pytest.approx(0.01)
    assert snap["cost_by_model"] == {"gpt-5.4": pytest.approx(0.01)}
    assert snap["calls_by_model"] == {"gpt-5.4": 3, "deepseek-v4-flash": 1}
    assert snap["tokens_by_model"] == {"gpt-5.4": {"in": 1000, "out": 400}}
```

And in the second snapshot:

```python
    snap2 = adapter.get_stats()
    assert snap2["tokens_in"] == 3000
    assert snap2["tokens_out"] == 1000
    assert snap2["llm_calls"] == 8
    assert snap2["cost"] == pytest.approx(0.03)
    assert snap2["cost_by_model"]["gpt-5.4"] == pytest.approx(0.01)
    assert snap2["cost_by_model"]["deepseek-v4-flash"] == pytest.approx(0.02)
    assert snap2["calls_by_model"] == {"gpt-5.4": 6, "deepseek-v4-flash": 2}
    assert snap2["tokens_by_model"] == {"gpt-5.4": {"in": 3000, "out": 1300}}
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
uv run pytest tests/cli/test_batch_runner.py::test_batch_stats_adapter_exposes_running_totals -v
```

Expected: FAIL — `llm_calls` is 0 and `calls_by_model` is empty.

- [ ] **Step 3: Implement the adapter update**

In `cli/batch_runner.py`, update `_batch_stats_adapter` inner class:

```python
class _Adapter:
    def get_stats(inner_self) -> dict:
        snapshot = outer.batch_stats
        cost_by_model = dict(snapshot.get("cost_by_model") or {})
        calls_by_model = dict(snapshot.get("calls_by_model") or {})
        tokens_by_model = {
            k: {"in": v.get("in", 0), "out": v.get("out", 0)}
            for k, v in (snapshot.get("tokens_by_model") or {}).items()
        }
        total_cost = sum(cost_by_model.values()) if cost_by_model else None
        return {
            "llm_calls": snapshot.get("llm_calls", 0),
            "tool_calls": 0,
            "tokens_in": snapshot.get("tokens_in", 0),
            "tokens_out": snapshot.get("tokens_out", 0),
            "cost": total_cost,
            "cost_by_model": cost_by_model,
            "calls_by_model": calls_by_model,
            "tokens_by_model": tokens_by_model,
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
uv run pytest tests/cli/test_batch_runner.py::test_batch_stats_adapter_exposes_running_totals -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add cli/batch_runner.py tests/cli/test_batch_runner.py
git commit -m "feat: expose per-model usage in live dashboard adapter"
```

---

### Task 5: Run full unit test suite for affected modules

- [ ] **Step 1: Run all batch runner and stats handler tests**

```bash
uv run pytest tests/cli/test_batch_runner.py tests/cli/test_stats_handler.py -m unit -v
```

Expected: All tests PASS.

- [ ] **Step 2: Run broader CLI tests to catch regressions**

```bash
uv run pytest tests/cli/ -m unit -v
```

Expected: All tests PASS.

- [ ] **Step 3: Commit (if any fixes were needed)**

If no fixes were needed, this task has no code changes and no commit is required. If any test fixes were made, commit them with:

```bash
git add <files>
git commit -m "test: fix regressions from batch usage tracking"
```

---

## Self-Review Checklist

- [ ] Spec coverage: Does every section of `docs/superpowers/specs/2026-07-12-batch-model-usage-tracking-design.md` have a corresponding task?
  - StatsCallbackHandler per-model calls → Task 1
  - BatchRunner rollup → Task 2
  - Markdown footer `Batch Usage` table → Task 3
  - JSON `calls_by_model` / `tokens_by_model` → Task 3
  - Live dashboard adapter → Task 4
  - Testing → embedded in each task
- [ ] Placeholder scan: No `TBD`, `TODO`, or vague instructions remain.
- [ ] Type consistency: `llm_calls_by_model` (handler) maps to `calls_by_model` (batch stats and JSON). `tokens_by_model` uses `{"in": int, "out": int}` throughout.
