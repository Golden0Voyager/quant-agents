"""Shared prefetch helpers for analyst nodes.

Analysts that inject vendor data directly into the prompt (instead of
leaving it to optional LLM tool calls) use these helpers so that a vendor
failure degrades to an explicit ``DATA_UNAVAILABLE`` block rather than
aborting the node, and so that market policy (``data_policy.is_applicable``)
short-circuits non-applicable markets with a clear placeholder.
"""

import logging
from collections.abc import Callable

from tradingagents.dataflows.data_policy import is_applicable
from tradingagents.market_context import Market

logger = logging.getLogger(__name__)


def safe_prefetch(label: str, fetch: Callable[[], str]) -> str:
    """Fetch optional analyst enrichment without aborting the analyst node."""
    try:
        result = fetch()
        if result:
            return result
        detail = "empty response"
    except Exception as exc:  # noqa: BLE001 - enrichment must degrade gracefully
        logger.warning("%s prefetch failed: %s", label, exc)
        # Include the message (truncated): for vendor failures it is the
        # root-cause detail, and the analyst prompt is a key diagnostic.
        detail = f"{type(exc).__name__}: {exc}"[:200]
    return (
        f"DATA_UNAVAILABLE: {label} could not be retrieved ({detail}). "
        "Proceed without it; do not fabricate values."
    )


def prefetch_for_market(
    method: str, market: Market, label: str, fetch: Callable[[], str]
) -> str:
    """Run a manual prefetch only when the central policy permits it."""
    if not is_applicable(method, market):
        return (
            f"DATA_NOT_APPLICABLE: {label} does not apply to market {market}. "
            "Proceed without it; do not fabricate values."
        )
    return safe_prefetch(label, fetch)
