"""Select analyst tools from the central data-policy registry.

Registered policies are authoritative.  A tool that predates the registry is
kept available through the registry's explicit, warned ``legacy_policy_for``
compatibility path.  This is a deliberate allow-all legacy decision, not a
second capability registry; new market restrictions belong in ``data_policy``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from threading import Lock
from typing import Any

from langchain_core.tools import BaseTool

from tradingagents.dataflows.data_policy import (
    UnknownToolPolicyError,
    legacy_policy_for,
    policy_for,
)
from tradingagents.market_context import Market


class BoundToolsByMarket:
    """Bind one immutable tool context once per market and LLM instance."""

    def __init__(self, llm: Any, tools: Iterable[BaseTool]) -> None:
        self._llm = llm
        self._tools = tuple(tools)
        self._context_key = tuple((tool.name, id(tool)) for tool in self._tools)
        self._cache: dict[tuple[int, tuple[tuple[str, int], ...], Market], tuple] = {}
        self._lock = Lock()

    def get(self, market: Market) -> tuple[tuple[BaseTool, ...], Any]:
        """Return a single-flight cached ``(tools, bound_llm)`` pair."""
        key = (id(self._llm), self._context_key, market)
        with self._lock:
            cached = self._cache.get(key)
            if cached is None:
                market_tools = tuple(tools_for_market(self._tools, market))
                cached = (market_tools, self._llm.bind_tools(market_tools))
                self._cache[key] = cached
            return cached


def tools_for_market(tools: Iterable[BaseTool], market: Market) -> list[BaseTool]:
    """Return tools whose central policy allows ``market``.

    Unregistered legacy tools remain visible only through the explicit warned
    compatibility policy.  No tool-name allow/deny list is maintained here.
    """
    selected: list[BaseTool] = []
    for tool in tools:
        try:
            policy = policy_for(tool.name)
        except UnknownToolPolicyError:
            policy = legacy_policy_for(tool.name)
        if market in policy.applicable_markets:
            selected.append(tool)
    return selected


def tool_guidance_for(
    tools: Iterable[BaseTool], guidance_by_name: Mapping[str, str]
) -> str:
    """Render guidance only for tools that are actually available."""
    guidance = [
        guidance_by_name[tool.name]
        for tool in tools
        if tool.name in guidance_by_name
    ]
    if not guidance:
        return ""
    return "\n\n## Available Tool Guidance\n" + "\n".join(guidance)
