import os
import threading
from datetime import datetime, timezone
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage
from langchain_core.outputs import LLMResult

from tradingagents.llm_clients.pricing import get_price_for_model


def _parse_price(env_var: str) -> float | None:
    """Parse a price from an environment variable.

    Returns None when the env var is missing or unparseable, which the
    callback handler treats as "no default rate" — call-level lookups
    against the per-model catalog still proceed.
    """
    val = os.environ.get(env_var)
    if not val:
        return None
    try:
        return float(val)
    except ValueError:
        return None


def _extract_model_name(serialized: Any) -> str | None:
    """Pull the model identifier out of a LangChain ``serialized`` dict.

    Different chat-model integrations store the model under different
    keys: OpenAI-compatible clients use ``model_name`` (the LangChain
    default), Anthropic's official integration uses ``model``. We try
    both, falling back to None when the LangChain object didn't expose
    the model in its serialized form (older LLM-style chains).
    """
    if not isinstance(serialized, dict):
        return None
    kwargs = serialized.get("kwargs") or {}
    if not isinstance(kwargs, dict):
        return None
    return kwargs.get("model_name") or kwargs.get("model")


class StatsCallbackHandler(BaseCallbackHandler):
    """Callback handler that tracks LLM calls, tool calls, and token usage.

    Cost is estimated per call by looking up the model name in the
    ``tradingagents.llm_clients.pricing`` catalog. The catalog lookup
    falls back to the ``INPUT_TOKEN_PRICE_PER_1M`` / ``OUTPUT_TOKEN_PRICE_PER_1M``
    env-var pair (the pre-catalog behavior) when the model isn't
    registered. If neither is available, cost for that call silently
    disables — token counts and call counts are still recorded.
    """

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self.llm_calls = 0
        self.tool_calls = 0
        self.tokens_in = 0
        self.tokens_out = 0
        # Env-var default rates, kept for backward compatibility: when
        # the per-model catalog doesn't know a model, we use these.
        self._default_input_price = _parse_price("INPUT_TOKEN_PRICE_PER_1M")
        self._default_output_price = _parse_price("OUTPUT_TOKEN_PRICE_PER_1M")
        # Track the model name of the most recently started chat-model
        # call. LangChain invokes on_chat_model_start right before the
        # HTTP call and on_llm_end when the response comes back, so the
        # model name is fresh in the second call.
        self._current_model: str | None = None
        # Per-model bucketing — the dashboard surfaces this so users
        # can see "OpenAI gpt-5.4 cost $X, DeepSeek deepseek-v4-flash
        # cost $Y" in mixed-provider runs.
        self.tokens_by_model: dict[str, list[int]] = {}
        self.cost_by_model: dict[str, float] = {}
        self.llm_calls_by_model: dict[str, int] = {}

    # ---- model-name capture ------------------------------------------

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[Any]],
        **kwargs: Any,
    ) -> None:
        """Record the model name being called so on_llm_end can price it."""
        with self._lock:
            self.llm_calls += 1
            model_name = _extract_model_name(serialized) or "unknown"
            self.llm_calls_by_model[model_name] = self.llm_calls_by_model.get(model_name, 0) + 1
            self._current_model = model_name

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        **kwargs: Any,
    ) -> None:
        """Counter for non-chat LLM invocations (legacy text completions)."""
        with self._lock:
            self.llm_calls += 1
            model_name = _extract_model_name(serialized) or "unknown"
            self.llm_calls_by_model[model_name] = self.llm_calls_by_model.get(model_name, 0) + 1
            # Some integrations only set the model on the legacy
            # ``on_llm_start`` path; capture it there too so we don't
            # miss the model in cost estimation.
            if self._current_model is None:
                self._current_model = model_name

    # ---- token accumulation ------------------------------------------

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        """Extract token usage from the LLM response and price it."""
        try:
            generation = response.generations[0][0]
        except (IndexError, TypeError):
            return

        usage_metadata = None
        if hasattr(generation, "message"):
            message = generation.message
            if isinstance(message, AIMessage) and hasattr(message, "usage_metadata"):
                usage_metadata = message.usage_metadata

        if not usage_metadata:
            return

        in_tokens = usage_metadata.get("input_tokens", 0) or 0
        out_tokens = usage_metadata.get("output_tokens", 0) or 0

        model_name = self._current_model or "unknown"
        call_cost = self._price_tokens(model_name, in_tokens, out_tokens)

        with self._lock:
            self.tokens_in += in_tokens
            self.tokens_out += out_tokens
            bucket = self.tokens_by_model.setdefault(model_name, [0, 0])
            bucket[0] += in_tokens
            bucket[1] += out_tokens
            if call_cost is not None:
                self.cost_by_model[model_name] = (
                    self.cost_by_model.get(model_name, 0.0) + call_cost
                )

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        **kwargs: Any,
    ) -> None:
        """Increment tool call counter when a tool starts."""
        with self._lock:
            self.tool_calls += 1

    # ---- pricing helpers --------------------------------------------

    def _price_tokens(
        self, model_name: str, in_tokens: int, out_tokens: int,
    ) -> float | None:
        """Return USD cost for one LLM call.

        Order: per-model catalog → env-var defaults → None. ``None``
        means the dashboard will omit this model's cost from the
        per-model breakdown (and from the rolled-up total) rather than
        showing a misleading $0.00.
        """
        price = get_price_for_model(model_name, at=datetime.now(timezone.utc))
        if price is None:
            if self._default_input_price is None or self._default_output_price is None:
                return None
            in_rate, out_rate = self._default_input_price, self._default_output_price
        else:
            in_rate, out_rate = price

        return in_tokens / 1_000_000 * in_rate + out_tokens / 1_000_000 * out_rate

    # ---- public read API --------------------------------------------

    def get_stats(self) -> dict[str, Any]:
        """Return current statistics including per-model cost breakdown.

        The legacy ``cost`` field is preserved (sum of all per-model
        costs) so existing dashboard rendering keeps working. New
        ``cost_by_model`` exposes the breakdown for callers that want
        to display it (e.g. a dashboard sub-line or a post-run report).
        """
        with self._lock:
            total_cost = (
                sum(self.cost_by_model.values()) if self.cost_by_model else None
            )
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
