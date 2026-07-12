# Batch Model Usage Tracking Design

## Goal

Extend the `quant_agents` batch output so users can see, per model:

- how many LLM calls were made
- how many input/output tokens were consumed

Both in the human-readable `batch_summary.md` and the machine-readable `batch_summary.json`.

## Background

The batch runner already tracks token counts and cost via `StatsCallbackHandler` (`cli/stats_handler.py`) and rolls them up in `BatchRunner._accumulate_stats()` (`cli/batch_runner.py`). The current summary footer shows total tokens and a per-model cost breakdown, but it does **not** expose:

- per-model LLM call counts
- per-model token usage

These numbers are already available inside `StatsCallbackHandler` (`llm_calls`, `tokens_by_model`) but are not propagated into the batch summary.

## Design

### 1. Extend `StatsCallbackHandler`

Add per-model call tracking alongside the existing per-model token tracking.

```python
self.llm_calls_by_model: dict[str, int] = {}
```

Increment the counter in the same places where `llm_calls` is already incremented (`on_chat_model_start` for chat models, `on_llm_start` for legacy text-completion models; LangChain invokes only one path per call, so there is no double-counting):

```python
def on_chat_model_start(self, serialized, messages, **kwargs):
    model_name = _extract_model_name(serialized) or "unknown"
    with self._lock:
        self.llm_calls += 1
        self.llm_calls_by_model[model_name] = self.llm_calls_by_model.get(model_name, 0) + 1
        self._current_model = model_name
```

`get_stats()` returns the new field:

```python
{
    "llm_calls": self.llm_calls,
    "llm_calls_by_model": dict(self.llm_calls_by_model),
    "tokens_by_model": {k: {"in": v[0], "out": v[1]} for k, v in self.tokens_by_model.items()},
    ...
}
```

### 2. Extend `BatchRunner` batch stats

In `_accumulate_stats()`, read `llm_calls_by_model` (handler key) and `tokens_by_model` from the per-ticker handler and roll them into `self.batch_stats` under the concise keys `calls_by_model` and `tokens_by_model`.

```python
self.batch_stats = {
    "tokens_in": 0,
    "tokens_out": 0,
    "llm_calls": 0,
    "cost_by_model": {},
    "calls_by_model": {},       # {model: int}
    "tokens_by_model": {},      # {model: {"in": int, "out": int}}
    "per_ticker": {
        ticker: {
            "tokens_in": int,
            "tokens_out": int,
            "llm_calls": int,
            "cost": float | None,
            "cost_by_model": dict,
            "calls_by_model": dict,
            "tokens_by_model": dict,
        }
    }
}
```

Rollup logic merges per-model dicts the same way `cost_by_model` is merged today, using `self._lock` for thread safety in concurrent batch mode.

For tickers skipped by copying an existing report, the per-ticker usage fields remain empty and do not contribute to totals.

### 3. Markdown footer rendering

Add a new `## Batch Usage` section below the existing `## Batch Cost` section in `batch_summary.md`.

Example:

```markdown
## Batch Usage

| Model | Calls | Tokens In | Tokens Out | Cost |
|-------|-------|-----------|------------|------|
| deepseek-v4-flash | 120 | 5.2k | 1.1k | $0.83 |
| deepseek-ai/DeepSeek-V4-Pro | 8 | 0.4k | 0.1k | $0.27 |
| Qwen/Qwen3.5-397B-A17B | 8 | 0.4k | 0.1k | $0.18 |
| **Total** | **128** | **5.6k↑** | **1.2k↓** | **$1.10** |
```

Also append total LLM calls to the existing `Batch Cost` bullet list:

```markdown
- **Total tokens**: 5.6k↑ 1.2k↓
- **Total LLM calls**: 128
- **Total cost**: $1.10（¥7.45）
```

Token formatting reuses the existing `1k` abbreviation logic used for the per-ticker Tokens column.

### 4. JSON output

`batch_summary.json` keeps all existing fields and adds:

```json
{
  "rows": [
    {
      "ticker": "300034",
      ...
      "llm_calls": 6,
      "calls_by_model": {"deepseek-v4-flash": 6},
      "tokens_by_model": {"deepseek-v4-flash": {"in": 360000, "out": 56000}}
    }
  ],
  "totals": {
    "tokens_in": 8270300,
    "tokens_out": 1280600,
    "llm_calls": 128,
    "cost_by_model": {...},
    "calls_by_model": {...},
    "tokens_by_model": {...},
    "total_cost_usd": 1.0972,
    "usd_to_cny_rate": 7.25,
    "total_cost_cny": 7.95
  }
}
```

All additions are additive; no existing keys are renamed or removed.

### 5. Live dashboard adapter

`_batch_stats_adapter()` currently returns zeros for `llm_calls` and `tool_calls`. Update it to return the rolled-up batch totals so the live footer remains consistent with the final summary.

### 6. Testing

- Add/update tests in `tests/cli/test_batch_runner.py` to assert:
  - `batch_summary.md` contains a `## Batch Usage` table.
  - The usage table includes `Calls`, `Tokens In`, `Tokens Out`, and `Cost` columns.
  - `batch_summary.json["totals"]` contains `llm_calls`, `calls_by_model`, and `tokens_by_model`.
  - Each successful row contains `llm_calls`, `calls_by_model`, and `tokens_by_model`.
- Add/update tests for `StatsCallbackHandler` to assert `llm_calls_by_model` is populated correctly.

### 7. Backward compatibility

- All new fields are optional/additive.
- Existing consumers of `batch_summary.json` that ignore unknown keys continue to work unchanged.
- Existing dashboard rendering that only reads `tokens_in`, `tokens_out`, and `cost_by_model` is unaffected.

## Out of Scope

- Per-ticker markdown columns for calls/tokens (user requested footer-level summary).
- Tool call counts per model (`tool_calls` remains a single total in the handler; adding per-model tool calls is not required for this feature).
- Changing the token plan quota logic or adding rate-limit alerts.

## Files to Modify

- `cli/stats_handler.py` — add `llm_calls_by_model` tracking.
- `cli/batch_runner.py` — rollup, markdown rendering, JSON output, adapter.
- `tests/cli/test_batch_runner.py` — new assertions.
- `tests/cli/test_stats_handler.py` or equivalent — new handler assertions.
