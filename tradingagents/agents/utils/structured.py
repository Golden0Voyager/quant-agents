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
import re
from collections.abc import Callable
from typing import Any, Literal, TypeVar

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


# Canonical three-tier confidence scale used by ResearchPlan / TraderProposal /
# PortfolioDecision. Both English levels and common Chinese phrasings normalise
# to the same literals so the heuristic parser below survives multilingual
# free-text fallbacks.
_CONFIDENCE_NORMALIZE: dict[str, Literal["low", "medium", "high"]] = {
    "low": "low", "低": "low", "较低": "low", "偏低": "low",
    "medium": "medium", "中": "medium", "中等": "medium", "中性": "medium",
    "中等偏高": "medium", "中等偏低": "medium",
    "high": "high", "高": "high", "较高": "high", "偏高": "high",
}

# Matches "Confidence: X" / "**Confidence**: X" / "置信度：X" / "整体置信度较低" /
# a "| 置信度 | 低 |" table cell — tolerates markdown bold, whitespace, table
# pipes, and an EN or CN colon (or the glued Chinese 为/是/no-sep form) between
# the label and value. The captured value is restricted to a known confidence
# token so prose like "Confidence in the thesis is high" does not misparse.
_CONFIDENCE_LABEL_RE = re.compile(
    r"(?:confidence|置信度|置信水平|信心)[\s*:：\-为是|｜]*"
    r"(low|medium|high|较低|偏低|中等偏[高低]|中等|中性|较高|偏高|低|中|高)",
    re.IGNORECASE,
)


def parse_confidence(text: str) -> Literal["low", "medium", "high"] | None:
    """Heuristically extract a low/medium/high confidence level from prose text.

    Mirrors :func:`tradingagents.agents.utils.rating.parse_rating`: scan each
    line for an explicit ``Confidence: X`` / ``置信度：X`` label (tolerant of
    markdown bold and EN/CN colons), then normalise the English or Chinese
    value word to the canonical three-tier scale. The first labelled line wins.

    Returns ``None`` when no confidence word is present so callers can decide
    the default rather than silently assuming ``low``.
    """
    if not text:
        return None
    for line in text.splitlines():
        m = _CONFIDENCE_LABEL_RE.search(line)
        if m:
            raw = m.group(1).strip("*:：.,()（） 　").lower()
            level = _CONFIDENCE_NORMALIZE.get(raw)
            if level is not None:
                return level
    return None


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
            # Broad catch is intentional: provider capability gaps (e.g.
            # deepseek-reasoner raising NotImplementedError for tool_choice)
            # must degrade to free text. Programming errors still surface via
            # the full traceback in the logs.
            logger.warning(
                "%s: structured-output invocation failed (%s: %s); "
                "falling back to free text",
                agent_name, type(exc).__name__, exc,
                exc_info=True,
            )

    response = plain_llm.invoke(prompt)
    # Last-resort path: the pipeline must never block on a missing attribute.
    content = getattr(response, "content", None)
    if content is None:
        content = str(response)
    fallback = f"\n{FALLBACK_MARKER}\n{content}"
    # The free-text path bypasses the schema renderer, so the structured
    # ``**Confidence**`` line is normally lost. If the model stated a
    # confidence level anywhere in prose, re-surface it in the canonical form
    # so downstream consumers (report body, batch summary) still see it and do
    # not have to assume "low" just because the output format degraded.
    # Case-insensitive: the model may emit "**confidence**" or
    # "**CONFIDENCE**", which would otherwise get a duplicated line.
    if isinstance(content, str) and "**confidence**" not in content.lower():
        level = parse_confidence(content)
        if level is not None:
            fallback = f"{fallback}\n\n**Confidence**: {level}"
    return fallback
