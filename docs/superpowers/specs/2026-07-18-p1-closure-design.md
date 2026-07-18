# P1 Closure Design

## Goal

Close the three remaining P1 correctness gaps without broad architectural
refactoring: resilient sentiment news prefetch, explicit structured-output
fallback propagation, and trustworthy fundamentals snapshot metadata.

## Scope

The change covers:

1. Exhausted news-vendor rate limits must not abort the sentiment analyst.
2. Structured-output fallback must be represented as an explicit LangGraph
   state update and remain visible in batch summaries.
3. Fundamentals snapshots must preserve signed values, distinguish errors from
   valid data, and identify the vendor and analysis date used.

It does not include general cache governance, prediction-market deletion,
readiness-panel cleanup, or the larger state/vendor refactors proposed for P2.

## Design

### 1. News rate-limit degradation

`route_to_vendor()` will retain the first `VendorRateLimitError` encountered.
If every configured vendor is exhausted and no vendor returned clean no-data,
the router will raise that specific error instead of the generic
`RuntimeError("No available vendor ...")`.

The sentiment analyst will prefetch each external input through a small private
helper. The helper returns the source text on success and a canonical
`DATA_UNAVAILABLE` block on any vendor/network exception. A failure in Yahoo
news therefore lowers the available evidence but does not prevent the node from
calling the LLM with Eastmoney data.

Unit tests for sentiment-node behavior will replace real external fetches with
deterministic fakes. A separate regression test will exercise the real router
control flow with a rate-limited fake vendor and assert that the sentiment node
still produces a report.

### 2. Explicit structured fallback propagation

Mutation of the input state inside `invoke_structured_or_freetext()` will no
longer be the propagation mechanism. The rendered fallback marker remains in
the returned text for report-level traceability and backward compatibility.

`AgentState` will gain `structured_fallback_agents`, an append-reduced list of
agent names. The initial state contains an empty list. Each structured decision
node checks its returned text for `FALLBACK_MARKER`; on fallback it returns its
own name in `structured_fallback_agents` as part of the normal LangGraph update.
This applies to the Sentiment Analyst, Research Manager, Trader, and Portfolio
Manager.

The batch runner treats a non-empty `structured_fallback_agents` list as a
fallback. It continues checking the final-decision marker for compatibility
with old checkpoints and reports. Existing `_structured_fallback` reads may be
kept temporarily for old serialized state, but new execution will not depend on
input-dict mutation.

### 3. Fundamentals snapshot correctness and provenance

Fundamental metric patterns will accept optional leading plus/minus signs.
Parsing remains sparse: unavailable metrics are omitted rather than estimated.

The routing implementation will expose an internal result carrying both the
vendor output and the selected vendor name. The public `route_to_vendor()`
interface continues returning the existing payload, so current tools and
agents remain compatible. A narrow `route_to_vendor_with_source()` interface
will be used by the fundamentals snapshot builder.

The snapshot dictionary will contain:

- `symbol`: canonical requested symbol;
- `as_of`: requested analysis date;
- `source`: actual successful vendor;
- parsed metric fields; or
- `error`: retrieval/parsing failure description.

A snapshot is available only when at least one recognized metric was parsed.
An error-only or metadata-only dictionary renders an explicit unavailable
message and is never labelled as verified data. The prompt-facing table names
the actual source and analysis date.

## Error Handling

- Rate limits retain their specific exception type through vendor routing.
- Sentiment enrichment failures become prompt-visible `DATA_UNAVAILABLE`
  blocks and are logged at warning level.
- A fundamentals vendor exception or zero parsed metrics produces an
  unavailable snapshot rather than a misleading empty verified table.
- Existing vendor callers retain their current return interface.

## Testing

Tests will follow red-green-refactor cycles for each behavior:

1. Router preserves an exhausted `VendorRateLimitError`.
2. Sentiment node survives unavailable news without real network access.
3. Each structured decision node returns its fallback agent state update.
4. Batch summary detects upstream fallback agents.
5. Fundamentals parsing preserves negative values.
6. Fundamentals snapshot records source and `as_of` metadata.
7. Error-only and metric-free snapshots render unavailable.

After targeted tests pass, run the complete unit suite and Ruff using the
project-required `uv run` commands.

## Compatibility

- Existing report markers remain readable.
- Existing checkpoints containing `_structured_fallback` remain supported by
  the batch runner.
- `route_to_vendor()` keeps its existing return type and call pattern.
- No dependency or configuration-format changes are required.
