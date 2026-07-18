"""Shared helpers for invoking an agent with structured output and a graceful fallback.

The Portfolio Manager, Trader, and Research Manager all follow the same
canonical pattern:

1. At agent creation, wrap the LLM with ``with_structured_output(Schema)``
   so the model returns a typed Pydantic instance. If the provider does
   not support structured output (rare; mostly older Ollama models), the
   wrap is skipped and the agent uses free-text generation instead.
2. At invocation, run the structured call and render the result back to
   markdown. If the structured call itself fails for any reason
   (malformed JSON from a weak model, transient provider issue), fall
   back to a plain ``llm.invoke`` so the pipeline never blocks.

Centralising the pattern here keeps the agent factories small and ensures
all three agents log the same warnings when fallback fires.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, TypeVar

from pydantic import BaseModel

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


def bind_structured(llm: Any, schema: type[T], agent_name: str) -> Any | None:
    """Return ``llm.with_structured_output(schema)`` or ``None`` if unsupported.

    A few reasoning-only providers (notably DeepSeek's deepseek-reasoner / R1)
    advertise NotImplementedError because they have no tool_choice — this is
    an *expected* fallback path, not an error, so it's logged at debug. Any
    other failure mode (AttributeError from an unwrapped client) stays at
    warning so it surfaces to the operator.
    """
    try:
        return llm.with_structured_output(schema)
    except NotImplementedError as exc:
        logger.debug(
            "%s: structured output unavailable on this model (%s); "
            "using free-text generation",
            agent_name, exc,
        )
        return None
    except AttributeError as exc:
        logger.warning(
            "%s: provider does not support with_structured_output (%s); "
            "falling back to free-text generation",
            agent_name, exc,
        )
        return None


FALLBACK_MARKER = "<!--STRUCTURED_FALLBACK: schema validation failed, treat with low confidence-->"


def invoke_structured_or_freetext(
    structured_llm: Any | None,
    plain_llm: Any,
    prompt: Any,
    render: Callable[[T], str],
    agent_name: str,
    validate: Callable[[T], tuple[T, str | None]] | None = None,
    state: dict | None = None,
) -> str:
    """Run the structured call and render to markdown; fall back to free-text on any failure.

    ``prompt`` is whatever the underlying LLM accepts (a string for chat
    invocations, a list of message dicts for chat models that take that
    shape). The same value is forwarded to the free-text path so the
    fallback sees the same input the structured call did.

    ``validate`` is an optional deterministic post-check on the parsed
    result. It returns the (possibly corrected) result plus an optional
    markdown note that is appended to the rendered output. It only runs on
    the structured path; the free-text fallback has no typed fields to check.

    ``state`` is retained as a deprecated compatibility parameter. Fallback
    propagation is handled by graph nodes returning explicit state updates.
    The returned free-text content is prefixed with an HTML-style marker.
    """
    if structured_llm is not None:
        try:
            result = structured_llm.invoke(prompt)
            if result is None:
                # A thinking model can answer in plain text instead of calling
                # the tool, leaving the parser with nothing to return. Treat it
                # as a structured miss and fall back, with a clear reason.
                raise ValueError("structured output returned no parsed result")
            note = None
            if validate is not None:
                result, note = validate(result)
            rendered = render(result)
            return f"{rendered}\n\n{note}" if note else rendered
        except Exception as exc:
            logger.warning(
                "%s: structured-output invocation failed (%s); retrying once as free text",
                agent_name, exc,
            )

    response = plain_llm.invoke(prompt)
    return f"\n{FALLBACK_MARKER}\n{response.content}"
