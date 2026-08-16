"""Select analyst tools from the central data-policy registry.

Registered policies are authoritative.  A tool that predates the registry is
kept available through the registry's explicit, warned ``legacy_policy_for``
compatibility path.  This is a deliberate allow-all legacy decision, not a
second capability registry; new market restrictions belong in ``data_policy``.
"""

from __future__ import annotations

from collections.abc import Iterable

from langchain_core.tools import BaseTool

from tradingagents.dataflows.data_policy import (
    UnknownToolPolicyError,
    legacy_policy_for,
    policy_for,
)
from tradingagents.market_context import Market


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
